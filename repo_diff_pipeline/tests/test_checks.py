"""Tests for checks.py: ruff/bandit wrappers (subprocess mocked), security-rule heuristics, orchestration."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from repo_diff_pipeline import checks
from repo_diff_pipeline.diff_parser import parse_diff
from repo_diff_pipeline.models import Severity


def _proc(stdout):
    return SimpleNamespace(stdout=stdout, returncode=1)


def _diff_with(*added_lines, path="app.py"):
    body = "".join(f"+{line}\n" for line in added_lines)
    return parse_diff(
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(added_lines)} @@\n{body}"
    )


# --- run_ruff -----------------------------------------------------------------

def test_ruff_skipped_without_python_files(tmp_path):
    result = checks.run_ruff(tmp_path, [])
    assert not result.ran and 'no Python files' in result.skipped_reason


def test_ruff_skipped_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, '_tool_available', lambda name: False)
    result = checks.run_ruff(tmp_path, ['a.py'])
    assert not result.ran and 'not installed' in result.skipped_reason


def test_ruff_parses_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, '_tool_available', lambda name: True)
    payload = json.dumps([{'filename': 'a.py', 'location': {'row': 4}, 'message': 'unused', 'code': 'F401'}])
    monkeypatch.setattr(checks, '_run_tool', lambda cmd, cwd: _proc(payload))
    result = checks.run_ruff(tmp_path, ['a.py'])
    assert result.ran and result.error is None
    (finding,) = result.findings
    assert (finding.path, finding.line, finding.rule_id) == ('a.py', 4, 'F401')
    assert finding.severity is Severity.WARNING


def test_ruff_empty_output_means_no_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, '_tool_available', lambda name: True)
    monkeypatch.setattr(checks, '_run_tool', lambda cmd, cwd: _proc(''))
    assert checks.run_ruff(tmp_path, ['a.py']).findings == []


def test_ruff_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, '_tool_available', lambda name: True)

    def boom(cmd, cwd):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(checks, '_run_tool', boom)
    result = checks.run_ruff(tmp_path, ['a.py'])
    assert not result.ran and result.error == 'timed out'


def test_ruff_unparseable_output(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, '_tool_available', lambda name: True)
    monkeypatch.setattr(checks, '_run_tool', lambda cmd, cwd: _proc('garbage'))
    result = checks.run_ruff(tmp_path, ['a.py'])
    assert result.ran and 'could not parse ruff output' in result.error


# --- run_bandit ---------------------------------------------------------------

def test_bandit_skipped_without_python_files(tmp_path):
    assert not checks.run_bandit(tmp_path, []).ran


def test_bandit_skipped_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, '_tool_available', lambda name: False)
    assert 'not installed' in checks.run_bandit(tmp_path, ['a.py']).skipped_reason


def test_bandit_maps_severities(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, '_tool_available', lambda name: True)
    payload = json.dumps({'results': [
        {'filename': 'a.py', 'line_number': 1, 'issue_severity': 'LOW', 'issue_text': 'l', 'test_id': 'B1'},
        {'filename': 'a.py', 'line_number': 2, 'issue_severity': 'MEDIUM', 'issue_text': 'm', 'test_id': 'B2'},
        {'filename': 'a.py', 'line_number': 3, 'issue_severity': 'HIGH', 'issue_text': 'h', 'test_id': 'B3'},
        {'filename': 'a.py', 'line_number': 4, 'issue_severity': 'WEIRD', 'issue_text': 'w', 'test_id': 'B4'},
    ]})
    monkeypatch.setattr(checks, '_run_tool', lambda cmd, cwd: _proc(payload))
    severities = [f.severity for f in checks.run_bandit(tmp_path, ['a.py']).findings]
    assert severities == [Severity.INFO, Severity.WARNING, Severity.CRITICAL, Severity.INFO]


def test_bandit_timeout_and_bad_output(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, '_tool_available', lambda name: True)

    def boom(cmd, cwd):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(checks, '_run_tool', boom)
    assert checks.run_bandit(tmp_path, ['a.py']).error == 'timed out'

    monkeypatch.setattr(checks, '_run_tool', lambda cmd, cwd: _proc('not json'))
    result = checks.run_bandit(tmp_path, ['a.py'])
    assert result.ran and 'could not parse bandit output' in result.error


# --- low-level helpers --------------------------------------------------------

def test_run_tool_passes_timeout_and_never_raises_on_nonzero(tmp_path):
    proc = checks._run_tool(['python3', '-c', 'import sys; print("x"); sys.exit(3)'], cwd=tmp_path)
    assert proc.returncode == 3 and proc.stdout.strip() == 'x'


def test_run_tool_without_resource_module(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, 'resource', None)
    assert checks._run_tool(['python3', '-c', 'pass'], cwd=tmp_path).returncode == 0


def test_limit_resources_applies_rlimit(monkeypatch):
    calls = []
    fake = SimpleNamespace(RLIMIT_AS=9, setrlimit=lambda *a: calls.append(a))
    monkeypatch.setattr(checks, 'resource', fake)
    checks._limit_resources()
    assert calls == [(9, (512 * 1024 * 1024, 512 * 1024 * 1024))]


def test_limit_resources_swallows_errors(monkeypatch):
    def fail(*_a):
        raise ValueError('nope')

    monkeypatch.setattr(checks, 'resource', SimpleNamespace(RLIMIT_AS=9, setrlimit=fail))
    checks._limit_resources()  # must not raise


def test_tool_available(monkeypatch):
    monkeypatch.setattr(checks.shutil, 'which', lambda name: '/bin/x' if name == 'yes' else None)
    assert checks._tool_available('yes') and not checks._tool_available('no')


# --- apply_security_rules -----------------------------------------------------

@pytest.mark.parametrize('line,rule,severity', [
    ('requests.get(u, verify=False)', 'disabled_tls_verification', Severity.ERROR),
    ('eval(user_input)', 'eval_or_exec_call', Severity.ERROR),
    ('exec (code)', 'eval_or_exec_call', Severity.ERROR),
    ('DEBUG = True', 'hardcoded_debug_flag', Severity.INFO),
])
def test_security_rule_matches(line, rule, severity):
    result = checks.apply_security_rules(_diff_with(line).files)
    assert [(f.rule_id, f.severity) for f in result.findings] == [(rule, severity)]
    assert result.findings[0].line == 1


def test_security_rules_ignore_clean_code_and_lookalikes():
    diff = _diff_with('evaluate(x)', 'verify = True', 'x = 1')
    assert checks.apply_security_rules(diff.files).findings == []


def test_security_rules_only_inspect_added_lines():
    diff = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-eval(x)\n+safe(x)\n"
    )
    assert checks.apply_security_rules(diff.files).findings == []


def test_security_rules_skip_binary_files():
    diff = _diff_with('eval(x)')
    diff.files[0].is_binary = True
    assert checks.apply_security_rules(diff.files).findings == []


# --- run_all_checks -----------------------------------------------------------

def test_run_all_checks_without_checkout_skips_file_tools():
    results = checks.run_all_checks(None, _diff_with('eval(x)').files)
    assert [r.tool for r in results] == ['security_rules', 'ruff', 'bandit']
    assert results[0].ran and not results[1].ran and not results[2].ran
    assert 'no repo checkout' in results[1].skipped_reason


def test_run_all_checks_only_passes_active_python_files(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(checks, 'run_ruff', lambda p, files: seen.setdefault('ruff', files) and checks.CheckResult('ruff', True))
    monkeypatch.setattr(checks, 'run_bandit', lambda p, files: checks.CheckResult('bandit', True))
    diff = parse_diff(
        "diff --git a/keep.py b/keep.py\n--- a/keep.py\n+++ b/keep.py\n@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n"
        "diff --git a/readme.md b/readme.md\n--- a/readme.md\n+++ b/readme.md\n@@ -1 +1 @@\n-a\n+b\n"
    )
    results = checks.run_all_checks(Path(tmp_path), diff.files)
    assert seen['ruff'] == ['keep.py']
    assert [r.tool for r in results] == ['security_rules', 'ruff', 'bandit']
