"""Tests for bandit_scanner: real Bandit against the sample files, plus mocked-subprocess failure modes."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from Static_checks_and_sec_rules.services import bandit_scanner as bs

SAMPLES = Path(__file__).resolve().parents[1] / 'samples'


def _fake_run(stdout='{}', returncode=0, stderr=''):
    return lambda *a, **k: SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


# --- real bandit ------------------------------------------------------------------

def test_vulnerable_sample_produces_normalised_findings():
    result = bs.scan_python_file(str(SAMPLES / 'vulnerable.py'))
    assert result['tool'] == 'bandit'
    assert result['total_findings'] == len(result['findings']) > 0
    assert sum(result['severity_counts'].values()) == result['total_findings']
    finding = result['findings'][0]
    assert finding['source'] == 'bandit'
    assert finding['normalized_severity'] in {'high', 'medium', 'info'}
    assert {'rule_id', 'message', 'line', 'code', 'cwe'} <= set(finding)


def test_safe_sample_has_no_findings():
    result = bs.scan_python_file(str(SAMPLES / 'safe.py'))
    assert result['total_findings'] == 0
    assert result['severity_counts'] == {'critical': 0, 'high': 0, 'medium': 0, 'info': 0}


def test_unparseable_sample_raises_scan_error():
    with pytest.raises(bs.ScanError):
        bs.scan_python_file(str(SAMPLES / 'broken.py'))


# --- _run_bandit failure modes ------------------------------------------------------

def test_timeout_raises_scan_error(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired('bandit', 30)

    monkeypatch.setattr(bs.subprocess, 'run', boom)
    with pytest.raises(bs.ScanError, match='timed out'):
        bs._run_bandit('x.py')


def test_invalid_json_raises_scan_error(monkeypatch):
    monkeypatch.setattr(bs.subprocess, 'run', _fake_run(stdout='not json', returncode=2, stderr='crash'))
    with pytest.raises(bs.ScanError, match='invalid report'):
        bs._run_bandit('x.py')


def test_report_errors_raise_even_with_exit_code_zero(monkeypatch):
    monkeypatch.setattr(bs.subprocess, 'run', _fake_run(json.dumps({'errors': [{'reason': 'syntax'}], 'results': []})))
    with pytest.raises(bs.ScanError, match='could not be analyzed'):
        bs._run_bandit('x.py')


def test_unexpected_exit_code_raises(monkeypatch):
    monkeypatch.setattr(bs.subprocess, 'run', _fake_run(json.dumps({'results': []}), returncode=2))
    with pytest.raises(bs.ScanError, match='failed'):
        bs._run_bandit('x.py')


@pytest.mark.parametrize('code', [0, 1])
def test_exit_codes_zero_and_one_are_success(monkeypatch, code):
    monkeypatch.setattr(bs.subprocess, 'run', _fake_run(json.dumps({'results': []}), returncode=code))
    assert bs._run_bandit('x.py') == {'results': []}


def test_bandit_is_invoked_with_json_format_and_timeout(monkeypatch):
    seen = {}

    def spy(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return SimpleNamespace(stdout='{}', returncode=0, stderr='')

    monkeypatch.setattr(bs.subprocess, 'run', spy)
    bs._run_bandit('/tmp/f.py')
    assert seen['cmd'][-4:] == ['-f', 'json', '/tmp/f.py'][-4:] or '/tmp/f.py' in seen['cmd']
    assert 'json' in seen['cmd'] and seen['timeout'] == 30


# --- normalisation --------------------------------------------------------------------

def test_severity_mapping_and_defaults(monkeypatch):
    report = {'results': [
        {'issue_severity': 'HIGH', 'test_id': 'B1', 'line_number': 1},
        {'issue_severity': 'MEDIUM', 'test_id': 'B2', 'line_number': 2},
        {'issue_severity': 'LOW', 'test_id': 'B3', 'line_number': 3},
        {'issue_severity': 'ODD', 'test_id': 'B4', 'line_number': 4},
        {'test_id': 'B5', 'line_number': 5},  # missing severity -> LOW
    ]}
    monkeypatch.setattr(bs, '_run_bandit', lambda p: report)
    result = bs.scan_python_file('x.py')
    assert result['severity_counts'] == {'critical': 0, 'high': 1, 'medium': 1, 'info': 3}
    assert [f['normalized_severity'] for f in result['findings']] == ['high', 'medium', 'info', 'info', 'info']
