"""Prompt construction for the LLM review step.

The prompt asks the model to review *changed Python code*, given the diff
and what static analysis already found, and to return STRICT JSON so the
response is machine-parseable. Structured-output-by-instruction: we tell
the model to emit only JSON, then parse it defensively downstream.
"""
from __future__ import annotations

from .types import ReviewFile

SYSTEM_PROMPT = """You are a senior Python code reviewer performing an \
automated first-pass review of a code change (a diff), before a human \
reviewer sees it.

You are given:
- The diff for one file (added/removed/context lines).
- A list of issues that static analysis tools (Pylint, mypy, security \
scanners) already found on this change.

Your job:
1. Explain the static findings in plain language a junior developer can act on.
2. Find ADDITIONAL issues the static tools missed: bugs, security or \
privacy risks, code smells, performance problems, missing tests, incorrect \
API usage, or missing documentation.
3. For each issue, give a concrete suggested fix.

Only comment on the changed lines and their immediate context. Do not \
invent issues; if the change looks clean, return an empty findings list.

Return ONLY a JSON object, no prose, no markdown fences, in exactly this shape:
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
}"""


def build_user_prompt(file: ReviewFile) -> str:
    """Assemble the per-file user message from the diff + static findings."""
    parts: list[str] = []
    parts.append(f"FILE: {file.file_path}  (language: {file.language})")
    parts.append("")
    parts.append("DIFF:")
    for h in file.hunks:
        parts.append(h.diff)
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
