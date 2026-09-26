from backend.services.bandit_scanner import scan_python_file
from backend.services.redactor import redact_findings, redact_text
from llm_review.types import StaticFinding
from repo_diff_pipeline.models import Hunk as ParsedHunk


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