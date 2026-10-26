"""Prompt construction for the LLM review step.

The prompt asks the model to review *changed Python code*, given the diff
and what static analysis already found, and to return STRICT JSON so the
response is machine-parseable. Structured-output-by-instruction: we tell
the model to emit only JSON, then parse it defensively downstream.
"""
from __future__ import annotations

import logging

from .types import Hunk, ReviewFile

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior Python code reviewer performing an \
automated first-pass review of a code change (a diff), before a human \
reviewer sees it.

You are given:
- The diff for one file (added/removed/context lines). Each line that \
exists in the new file is prefixed with its line number in the left \
column before the "|"; removed lines have no number there. The "line" \
field you report must be the number shown in that left column.
- A list of issues that static analysis tools (Pylint, mypy, security \
scanners) already found on this change.

Your job:
1. If there are static findings, explain them in plain language a junior \
developer can act on.
2. If the diff introduces defects the static tools missed -- bugs, \
security or privacy risks, missing tests, incorrect API usage, \
performance problems, or a maintainability issue with a concrete, \
demonstrable negative consequence for whoever runs or maintains this \
code -- report them.
3. For each finding you report, give a concrete suggested fix.
4. If the diff introduces no defects, return {"findings": []}.

Report defects only -- something that is actually wrong, risky, or will \
cause a real problem. Do NOT report style preferences, naming opinions, \
or a design choice you would simply have made differently; those are \
not defects unless they demonstrably cause harm. Only comment on the \
changed lines and their immediate context. An empty findings list is a \
valid, expected, and common answer for a clean diff -- do not invent an \
issue just to have something to say; return an empty list whenever \
nothing meets this bar.

Assess each finding's category yourself, using these definitions -- do \
not simply copy a static finding's category label:
- bug: incorrect runtime behavior, including a resource that is never \
released (e.g. an unclosed file or connection).
- security: an exploitable weakness.
- code_smell: a maintainability issue with no runtime impact.
- missing_tests: new logic introduced with no test covering it.
A static finding's category is a hint about where to look, not the \
answer for what category to report. The tool that found it may have \
labeled it something else (e.g. a style or maintainability category) \
even when it describes behavior that is actually incorrect at runtime.

Return ONLY a JSON object, no prose, no markdown fences, in exactly this shape.

If the diff has a defect to report:
{
  "findings": [
    {
      "category": "bug|security|code_smell|performance|missing_tests|api_misuse|missing_docs",
      "line": <int line number in the file>,
      "title": "<short title>",
      "explanation": "<why it matters>",
      "suggested_fix": "<concrete fix>",
      "relates_to_static_finding_id": "<id or null>"
    }
  ]
}

If the diff has no defect to report -- this is the correct output for a clean diff:
{
  "findings": []
}"""


def _format_hunk_with_line_numbers(h: Hunk) -> str:
    """Reformat h.diff so every line that exists in the new file is
    prefixed with its new-file line number, and removed lines are left
    unnumbered with their '-' marker -- so the model can read a line
    number directly instead of counting forward from the hunk header
    through added/removed/context lines (which is what caused at least
    one real misattribution: a finding reported two lines early, at the
    hunk's start_line, instead of the actual line of the change).

    The running counter is seeded from h.start_line -- the new-file start
    line the diff parser already extracted from the hunk header (the
    "+c,d" part of "@@ -a,b +c,d @@") -- and advanced over context/added
    lines, mirroring ordinary unified-diff semantics.

    Falls back to the raw, unformatted h.diff (unchanged, current
    behavior) whenever anything about that is uncertain: a missing/
    malformed header, an unrecognized line marker, or -- the main
    safety net -- if the line numbers this computes don't exactly match
    h.added_lines/h.context_lines, the diff parser's own authoritative
    per-line numbers. That keeps a bug here from silently feeding the
    model wrong line numbers; it just degrades to the old prompt shape.
    """
    lines = h.diff.split("\n")
    if not lines or not lines[0].startswith("@@"):
        log.warning(
            "Hunk %s: diff text doesn't start with a hunk header; "
            "leaving unformatted.",
            h.hunk_id,
        )
        return h.diff

    out = [lines[0]]  # hunk header, kept verbatim (not numbered)
    counter = h.start_line
    computed_added: list[int] = []
    computed_context: list[int] = []

    for line in lines[1:]:
        if not line or line.startswith("\\"):
            # "\ No newline at end of file" (or any other stray/blank
            # line): metadata about the preceding line, not a line of
            # its own -- pass through verbatim, don't consume a number.
            out.append(line)
            continue

        marker, content = line[0], line[1:]
        if marker == "+":
            out.append(f"{counter:>5} | {marker}{content}")
            computed_added.append(counter)
            counter += 1
        elif marker == " ":
            out.append(f"{counter:>5} | {marker}{content}")
            computed_context.append(counter)
            counter += 1
        elif marker == "-":
            # Removed lines have no new-file line number -- left blank,
            # still carrying the '-' marker so the model can tell it was
            # deleted rather than mistake the blank column for a context
            # line that just wasn't numbered.
            out.append(f"{'':>5} | {marker}{content}")
        else:
            log.warning(
                "Hunk %s: unrecognized diff line marker %r; leaving "
                "unformatted.",
                h.hunk_id,
                marker,
            )
            return h.diff

    if computed_added != h.added_lines or computed_context != h.context_lines:
        log.warning(
            "Hunk %s: computed line numbers (added=%s context=%s) don't "
            "match the diff parser's (added=%s context=%s); leaving "
            "unformatted.",
            h.hunk_id,
            computed_added,
            computed_context,
            h.added_lines,
            h.context_lines,
        )
        return h.diff

    return "\n".join(out)


def build_user_prompt(file: ReviewFile) -> str:
    """Assemble the per-file user message from the diff + static findings."""
    parts: list[str] = []
    parts.append(f"FILE: {file.file_path}  (language: {file.language})")
    parts.append("")
    parts.append("DIFF:")
    for h in file.hunks:
        parts.append(_format_hunk_with_line_numbers(h))
    parts.append("")

    if file.static_findings:
        parts.append("STATIC ANALYSIS FINDINGS (already detected):")
        for sf in file.static_findings:
            parts.append(
                f"- [{sf.finding_id}] ({sf.tool}:{sf.rule_id}) "
                f"line {sf.line} — {sf.category}: {sf.message}"
            )
    else:
        parts.append("STATIC ANALYSIS FINDINGS: none")

    parts.append("")
    parts.append("Review the change and respond with the JSON object only.")
    return "\n".join(parts)
