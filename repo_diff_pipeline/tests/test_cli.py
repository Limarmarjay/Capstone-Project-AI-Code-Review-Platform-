"""Tests for cli.py. The pipeline itself is mocked; we verify argument wiring and output."""
import json
import sys
from pathlib import Path

import pytest

from repo_diff_pipeline import cli
from repo_diff_pipeline.models import (
    InputType, ParsedDiff, Severity, SubmissionReport,
)


def _run(monkeypatch, argv, report=None):
    captured = {}

    def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return report or SubmissionReport('r', 'b', 'h', ParsedDiff(files=[]), input_type=InputType.PATCH_FILE)

    monkeypatch.setattr(cli.pipeline, 'run_review_pipeline', fake_pipeline)
    monkeypatch.setattr(sys, 'argv', ['cli', *argv])
    return captured


def test_patch_file_is_read_and_report_printed(monkeypatch, tmp_path, capsys):
    patch = tmp_path / 'p.patch'
    patch.write_text('PATCH BODY')
    captured = _run(monkeypatch, ['--patch-file', str(patch), '--no-ai-review', '--no-persist'])
    assert cli.main() == 0
    assert captured['patch_text'] == 'PATCH BODY'
    assert captured['run_ai_review'] is False and captured['persist'] is False
    out = json.loads(capsys.readouterr().out)
    assert out['input_type'] == 'patch_file'  # enum serialised


def test_blocked_report_returns_exit_code_1(monkeypatch, capsys):
    blocked = SubmissionReport('r', 'b', 'h', ParsedDiff(files=[]), blocked=True, block_reason='bad')
    _run(monkeypatch, ['--pr-url', 'https://github.com/o/r/pull/1'], blocked)
    assert cli.main() == 1


def test_output_flag_writes_file(monkeypatch, tmp_path, capsys):
    out_file = tmp_path / 'out.json'
    _run(monkeypatch, ['--repo-path', '.', '--base', 'a', '--head', 'b', '-o', str(out_file)])
    cli.main()
    assert capsys.readouterr().out == ''
    assert json.loads(out_file.read_text())['repo_ref'] == 'r'


def test_arguments_are_forwarded(monkeypatch):
    captured = _run(monkeypatch, [
        '--repo-url', 'https://x/y.git', '--base', 'b1', '--head', 'h1',
        '--github-token', 'tok', '--db-path', 'x.db',
    ])
    cli.main()
    assert captured['repo_url'] == 'https://x/y.git'
    assert (captured['base_ref'], captured['head_ref']) == ('b1', 'h1')
    assert captured['github_token'] == 'tok' and captured['db_path'] == 'x.db'
    assert captured['run_ai_review'] is True and captured['persist'] is True


def test_github_token_defaults_to_env(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'env-token')
    captured = _run(monkeypatch, ['--pr-url', 'https://github.com/o/r/pull/1'])
    cli.main()
    assert captured['github_token'] == 'env-token'


def test_no_source_is_a_usage_error(monkeypatch, capsys):
    _run(monkeypatch, [])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert 'is required' in capsys.readouterr().err


def test_empty_patch_file_still_counts_as_a_source(monkeypatch, tmp_path):
    patch = tmp_path / 'empty.patch'
    patch.write_text('')
    captured = _run(monkeypatch, ['--patch-file', str(patch)])
    cli.main()
    assert captured['patch_text'] == ''


def test_json_default():
    from pathlib import PurePath

    assert cli._json_default(Severity.ERROR) == 'error'
    assert cli._json_default(PurePath('/a/b')) == '/a/b'
    with pytest.raises(TypeError):
        cli._json_default(object())


def test_module_entrypoint_exits_with_main_status(monkeypatch, tmp_path):
    import runpy

    patch = tmp_path / 'p.patch'
    patch.write_text('diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n')
    monkeypatch.setattr(sys, 'argv', ['cli', '--patch-file', str(patch), '--no-ai-review', '--no-persist'])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module('repo_diff_pipeline.cli', run_name='__main__')
    assert exc.value.code == 0
