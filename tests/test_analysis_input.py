import json
from dataclasses import asdict

import pytest

from backend.services.analysis_input import (
    bandit_finding_to_static_finding,
    build_review_file,
    parsed_hunk_to_review_hunk,
    scan_python_file_to_static_findings,
)
from backend.services.bandit_scanner import ScanError
from repo_diff_pipeline.models import (
    ChangeType,
    DiffLine,
    FileDiff,
    Hunk,
)



def test_bandit_finding_converts_to_static_finding():
    finding = {
        "rule_id": "B602",
        "message": "subprocess call with shell=True",
        "line": 10,
        "normalized_severity": "high",
    }

    result = bandit_finding_to_static_finding(
        finding,
        "src/example.py",
    )

    assert result.finding_id == "bandit:B602:src/example.py:10"
    assert result.tool == "bandit"
    assert result.rule_id == "B602"
    assert result.category == "security"
    assert result.message == "subprocess call with shell=True"
    assert result.file_path == "src/example.py"
    assert result.line == 10


def test_python_file_scans_redacts_and_converts_findings():
    findings = scan_python_file_to_static_findings(
        "samples/vulnerable.py",
        "src/vulnerable.py",
    )

    assert len(findings) == 3
    assert all(finding.tool == "bandit" for finding in findings)
    assert all(finding.category == "security" for finding in findings)
    assert all(finding.file_path == "src/vulnerable.py" for finding in findings)

    serialized = json.dumps([asdict(finding) for finding in findings])

    assert "super_secret_password" not in serialized
    assert "<REDACTED_SECRET>" in serialized
    assert any(finding.rule_id == "B602" for finding in findings)


def test_unparseable_file_raises_instead_of_returning_empty():
    with pytest.raises(ScanError, match="File could not be analyzed"):
        scan_python_file_to_static_findings(
            "samples/broken.py",
            "src/broken.py",
        )


def test_parsed_hunk_converts_to_review_hunk():
    hunk = Hunk(
        source_start=10,
        source_length=2,
        target_start=10,
        target_length=3,
        lines=[
            DiffLine(
                line_type="context",
                content="def example():",
                source_line_no=10,
                target_line_no=10,
            ),
            DiffLine(
                line_type="del",
                content='    password = "old_secret"',
                source_line_no=11,
                target_line_no=None,
            ),
            DiffLine(
                line_type="add",
                content='    password = "new_secret"',
                source_line_no=None,
                target_line_no=11,
            ),
            DiffLine(
                line_type="add",
                content="    return True",
                source_line_no=None,
                target_line_no=12,
            ),
        ],
    )

    result = parsed_hunk_to_review_hunk(hunk, "h1")

    assert result["hunk_id"] == "h1"
    assert result["start_line"] == 10
    assert result["end_line"] == 12

    assert result["added_lines"] == [11, 12]
    assert result["removed_lines"] == [11]
    assert result["context_lines"] == [10]

    assert result["diff"].startswith("@@ -10,2 +10,3 @@")

    assert "old_secret" not in result["diff"]
    assert "new_secret" not in result["diff"]
    assert result["diff"].count("<REDACTED_SECRET>") == 2

    assert '-    password = "<REDACTED_SECRET>"' in result["diff"]
    assert '+    password = "<REDACTED_SECRET>"' in result["diff"]


def test_build_review_file_combines_hunks_and_static_findings():
    file_diff = FileDiff(
        path="samples/vulnerable.py",
        change_type=ChangeType.MODIFIED,
        hunks=[
            Hunk(
                source_start=1,
                source_length=2,
                target_start=1,
                target_length=2,
                lines=[
                    DiffLine(
                        line_type="context",
                        content="import subprocess",
                        source_line_no=1,
                        target_line_no=1,
                    ),
                    DiffLine(
                        line_type="add",
                        content='password = "super_secret_password"',
                        source_line_no=None,
                        target_line_no=2,
                    ),
                ],
            )
        ],
    )

    result = build_review_file(
        file_diff,
        ".",
    )

    assert result["file_path"] == "samples/vulnerable.py"
    assert result["language"] == "python"

    assert len(result["hunks"]) == 1
    assert len(result["static_findings"]) == 3

    serialized = json.dumps(result)

    assert "super_secret_password" not in serialized
    assert "<REDACTED_SECRET>" in serialized

    assert any(
        finding["rule_id"] == "B602"
        for finding in result["static_findings"]
    )


def test_path_outside_repo_is_rejected():
    file_diff = FileDiff(
        path="../outside.py",
        change_type=ChangeType.MODIFIED,
        hunks=[],
    )

    with pytest.raises(ScanError, match="outside the repository"):
        build_review_file(
            file_diff,
            "samples",
        )