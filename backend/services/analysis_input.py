from backend.services.bandit_scanner import scan_python_file
from backend.services.redactor import redact_findings
from llm_review.types import StaticFinding


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