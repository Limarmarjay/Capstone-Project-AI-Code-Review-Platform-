from repo_diff_pipeline.models import (
    AIReviewResult,
    CheckFinding,
    CheckResult,
    ReviewComment,
    Severity,
)
from repo_diff_pipeline.report import (
    combine_findings,
    generate_inline_comments,
    generate_summary_report,
)


def _check_result(tool, severity, message="issue"):
    return CheckResult(
        tool=tool,
        ran=True,
        findings=[CheckFinding(tool=tool, path="a.py", line=1, severity=severity, message=message)],
    )


def test_combine_findings_merges_static_and_ai():
    check_results = [_check_result("ruff", Severity.WARNING)]
    ai_review = AIReviewResult(
        summary="looks fine",
        comments=[ReviewComment(path="a.py", line=2, severity=Severity.ERROR, comment="bug here")],
    )
    combined = combine_findings(check_results, ai_review)
    assert len(combined) == 2
    sources = {f.source for f in combined}
    assert sources == {"static:ruff", "ai"}


def test_combine_findings_sorts_by_severity_critical_first():
    check_results = [
        _check_result("toolA", Severity.INFO),
        _check_result("toolB", Severity.CRITICAL),
        _check_result("toolC", Severity.WARNING),
    ]
    combined = combine_findings(check_results, None)
    assert [f.severity for f in combined] == [Severity.CRITICAL, Severity.WARNING, Severity.INFO]


def test_combine_findings_skips_ai_review_with_error():
    ai_review = AIReviewResult(summary="", error="malformed output")
    combined = combine_findings([], ai_review)
    assert combined == []


def test_generate_inline_comments_preserves_count_and_severity():
    check_results = [_check_result("bandit", Severity.CRITICAL, "sql injection risk")]
    combined = combine_findings(check_results, None)
    inline = generate_inline_comments(combined)
    assert len(inline) == 1
    assert inline[0].severity == Severity.CRITICAL
    assert "bandit" in inline[0].body


def test_generate_summary_report_counts_by_severity():
    check_results = [
        _check_result("toolA", Severity.CRITICAL),
        _check_result("toolB", Severity.WARNING),
    ]
    combined = combine_findings(check_results, None)
    summary = generate_summary_report(combined, None)
    assert summary.total_findings == 2
    assert summary.findings_by_severity == {"critical": 1, "warning": 1}


def test_generate_summary_report_with_no_findings():
    summary = generate_summary_report([], None)
    assert summary.total_findings == 0
    assert "No issues found" in summary.text
