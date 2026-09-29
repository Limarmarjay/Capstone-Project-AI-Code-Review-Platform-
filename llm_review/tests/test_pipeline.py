"""Tests for the LLM review pipeline.

Run:  python -m pytest tests/ -v
(or with the stdlib: python tests/test_pipeline.py)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_review import review
from llm_review.client import MockLLMClient
from llm_review.severity import assign_severity, score
from llm_review.types import (
    Category,
    ReviewFile,
    Severity,
    StaticFinding,
)
from llm_review.pipeline import _combine, _static_to_finding


def _load_input():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "mock_analysis_input.json")
    with open(path) as f:
        return json.load(f)


def test_happy_path_produces_findings():
    out = review(_load_input(), client=MockLLMClient())
    assert out["review_status"] == "completed"
    assert out["llm_status"] == "success"
    assert len(out["findings"]) >= 1
    assert 0 <= out["overall_score"] <= 100


def test_combine_links_static_and_llm():
    out = review(_load_input(), client=MockLLMClient())
    # The SQL injection is flagged by both static (sf_002) and the LLM,
    # so at least one finding should be source=combined and reference sf_002.
    combined = [f for f in out["findings"] if f["source"] == "combined"]
    assert any("sf_002" in f["static_finding_ids"] for f in combined)


def test_different_categories_on_same_line_do_not_combine():
    static_finding = StaticFinding(
        finding_id="bandit:B404:example.py:1",
        tool="bandit",
        rule_id="B404",
        category="security",
        message="Import of subprocess module.",
        file_path="example.py",
        line=1,
        severity="low",
    )

    review_file = ReviewFile(
        file_path="example.py",
        language="python",
        hunks=[],
        static_findings=[static_finding],
    )

    llm_findings = [
        {
            "category": "missing_docs",
            "line": 1,
            "title": "Add a docstring to the changed function",
            "explanation": "The changed function has no docstring.",
            "suggested_fix": "Add a short docstring.",
            "relates_to_static_finding_id": None,
        }
    ]

    findings = _combine(
        review_file,
        llm_findings,
    )

    assert len(findings) == 2

    static_result = next(
        finding
        for finding in findings
        if static_finding.finding_id in finding.static_finding_ids
    )

    assert static_result.severity == Severity.LOW


def test_no_static_finding_is_dropped():
    out = review(_load_input(), client=MockLLMClient())
    referenced = {sid for f in out["findings"] for sid in f["static_finding_ids"]}
    # sf_001 (unused import) wasn't flagged by the mock LLM, but must still
    # appear as a static-source finding.
    assert "sf_001" in referenced


def test_fallback_on_llm_timeout():
    out = review(_load_input(), client=MockLLMClient(fail=True))
    assert out["llm_status"] == "timeout"
    # Pipeline still completes and still returns the static findings.
    assert out["review_status"] == "completed"
    assert all(f["source"] == "static" for f in out["findings"])
    assert any("sf_002" in f["static_finding_ids"] for f in out["findings"])


def test_fallback_on_garbage_response():
    out = review(_load_input(), client=MockLLMClient(garbage=True))
    assert out["llm_status"] == "invalid_response"
    assert out["review_status"] == "completed"
    assert len(out["findings"]) >= 1  # static findings survive


def test_redaction_gate_skips_llm():
    data = _load_input()
    data["redaction_applied"] = False
    out = review(data, client=MockLLMClient())
    assert out["llm_status"] == "skipped"
    assert all(f["source"] == "static" for f in out["findings"])


def test_severity_escalates_when_confirmed_by_both():
    base = assign_severity(Category.SECURITY)
    escalated = assign_severity(Category.SECURITY, confirmed_by_both=True)
    assert base == Severity.CRITICAL
    assert escalated == Severity.CRITICAL  # already max, stays

    base_bug = assign_severity(Category.BUG)
    esc_bug = assign_severity(Category.BUG, confirmed_by_both=True)
    assert base_bug == Severity.HIGH
    assert esc_bug == Severity.CRITICAL


def test_severity_levels_map_to_numbers():
    assert Severity.CRITICAL.level == 4
    assert Severity.HIGH.level == 3
    assert Severity.MEDIUM.level == 2
    assert Severity.LOW.level == 1


def test_score_clamped():
    class F:
        def __init__(self, sev): self.severity = sev
    assert score([F(Severity.CRITICAL)] * 5) == 0     # floored
    assert score([]) == 100                            # perfect


if __name__ == "__main__":
    # Minimal stdlib runner so it works without pytest installed.
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)


def test_low_base_severity_is_respected():
    assert (
        assign_severity(
            Category.SECURITY,
            base_severity=Severity.LOW,
        )
        == Severity.LOW
    )


def test_static_finding_uses_tool_baseline_severity():
    static_finding = StaticFinding(
        finding_id="bandit:B602:example.py:3",
        tool="bandit",
        rule_id="B602",
        category="security",
        message="Subprocess call with shell=True.",
        file_path="example.py",
        line=3,
        severity="high",
    )

    review_file = ReviewFile(
        file_path="example.py",
        language="python",
        hunks=[],
        static_findings=[static_finding],
    )

    finding = _static_to_finding(
        review_file,
        static_finding,
    )

    assert finding.severity == Severity.HIGH


def test_confirmed_static_finding_escalates_from_tool_baseline():
    static_finding = StaticFinding(
        finding_id="bandit:B602:example.py:3",
        tool="bandit",
        rule_id="B602",
        category="security",
        message="Subprocess call with shell=True.",
        file_path="example.py",
        line=3,
        severity="high",
    )

    review_file = ReviewFile(
        file_path="example.py",
        language="python",
        hunks=[],
        static_findings=[static_finding],
    )

    llm_findings = [
        {
            "category": "security",
            "line": 3,
            "title": "Shell command execution",
            "explanation": "The shell call may allow unsafe command execution.",
            "suggested_fix": "Avoid shell=True.",
            "relates_to_static_finding_id": "bandit:B602:example.py:3",
        }
    ]

    findings = _combine(
        review_file,
        llm_findings,
    )

    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
