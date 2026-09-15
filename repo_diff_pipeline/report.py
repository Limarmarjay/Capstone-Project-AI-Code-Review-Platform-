"""
'Combine Findings (LLM + Static Analysis)' -> 'Assign Severity Levels' ->
'Generate Inline Comments (annotations)' -> 'Generate Summary Report'
"""
from __future__ import annotations

from collections import Counter

from .models import (
    AIReviewResult,
    CheckResult,
    CombinedFinding,
    InlineComment,
    Severity,
    SummaryReport,
)

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.ERROR: 1, Severity.WARNING: 2, Severity.INFO: 3}


def combine_findings(
    check_results: list[CheckResult], ai_review: AIReviewResult | None
) -> list[CombinedFinding]:
    """Merge static-check findings and AI review comments into one list, sorted by severity."""
    combined: list[CombinedFinding] = []

    for result in check_results:
        if not result.ran:
            continue
        for finding in result.findings:
            combined.append(
                CombinedFinding(
                    source=f"static:{result.tool}",
                    path=finding.path,
                    line=finding.line,
                    severity=finding.severity,
                    message=finding.message,
                    rule_id=finding.rule_id,
                )
            )

    if ai_review and ai_review.error is None:
        for comment in ai_review.comments:
            combined.append(
                CombinedFinding(
                    source="ai",
                    path=comment.path,
                    line=comment.line,
                    severity=comment.severity,
                    message=comment.comment,
                    rule_id=None,
                )
            )

    combined.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.path, f.line or 0))
    return combined


def generate_inline_comments(findings: list[CombinedFinding]) -> list[InlineComment]:
    """Turn combined findings into per-line annotations for display in a PR-style UI."""
    return [
        InlineComment(
            path=f.path,
            line=f.line,
            severity=f.severity,
            body=f"[{f.source}] {f.message}" + (f" ({f.rule_id})" if f.rule_id else ""),
            source=f.source,
        )
        for f in findings
    ]


def generate_summary_report(
    findings: list[CombinedFinding], ai_review: AIReviewResult | None
) -> SummaryReport:
    """Produce the human-readable top-of-PR summary."""
    counts = Counter(f.severity.value for f in findings)
    lines = []

    if ai_review and ai_review.summary:
        lines.append(ai_review.summary)
        lines.append("")
    elif ai_review and ai_review.error:
        lines.append(f"(AI review unavailable: {ai_review.error})")
        lines.append("")

    if findings:
        lines.append(f"Found {len(findings)} issue(s):")
        for severity in (Severity.CRITICAL, Severity.ERROR, Severity.WARNING, Severity.INFO):
            n = counts.get(severity.value, 0)
            if n:
                lines.append(f"  - {severity.value}: {n}")
    else:
        lines.append("No issues found by static checks or AI review.")

    return SummaryReport(
        text="\n".join(lines),
        total_findings=len(findings),
        findings_by_severity=dict(counts),
    )
