import json
from dataclasses import asdict

import pytest

from backend.services.analysis_input import (
    bandit_finding_to_static_finding,
    scan_python_file_to_static_findings,
)
from backend.services.bandit_scanner import ScanError



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