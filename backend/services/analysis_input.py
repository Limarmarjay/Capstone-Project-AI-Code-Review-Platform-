from dataclasses import asdict
from pathlib import Path

from backend.services.bandit_scanner import ScanError, scan_python_file
from backend.services.redactor import redact_findings, redact_text
from llm_review.types import StaticFinding
from repo_diff_pipeline.models import (
    ChangeType,
    FileDiff,
    Hunk as ParsedHunk,
    ParsedDiff,
)


def bandit_finding_to_static_finding(
    finding: dict,
    file_path: str,
) -> StaticFinding:
    rule_id = finding.get("rule_id", "")
    line = int(finding["line"])

    return StaticFinding(
        finding_id=f"bandit:{rule_id}:{file_path}:{line}",
        tool="bandit",
        rule_id=rule_id,
        category="security",
        message=finding.get("message", ""),
        file_path=file_path,
        line=line,
    )


def scan_python_file_to_static_findings(
    scan_path: str,
    file_path: str,
) -> list[StaticFinding]:
    results = scan_python_file(scan_path)
    redacted_results = redact_findings(results)

    return [
        bandit_finding_to_static_finding(finding, file_path)
        for finding in redacted_results.get("findings", [])
    ]


def parsed_hunk_to_review_hunk(
    hunk: ParsedHunk,
    hunk_id: str,
) -> dict:
    diff_lines = [
        f"@@ -{hunk.source_start},{hunk.source_length} "
        f"+{hunk.target_start},{hunk.target_length} @@"
    ]

    added_lines = []
    removed_lines = []
    context_lines = []

    for line in hunk.lines:
        prefix = {
            "add": "+",
            "del": "-",
            "context": " ",
        }[line.line_type]

        diff_lines.append(f"{prefix}{line.content}")

        if line.line_type == "add" and line.target_line_no is not None:
            added_lines.append(line.target_line_no)

        elif line.line_type == "del" and line.source_line_no is not None:
            removed_lines.append(line.source_line_no)

        elif line.line_type == "context" and line.target_line_no is not None:
            context_lines.append(line.target_line_no)

    rendered_diff = "\n".join(diff_lines)
    redacted_diff = redact_text(rendered_diff)

    return {
        "hunk_id": hunk_id,
        "start_line": hunk.target_start,
        "end_line": hunk.target_start + max(hunk.target_length - 1, 0),
        "diff": redacted_diff,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "context_lines": context_lines,
    }


def build_review_file(
    file_diff: FileDiff,
    repo_path: str | None,
) -> dict:
    static_findings: list[StaticFinding] = []

    can_scan = (
        repo_path is not None
        and not file_diff.is_binary
        and file_diff.extension == "py"
        and file_diff.change_type is not ChangeType.DELETED
    )

    if can_scan:
        repo_root = Path(repo_path).resolve()
        scan_path = (repo_root / file_diff.path).resolve()

        if not scan_path.is_relative_to(repo_root):
            raise ScanError("File path is outside the repository.")

        static_findings = scan_python_file_to_static_findings(
            str(scan_path),
            file_diff.path,
        )

    hunks = [
        parsed_hunk_to_review_hunk(
            hunk,
            f"h{index}",
        )
        for index, hunk in enumerate(file_diff.hunks, start=1)
    ]

    return {
        "file_path": file_diff.path,
        "language": "python" if file_diff.extension == "py" else file_diff.extension,
        "hunks": hunks,
        "static_findings": [
            asdict(finding)
            for finding in static_findings
        ],
    }


def build_analysis_input(
    parsed_diff: ParsedDiff,
    repo_path: str | None,
    *,
    submission_id: str,
    source_type: str,
    repository: str,
) -> tuple[dict, dict[str, str]]:
    review_files = []
    scan_issues: dict[str, str] = {}

    for file_diff in parsed_diff.files:
        should_scan_python = (
            file_diff.extension == "py"
            and not file_diff.is_binary
            and file_diff.change_type is not ChangeType.DELETED
        )

        if should_scan_python and repo_path is None:
            scan_issues[file_diff.path] = (
                "Static analysis not run: full file not available."
            )

        try:
            review_file = build_review_file(
                file_diff,
                repo_path,
            )
        except ScanError as exc:
            scan_issues[file_diff.path] = str(exc)

            # Keep the file and its redacted hunks without retrying Bandit.
            review_file = build_review_file(
                file_diff,
                None,
            )

        review_files.append(review_file)

    analysis_input = {
        "submission_id": submission_id,
        "source_type": source_type,
        "repository": repository,
        "redaction_applied": True,
        "files": review_files,
    }

    return analysis_input, scan_issues


