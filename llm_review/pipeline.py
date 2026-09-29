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

import json
import logging
import re
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


def review(data: dict, *, client: LLMClient | None = None) -> dict:
    """Entry point. Takes the analysis-input dict from #2, returns the
    review-output dict for #4. `client` defaults to the offline mock."""
    client = client or MockLLMClient()
    inp = AnalysisInput.from_dict(data)

    # Safety assertion: #2 promises redaction happened before we get here.
    if not inp.redaction_applied:
        # Don't hard-crash; degrade to static-only so we never ship code to
        # the LLM that might still contain secrets.
        return _static_only(inp, LLMStatus.SKIPPED,
                            note="redaction not confirmed; LLM skipped").to_dict()

    all_findings: list[Finding] = []
    llm_status = LLMStatus.SUCCESS

    for file in inp.files:
        llm_raw, status = _run_llm(file, client)
        if status is not LLMStatus.SUCCESS:
            # First failure downgrades the whole run's status; later
            # failures on other files don't overwrite it, so the reported
            # status reflects what went wrong first, not what went wrong
            # last.
            if llm_status is LLMStatus.SUCCESS:
                llm_status = status
            all_findings.extend(_static_findings_only(file))
            continue
        all_findings.extend(_combine(file, llm_raw))

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

def _run_llm(
    file: ReviewFile, client: LLMClient, *, max_attempts: int = MAX_LLM_ATTEMPTS
) -> tuple[list[dict], LLMStatus]:
    """Call the LLM and parse its JSON. Returns (parsed_findings, status).

    Retries up to max_attempts on a transient failure (LLMTimeout, or any
    other non-permanent LLMError, or a response that fails JSON parsing) --
    any of those can be a one-off fluke. Never retries LLMPermanentError:
    a bad API key or an unsupported request won't succeed on a second try,
    so retrying it would only waste latency.
    """
    user = build_user_prompt(file)
    status = LLMStatus.TIMEOUT
    for _attempt in range(max_attempts):
        try:
            raw = client.complete(SYSTEM_PROMPT, user)
        except LLMPermanentError:
            return [], LLMStatus.INVALID_RESPONSE
        except LLMError:
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
    prefix = "" if status is LLMStatus.SUCCESS else "[LLM unavailable — static only] "
    return f"{prefix}{len(findings)} issue(s): " + ", ".join(bits) + "."


def _new_id() -> str:
    return "f_" + uuid.uuid4().hex[:8]
