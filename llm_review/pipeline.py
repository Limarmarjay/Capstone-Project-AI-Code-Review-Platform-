"""Core LLM review pipeline (Issue #3).

Flow (matches your flowchart section):
    Run LLM Review
        -> Valid LLM response?  --No-->  static-only fallback
        -> Combine Findings (LLM + Static)
        -> Assign Severity Levels
        -> Generate Summary (score + text)

The whole thing NEVER dead-ends on an LLM failure: if the model times out
or returns garbage, llm_status is set accordingly and findings are built
from static results alone.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import logging
import math
import os
import re
import sys
import time
import uuid

from .client import LLMClient, LLMError, LLMPermanentError, MockLLMClient
from .prompt import SYSTEM_PROMPT, build_user_prompt
from .severity import assign_severity, score
from .types import (
    AnalysisInput,
    Category,
    Finding,
    LLMStatus,
    ReviewFile,
    ReviewOutput,
    Severity,
    Source,
    StaticFinding,
)

log = logging.getLogger(__name__)

MAX_LLM_ATTEMPTS = 2  # retries a transient failure once before falling back

DEFAULT_LLM_REVIEW_BUDGET_SECONDS = 300.0  # same number as OllamaLLMClient's
# own per-call default (client.py) -- a value the team already accepted as
# "how long is reasonable to wait for a local Ollama call," reused here so
# a 1-file diff's worst case stays in the same order of magnitude it was
# before (previously up to MAX_LLM_ATTEMPTS x that value per file; now
# capped at this total regardless of file count) while an N-file diff's
# worst case becomes flat at this number instead of scaling with N.


def _default_llm_review_budget() -> float:
    """Reads $LLM_REVIEW_BUDGET_SECONDS; falls back to
    DEFAULT_LLM_REVIEW_BUDGET_SECONDS (with a stderr warning) if unset,
    blank, not a valid number, or not a positive finite one (zero,
    negative, inf, or nan would otherwise make the budget either
    meaningless or never-exhausted)."""
    raw = os.environ.get("LLM_REVIEW_BUDGET_SECONDS")
    if not raw:
        return DEFAULT_LLM_REVIEW_BUDGET_SECONDS

    try:
        value = float(raw)
    except ValueError:
        value = None

    if value is None or not math.isfinite(value) or value <= 0:
        print(
            f"Warning: LLM_REVIEW_BUDGET_SECONDS={raw!r} is not a valid "
            f"positive, finite number; using default "
            f"{DEFAULT_LLM_REVIEW_BUDGET_SECONDS}.",
            file=sys.stderr,
        )
        return DEFAULT_LLM_REVIEW_BUDGET_SECONDS
    return value


def _accepts_timeout_kwarg(client: LLMClient) -> bool:
    """True iff client.complete() will accept a `timeout=` keyword --
    declared explicitly, or via **kwargs. A wrapper whose complete() only
    takes (system, user) -- SanitizingClient, eval/run_eval.py's
    _ConnectionAwareOllamaClient, any test double shaped like either --
    is still a perfectly valid LLMClient; it's just called without the
    kwarg, with the budget's skip-ahead-of-time behavior (in _run_llm,
    below) still fully in effect even though that wrapper can't propagate
    a tighter per-call timeout to whatever it wraps.

    Checked via signature introspection, once per call, rather than a
    speculative call-and-catch-TypeError: a call that already happened
    can have side effects (a real network request), so discovering
    "unsupported" only after calling would risk a double call, or
    misattributing some unrelated internal TypeError to this instead."""
    try:
        params = inspect.signature(client.complete).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        p.name == "timeout" or p.kind is inspect.Parameter.VAR_KEYWORD
        for p in params
    )


def review(
    data: dict,
    *,
    client: LLMClient | None = None,
    budget_seconds: float | None = None,
) -> dict:
    """Entry point. Takes the analysis-input dict from #2, returns the
    review-output dict for #4. `client` defaults to the offline mock.

    `budget_seconds` caps the total wall-clock time spent on LLM calls
    across every file in this diff (default: $LLM_REVIEW_BUDGET_SECONDS,
    or DEFAULT_LLM_REVIEW_BUDGET_SECONDS if that's unset). Once it's
    gone, remaining files skip the LLM entirely and keep only their
    static findings. The overall llm_status reports PARTIAL -- so the
    summary reflects a partial, not a full, AI review -- only if at
    least one file still got a real LLM pass; if the budget ran out
    without a single success, it's reported as TIMEOUT instead, since a
    review where nothing succeeded isn't meaningfully "partial."

    Worst case when `client` DOES accept a `timeout=` kwarg: roughly
    `budget_seconds` total, since every in-flight call is itself capped
    to whatever's left of the budget. Worst case when it doesn't (e.g.
    wrapped in something that only implements complete(system, user),
    such as SanitizingClient) is `budget_seconds` PLUS one call's own
    configured timeout: the budget still stops any NEW attempt from
    starting once it's gone, but a call already in flight when the
    deadline passes can still run for up to that client's own timeout,
    since nothing can cap it from outside without its cooperation.
    """
    client = client or MockLLMClient()
    inp = AnalysisInput.from_dict(data)

    # Safety assertion: #2 promises redaction happened before we get here.
    if not inp.redaction_applied:
        # Don't hard-crash; degrade to static-only so we never ship code to
        # the LLM that might still contain secrets.
        return _static_only(inp, LLMStatus.SKIPPED,
                            note="redaction not confirmed; LLM skipped").to_dict()

    # #2's redaction covers the diff/patch text itself, but static tools
    # (e.g. bandit) can still echo a raw secret value into their own
    # finding message. Redact that -- and the hunk diff text again, as a
    # second layer -- once per file, and use that same redacted copy for
    # everything downstream (the LLM prompt AND the findings that end up
    # in review_output), so no unredacted static message can reach either.
    try:
        from Static_checks_and_sec_rules.services.redactor import redact_text
    except ImportError:
        log.error(
            "Could not import redact_text from "
            "Static_checks_and_sec_rules.services.redactor; refusing to "
            "send static finding messages or diff text to the LLM "
            "unredacted. Falling back to static-only."
        )
        return _static_only(
            inp, LLMStatus.SKIPPED,
            note="redaction module unavailable; static finding messages "
                 "could not be redacted; LLM skipped",
        ).to_dict()

    if budget_seconds is None:
        budget_seconds = _default_llm_review_budget()
    deadline = time.monotonic() + budget_seconds

    all_findings: list[Finding] = []
    llm_status = LLMStatus.SUCCESS
    any_success = False

    for file in inp.files:
        redacted_file = _redacted_copy(file, redact_text)
        llm_raw, status = _run_llm(redacted_file, client, deadline=deadline)
        if status is not LLMStatus.SUCCESS:
            # First failure downgrades the whole run's status; later
            # failures on other files don't overwrite it, so the reported
            # status reflects what went wrong first, not what went wrong
            # last.
            if llm_status is LLMStatus.SUCCESS:
                llm_status = status
            all_findings.extend(_static_findings_only(redacted_file))
            continue
        any_success = True
        all_findings.extend(_combine(redacted_file, llm_raw))

    if llm_status is LLMStatus.PARTIAL and not any_success:
        # The budget ran out, but nothing ever succeeded -- that's not a
        # partial AI review (some files reviewed, some not), it's the
        # LLM simply being unavailable for this whole diff. Report it
        # the same way a plain timeout would be, rather than a
        # more-specific-sounding status that overstates what happened.
        llm_status = LLMStatus.TIMEOUT

    output = ReviewOutput(
        submission_id=inp.submission_id,
        review_status="completed",
        llm_status=llm_status,
        overall_score=score(all_findings),
        summary=_summarize(all_findings, llm_status),
        findings=all_findings,
    )
    return output.to_dict()


# ---- LLM step -----------------------------------------------------------

def _redacted_copy(file: ReviewFile, redact) -> ReviewFile:
    """Return a copy of `file` with static-finding messages and hunk diff
    text passed through `redact`, so the LLM prompt never sees a raw
    secret value a static tool happened to quote. The original `file` is
    left untouched -- it's still used for combining/output afterward."""
    redacted_findings = [
        dataclasses.replace(sf, message=redact(sf.message))
        for sf in file.static_findings
    ]
    redacted_hunks = [
        dataclasses.replace(h, diff=redact(h.diff))
        for h in file.hunks
    ]
    return dataclasses.replace(file, hunks=redacted_hunks, static_findings=redacted_findings)


def _run_llm(
    file: ReviewFile,
    client: LLMClient,
    *,
    deadline: float,
    max_attempts: int = MAX_LLM_ATTEMPTS,
) -> tuple[list[dict], LLMStatus]:
    """Call the LLM and parse its JSON. Returns (parsed_findings, status).

    Retries up to max_attempts on a transient failure (LLMTimeout, or any
    other non-permanent LLMError, or a response that fails JSON parsing) --
    any of those can be a one-off fluke. Never retries LLMPermanentError:
    a bad API key or an unsupported request won't succeed on a second try,
    so retrying it would only waste latency.

    `deadline` is a time.monotonic() timestamp shared across every file
    in this review() call -- review()'s total time budget. If it's
    already passed before this file's first attempt, no call is made at
    all and this returns PARTIAL immediately. Every attempt that does
    happen passes `timeout=` (the time left before deadline) to
    complete() -- but only if the client actually accepts that kwarg;
    see _accepts_timeout_kwarg.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return [], LLMStatus.PARTIAL

    user = build_user_prompt(file)
    supports_timeout = _accepts_timeout_kwarg(client)
    status = LLMStatus.TIMEOUT
    for _attempt in range(max_attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return [], LLMStatus.PARTIAL
        kwargs = {"timeout": remaining} if supports_timeout else {}
        try:
            raw = client.complete(SYSTEM_PROMPT, user, **kwargs)
        except LLMPermanentError as e:
            log.warning("LLM call failed permanently: %s: %s", type(e).__name__, e)
            return [], LLMStatus.INVALID_RESPONSE
        except LLMError as e:
            log.warning("LLM call failed transiently: %s: %s", type(e).__name__, e)
            status = LLMStatus.TIMEOUT
            continue

        parsed = _parse_json(raw)
        if parsed is None:
            status = LLMStatus.INVALID_RESPONSE
            continue

        return parsed.get("findings", []), LLMStatus.SUCCESS

    return [], status


def _parse_json(raw: str) -> dict | None:
    """Defensively parse model output into a dict, or None if unusable.
    Strips markdown fences the model sometimes adds despite instructions."""
    if not raw or not raw.strip():
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # Last resort: grab the first {...} block.
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(obj, dict) or "findings" not in obj:
        return None
    findings = obj.get("findings")
    if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
        return None
    return obj


# ---- Combine step -------------------------------------------------------

def _combine(file: ReviewFile, llm_findings: list[dict]) -> list[Finding]:
    """Merge LLM findings with static findings for one file.

    - If an LLM finding references a static id OR lands on the same line as a
      static finding, it's COMBINED (and severity may escalate).
    - LLM-only findings are SOURCE=llm.
    - Static findings the LLM didn't touch are still emitted as SOURCE=static,
      so nothing the tools found gets dropped.
    """
    results: list[Finding] = []
    static_by_line: dict[int, StaticFinding] = {sf.line: sf for sf in file.static_findings}
    consumed_static: set[str] = set()

    for lf in llm_findings:
        category = _coerce_category(lf.get("category"))
        line = int(lf.get("line") or 0)
        related_id = lf.get("relates_to_static_finding_id")

        matched: StaticFinding | None = None
        if related_id:
            matched = next(
                (sf for sf in file.static_findings if sf.finding_id == related_id), None
            )
        if matched is None and line in static_by_line:
            candidate = static_by_line[line]

            if _coerce_category(candidate.category) == category:
                matched = candidate

        confirmed = matched is not None
        if matched:
            consumed_static.add(matched.finding_id)

        results.append(Finding(
            finding_id=_new_id(),
            source=Source.COMBINED if confirmed else Source.LLM,
            category=category,
            severity=assign_severity(
                category,
                confirmed_by_both=confirmed,
                base_severity=_static_baseline(matched) if matched else None,
            ),
            file_path=file.file_path,
            line=line or (matched.line if matched else 0),
            title=str(lf.get("title", "")).strip() or "Issue",
            explanation=str(lf.get("explanation", "")).strip(),
            suggested_fix=str(lf.get("suggested_fix", "")).strip(),
            static_finding_ids=[matched.finding_id] if matched else [],
        ))

    # Emit any static findings the LLM never addressed.
    for sf in file.static_findings:
        if sf.finding_id in consumed_static:
            continue
        results.append(_static_to_finding(file, sf))

    return results


def _static_findings_only(file: ReviewFile) -> list[Finding]:
    """Fallback path for one file: findings from static analysis alone."""
    return [_static_to_finding(file, sf) for sf in file.static_findings]


def _static_to_finding(file: ReviewFile, sf: StaticFinding) -> Finding:
    category = _coerce_category(sf.category)
    return Finding(
        finding_id=_new_id(),
        source=Source.STATIC,
        category=category,
        severity=assign_severity(
            category,
            base_severity=_static_baseline(sf),
        ),
        file_path=file.file_path,
        line=sf.line,
        title=f"{sf.tool}: {sf.rule_id}".strip(": "),
        explanation=sf.message,
        suggested_fix="",  # static tools don't always suggest fixes
        static_finding_ids=[sf.finding_id],
    )


# ---- Helpers ------------------------------------------------------------

def _static_only(inp: AnalysisInput, status: LLMStatus, *, note: str) -> ReviewOutput:
    findings: list[Finding] = []
    for file in inp.files:
        findings.extend(_static_findings_only(file))
    return ReviewOutput(
        submission_id=inp.submission_id,
        review_status="completed",
        llm_status=status,
        overall_score=score(findings),
        summary=f"Static-only review ({note}). " + _summarize(findings, status),
        findings=findings,
    )


_STATIC_SEVERITY_ALIASES: dict[str, Severity] = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
    # repo_diff_pipeline's info/warning/error/critical scale
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "info": Severity.LOW,
}


def _static_baseline(sf: StaticFinding) -> Severity | None:
    if not sf.severity:
        return None

    mapped = _STATIC_SEVERITY_ALIASES.get(sf.severity.strip().lower())
    if mapped is None:
        log.warning(
            "Unrecognized static finding severity %r for finding_id=%s; "
            "falling back to the category default.",
            sf.severity, sf.finding_id,
        )
        return None
    return mapped


def _coerce_category(value) -> Category:
    try:
        return Category(str(value))
    except (ValueError, TypeError):
        return Category.CODE_SMELL


def _summarize(findings: list[Finding], status: LLMStatus) -> str:
    if not findings:
        return "No issues found in the changed code."
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
    order = ["critical", "high", "medium", "low"]
    bits = [f"{counts[s]} {s}" for s in order if s in counts]
    if status is LLMStatus.SUCCESS:
        prefix = ""
    elif status is LLMStatus.PARTIAL:
        prefix = "[Partial AI review — time budget ran out before every file was reviewed] "
    else:
        prefix = "[LLM unavailable — static only] "
    return f"{prefix}{len(findings)} issue(s): " + ", ".join(bits) + "."


def _new_id() -> str:
    return "f_" + uuid.uuid4().hex[:8]
