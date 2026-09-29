from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from .bandit_scanner import ScanError, scan_python_file
from .redactor import redact_findings, redact_text
from llm_review.types import StaticFinding
from repo_diff_pipeline.models import (
    ChangeType,
    FileDiff,
    Hunk as ParsedHunk,
    ParsedDiff,
)

BANDIT_TO_REVIEW_SEVERITY = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "low",
}


def bandit_finding_to_static_finding(
    finding: dict,
    file_path: str,
) -> StaticFinding:
    rule_id = finding.get("rule_id", "")
    line = int(finding["line"])

    normalized_severity = finding.get("normalized_severity", "info")
    review_severity = BANDIT_TO_REVIEW_SEVERITY.get(
        normalized_severity
    )

    return StaticFinding(
        finding_id=f"bandit:{rule_id}:{file_path}:{line}",
        tool="bandit",
        rule_id=rule_id,
        category="security",
        message=finding.get("message", ""),
        file_path=file_path,
        line=line,
        severity=review_severity,
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


def _is_scannable_python(file_diff: FileDiff) -> bool:
    return (
        file_diff.extension == "py"
        and not file_diff.is_binary
        and file_diff.change_type is not ChangeType.DELETED
    )


def _reconstruct_added_python_file(file_diff: FileDiff) -> str:
    numbered_lines: list[tuple[int, str]] = []

    for hunk in file_diff.hunks:
        for line in hunk.lines:
            if line.line_type != "add" or line.target_line_no is None:
                raise ScanError(
                    "Added Python file could not be reconstructed completely."
                )

            numbered_lines.append(
                (line.target_line_no, line.content)
            )

    if not numbered_lines:
        return ""

    numbered_lines.sort(key=lambda item: item[0])

    actual_lines = [
        line_number
        for line_number, _ in numbered_lines
    ]

    expected_lines = list(
        range(1, actual_lines[-1] + 1)
    )

    if actual_lines != expected_lines:
        raise ScanError(
            "Added Python file could not be reconstructed completely."
        )

    return (
        "\n".join(
            content
            for _, content in numbered_lines
        )
        + "\n"
    )


def _scan_added_python_file(
    file_diff: FileDiff,
) -> list[StaticFinding]:
    source = _reconstruct_added_python_file(file_diff)

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / "submitted.py"

        temp_path.write_text(
            source,
            encoding="utf-8",
        )

        return scan_python_file_to_static_findings(
            str(temp_path),
            file_diff.path,
        )


def build_review_file(
    file_diff: FileDiff,
    repo_path: str | None,
    *,
    run_static_analysis: bool = True,
) -> dict:
    static_findings: list[StaticFinding] = []

    if run_static_analysis and _is_scannable_python(file_diff):
        if repo_path is not None:
            repo_root = Path(repo_path).resolve()
            scan_path = (repo_root / file_diff.path).resolve()

            if not scan_path.is_relative_to(repo_root):
                raise ScanError("File path is outside the repository.")

            static_findings = scan_python_file_to_static_findings(
                str(scan_path),
                file_diff.path,
            )

        elif file_diff.change_type is ChangeType.ADDED:
            static_findings = _scan_added_python_file(
                file_diff
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
        if (
            _is_scannable_python(file_diff)
            and repo_path is None
            and file_diff.change_type is not ChangeType.ADDED
        ):
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

            review_file = build_review_file(
                file_diff,
                None,
                run_static_analysis=False,
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


