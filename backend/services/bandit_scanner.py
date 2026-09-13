import json
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

class ScanError(Exception):
    """Raised when static analysis cannot be completed."""
    pass

SEVERITY_MAP = {
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}

def _run_bandit(file_path: str) -> dict:
    """
    Run Bandit and return its raw JSON report.
    
    This function is private because Bandit's raw output may contain
    sensitive source code.
    """

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "bandit",
                "-q",
                "-f",
                "json",
                file_path,
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    except subprocess.TimeoutExpired as exc:
        logger.exception("Bandit scan timed out.")
        raise ScanError("Static analysis timed out.") from exc

    try:
        report = json.loads(result.stdout)

    except json.JSONDecodeError as exc:
        logger.error(
        "Bandit returned invalid JSON. returncode=%s, stderr=%s",
        result.returncode,
        result.stderr,
        )

        raise ScanError(
            "Static analysis returned an invalid report."
        ) from exc

    # Bandit 1.9.4 can return exit code 0 even when
    # the file could not actually be parsed.
    scan_errors = report.get("errors", [])

    if scan_errors:
        logger.error(
            "Bandit could not analyze file. errors=%s",
            scan_errors,
        )

        raise ScanError("File could not be analyzed.")

    # Observed behavior: 
    # 0 = successful scan with no findings
    # 1 = successful scan with findings
    if result.returncode not in (0, 1):
        logger.error(
            "Bandit execution failed. returncode=%s, stderr=%s",
            result.returncode,
            result.stderr,
        )
        raise ScanError("Static analysis failed.")

    return report


def scan_python_file(file_path: str) -> dict:
    """
    Run Bandit and normalize its findings into our platform format.
    
    NOTE: 
    The message and code fields are not redacted yet.
    Do not expose this through the Flask API yet.
    """

    report = _run_bandit(file_path)

    findings = []

    severity_counts = {
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
    }

    for issue in report.get("results", []):
        native_severity = issue.get(
            "issue_severity", 
            "LOW",
            )

        normalized_severity = SEVERITY_MAP.get(
            native_severity, 
            "info",
        )

        severity_counts[normalized_severity] += 1

        findings.append(
            {
                "source": "bandit",
                "rule_id": issue.get("test_id"),
                "rule_name": issue.get("test_name"),
                "severity": native_severity,
                "normalized_severity": normalized_severity,
                "confidence": issue.get("issue_confidence"),
                "message": issue.get("issue_text"),
                "code": issue.get("code"),
                "line": issue.get("line_number"),
                "line_range": issue.get("line_range"),
                "cwe": issue.get("issue_cwe"),
                "more_info": issue.get("more_info"),
            }
        )

    return {
        "tool": "bandit",
        "total_findings": len(findings),
        "severity_counts": severity_counts,
        "findings": findings,
    }