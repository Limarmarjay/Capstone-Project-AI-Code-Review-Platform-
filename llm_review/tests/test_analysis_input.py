import json
from dataclasses import asdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = REPO_ROOT / "Static_checks_and_sec_rules"
SAMPLES = STATIC_ROOT / "samples"

from Static_checks_and_sec_rules.services.analysis_input import (
    bandit_finding_to_static_finding,
    build_analysis_input,
    build_review_file,
    parsed_hunk_to_review_hunk,
    scan_python_file_to_static_findings,
)

from Static_checks_and_sec_rules.services.bandit_scanner import ScanError


from repo_diff_pipeline.models import (
    ChangeType,
    DiffLine,
    FileDiff,
    Hunk,
    ParsedDiff,
)
from repo_diff_pipeline.diff_parser import parse_diff

from llm_review.types import AnalysisInput


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
    assert result.severity == "high"


def test_python_file_scans_redacts_and_converts_findings():
    findings = scan_python_file_to_static_findings(
        str(SAMPLES / "vulnerable.py"),
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
            str(SAMPLES / "broken.py"),
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
        str(STATIC_ROOT),
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
            str(SAMPLES),
        )


def test_build_analysis_input_continues_after_file_scan_error():
    parsed_diff = ParsedDiff(
        files=[
            FileDiff(
                path="samples/vulnerable.py",
                change_type=ChangeType.MODIFIED,
                hunks=[],
            ),
            FileDiff(
                path="samples/broken.py",
                change_type=ChangeType.MODIFIED,
                hunks=[],
            ),
        ]
    )

    analysis_input, scan_issues = build_analysis_input(
        parsed_diff,
        str(STATIC_ROOT),
        submission_id="sub_test",
        source_type="git_diff",
        repository="example/repo",
    )

    assert analysis_input["redaction_applied"] is True
    assert len(analysis_input["files"]) == 2

    vulnerable_file = analysis_input["files"][0]
    broken_file = analysis_input["files"][1]

    assert len(vulnerable_file["static_findings"]) == 3
    assert broken_file["static_findings"] == []

    assert scan_issues["samples/broken.py"] == "File could not be analyzed."

    serialized = json.dumps(analysis_input)
    assert "super_secret_password" not in serialized


def test_build_analysis_input_records_when_static_analysis_is_skipped():
    parsed_diff = ParsedDiff(
        files=[
            FileDiff(
                path="src/example.py",
                change_type=ChangeType.MODIFIED,
                hunks=[],
            )
        ]
    )

    analysis_input, scan_issues = build_analysis_input(
        parsed_diff,
        None,
        submission_id="sub_patch",
        source_type="patch_file",
        repository="<patch-file>",
    )

    assert analysis_input["files"][0]["static_findings"] == []
    assert scan_issues["src/example.py"] == (
        "Static analysis not run: full file not available."
    )


def test_built_analysis_input_matches_llm_contract():
    parsed_diff = ParsedDiff(
        files=[
            FileDiff(
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
        ]
    )

    analysis_input, scan_issues = build_analysis_input(
        parsed_diff,
        str(STATIC_ROOT),
        submission_id="sub_contract",
        source_type="git_diff",
        repository="example/repo",
    )

    parsed = AnalysisInput.from_dict(analysis_input)

    assert parsed.submission_id == "sub_contract"
    assert parsed.source_type == "git_diff"
    assert parsed.repository == "example/repo"
    assert parsed.redaction_applied is True
    assert scan_issues == {}

    parsed_file = parsed.files[0]

    assert parsed_file.file_path == "samples/vulnerable.py"
    assert parsed_file.language == "python"

    assert len(parsed_file.hunks) == 1

    hunk = parsed_file.hunks[0]

    assert hunk.hunk_id == "h1"
    assert hunk.start_line == 1
    assert hunk.end_line == 2
    assert hunk.added_lines == [2]
    assert hunk.context_lines == [1]
    assert "<REDACTED_SECRET>" in hunk.diff
    assert "super_secret_password" not in hunk.diff

    assert len(parsed_file.static_findings) == 3

    raw_b602 = next(
        finding
        for finding in analysis_input["files"][0]["static_findings"]
        if finding["rule_id"] == "B602"
    )

    parsed_b602 = next(
        finding
        for finding in parsed_file.static_findings
        if finding.rule_id == "B602"
    )

    assert parsed_b602.finding_id == raw_b602["finding_id"]
    assert parsed_b602.line == raw_b602["line"]
    assert parsed_b602.file_path == raw_b602["file_path"]
    assert parsed_b602.message == raw_b602["message"]


def test_added_python_file_without_repo_is_reconstructed_and_scanned():
    file_diff = FileDiff(
        path="src/new_file.py",
        change_type=ChangeType.ADDED,
        hunks=[
            Hunk(
                source_start=0,
                source_length=0,
                target_start=1,
                target_length=3,
                lines=[
                    DiffLine(
                        line_type="add",
                        content="import subprocess",
                        source_line_no=None,
                        target_line_no=1,
                    ),
                    DiffLine(
                        line_type="add",
                        content='password = "super_secret_password"',
                        source_line_no=None,
                        target_line_no=2,
                    ),
                    DiffLine(
                        line_type="add",
                        content='subprocess.run("echo hello", shell=True)',
                        source_line_no=None,
                        target_line_no=3,
                    ),
                ],
            )
        ],
    )

    parsed_diff = ParsedDiff(
        files=[file_diff]
    )

    analysis_input, scan_issues = build_analysis_input(
        parsed_diff,
        None,
        submission_id="sub_added",
        source_type="patch_file",
        repository="<patch-file>",
    )

    assert scan_issues == {}

    review_file = analysis_input["files"][0]

    rule_ids = {
        finding["rule_id"]
        for finding in review_file["static_findings"]
    }

    assert "B602" in rule_ids

    b602 = next(
        finding
        for finding in review_file["static_findings"]
        if finding["rule_id"] == "B602"
    )

    assert b602["line"] == 3

    serialized = json.dumps(review_file)

    assert "super_secret_password" not in serialized
    assert "<REDACTED_SECRET>" in serialized


def test_added_file_is_scanned_without_repo():
    parsed_diff = parse_diff(
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+import subprocess\n"
        "+def run(cmd):\n"
        "+    subprocess.run(cmd, shell=True)\n"
    )

    analysis_input, scan_issues = build_analysis_input(
        parsed_diff,
        None,
        submission_id="s",
        source_type="patch_file",
        repository="r",
    )

    findings = analysis_input["files"][0]["static_findings"]

    b602 = next(
        finding
        for finding in findings
        if finding["rule_id"] == "B602"
    )

    assert b602["line"] == 3
    assert scan_issues == {}


def test_incomplete_added_file_records_scan_issue_and_keeps_file():
    parsed_diff = ParsedDiff(
        files=[
            FileDiff(
                path="src/incomplete.py",
                change_type=ChangeType.ADDED,
                hunks=[
                    Hunk(
                        source_start=0,
                        source_length=0,
                        target_start=2,
                        target_length=1,
                        lines=[
                            DiffLine(
                                line_type="add",
                                content='print("hello")',
                                source_line_no=None,
                                target_line_no=2,
                            )
                        ],
                    )
                ],
            )
        ]
    )

    analysis_input, scan_issues = build_analysis_input(
        parsed_diff,
        None,
        submission_id="sub_incomplete",
        source_type="patch_file",
        repository="<patch-file>",
    )

    assert scan_issues["src/incomplete.py"] == (
        "Added Python file could not be reconstructed completely."
    )

    assert len(analysis_input["files"]) == 1
    assert analysis_input["files"][0]["file_path"] == "src/incomplete.py"
    assert analysis_input["files"][0]["static_findings"] == []


def test_bandit_info_severity_maps_to_low():
    finding = {
        "rule_id": "B404",
        "message": "Import of subprocess module.",
        "line": 1,
        "normalized_severity": "info",
    }

    result = bandit_finding_to_static_finding(
        finding,
        "src/example.py",
    )

    assert result.severity == "low"