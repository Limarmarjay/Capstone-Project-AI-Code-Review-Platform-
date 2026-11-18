"""Branch coverage for pipeline.run_review_pipeline plus on-disk secret redaction."""
from contextlib import contextmanager

import pytest

from repo_diff_pipeline import pipeline, secrets_scanner
from repo_diff_pipeline.diff_parser import parse_diff
from repo_diff_pipeline.ingestion import IngestionResult
from repo_diff_pipeline.models import AIReviewResult, InputType

PATCH = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n a\n+b = 1\n"
SECRET_PATCH = (
    "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n a\n"
    "+password = 'hunter2hunter2'\n"
)


def _no_ai(monkeypatch):
    monkeypatch.setattr(pipeline.ai_reviewer, 'review_diff_with_retry', lambda *a, **k: AIReviewResult('fine'))


def test_ingestion_error_becomes_blocked_report():
    report = pipeline.run_review_pipeline(patch_text='')
    assert report.blocked and 'empty' in report.block_reason
    assert report.repo_ref == '<unknown>'


def test_invalid_input_is_blocked():
    report = pipeline.run_review_pipeline(patch_text='just some prose, not a diff')
    assert report.blocked and report.block_reason.startswith('Invalid input:')
    assert report.input_type is InputType.PATCH_FILE


def test_unparseable_diff_is_blocked(monkeypatch):
    def boom(_text):
        raise pipeline.DiffParseError('Could not parse diff: x')

    monkeypatch.setattr(pipeline, 'parse_diff', boom)
    report = pipeline.run_review_pipeline(patch_text=PATCH)
    assert report.blocked and report.block_reason == 'Could not parse diff: x'
    assert report.input_type is InputType.PATCH_FILE


def test_non_python_is_blocked_and_keeps_diff():
    patch = "diff --git a/s.css b/s.css\n--- a/s.css\n+++ b/s.css\n@@ -1 +1 @@\n-a\n+b\n"
    report = pipeline.run_review_pipeline(patch_text=patch)
    assert report.blocked and 'Only Python' in report.block_reason
    assert report.diff.files[0].path == 's.css'


def test_oversized_change_is_blocked(monkeypatch):
    monkeypatch.setattr(pipeline, 'MAX_REASONABLE_CHANGED_LINES', 1)
    report = pipeline.run_review_pipeline(patch_text=PATCH.replace('+b = 1\n', '+b = 1\n+c = 2\n').replace('+1,2', '+1,3'))
    assert report.blocked and 'review limit' in report.block_reason


def test_happy_path_persists_and_skips_ai_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.ai_reviewer, 'review_diff_with_retry', lambda *a, **k: pytest.fail('AI must be skipped'))
    db = str(tmp_path / 'r.db')
    report = pipeline.run_review_pipeline(patch_text=PATCH, run_ai_review=False, db_path=db)
    assert not report.blocked and report.ai_review is None
    assert report.persisted_id
    assert pipeline.storage.get_report(report.persisted_id, db)['id'] == report.persisted_id


def test_persist_false_does_not_touch_db(monkeypatch):
    _no_ai(monkeypatch)
    monkeypatch.setattr(pipeline.storage, 'save_report', lambda *a, **k: pytest.fail('should not persist'))
    report = pipeline.run_review_pipeline(patch_text=PATCH, persist=False)
    assert report.persisted_id is None and report.summary_report is not None


def test_secrets_are_redacted_in_diff_and_reported(monkeypatch):
    _no_ai(monkeypatch)
    report = pipeline.run_review_pipeline(patch_text=SECRET_PATCH, persist=False)
    assert [s.rule_id for s in report.secrets_found] == ['generic_secret_assignment']
    added = [l.content for h in report.diff.files[0].hunks for l in h.lines if l.line_type == 'add']
    assert all('hunter2' not in a for a in added)


def _fake_ingest(tmp_path, *, temp):
    @contextmanager
    def ingest(**kwargs):
        yield IngestionResult(
            input_type=InputType.GIT_DIFF, diff_text=SECRET_PATCH, repo_ref='r', base_ref='b', head_ref='h',
            repo_path=str(tmp_path), is_temp_checkout=temp,
        )
    return ingest


@pytest.mark.parametrize('temp,expect_redacted', [(True, True), (False, False)])
def test_on_disk_redaction_only_for_temp_checkouts(monkeypatch, tmp_path, temp, expect_redacted):
    _no_ai(monkeypatch)
    monkeypatch.setattr(pipeline.checks, 'run_all_checks', lambda *a: [])
    (tmp_path / 'app.py').write_text("a\npassword = 'hunter2hunter2'\n")
    monkeypatch.setattr(pipeline.ingestion, 'ingest_submission', _fake_ingest(tmp_path, temp=temp))
    pipeline.run_review_pipeline(repo_path='x', persist=False)
    content = (tmp_path / 'app.py').read_text()
    assert ('[REDACTED]' in content) is expect_redacted
    assert ('hunter2' in content) is not expect_redacted


# --- secrets_scanner direct --------------------------------------------------------

@pytest.mark.parametrize('line,rule', [
    ('key = "AKIAABCDEFGHIJKLMNOP"', 'aws_access_key_id'),
    ('aws_secret_access_key = "' + 'A' * 40 + '"', 'aws_secret_key'),
    ('api_key = "abcdefghijklmnop1234"', 'generic_api_key'),
    ('h = "Bearer abcdefghijklmnopqrstu"', 'bearer_token'),
    ('-----BEGIN RSA PRIVATE KEY-----', 'private_key_block'),
    ('t = "xoxb-1234567890-abcdef"', 'slack_token'),
    ('t = "ghp_' + 'a' * 36 + '"', 'github_token'),
])
def test_each_pattern_detected_and_value_removed(line, rule):
    diff = parse_diff(f"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+{line}\n")
    findings = secrets_scanner.scan_and_redact(diff)
    assert rule in [f.rule_id for f in findings]
    assert '[REDACTED]' in diff.files[0].hunks[0].lines[0].content
    assert all(not hasattr(f, 'value') for f in findings)


def test_removed_and_context_lines_are_not_scanned():
    diff = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n"
        " password = 'contextsecret1'\n-password = 'removedsecret1'\n+x = 1\n"
    )
    assert secrets_scanner.scan_and_redact(diff) == []


def test_redact_files_on_disk_edge_cases(tmp_path):
    diff = parse_diff(SECRET_PATCH)
    findings = secrets_scanner.scan_and_redact(diff)
    # no findings / no root -> no-op
    secrets_scanner.redact_files_on_disk(tmp_path, diff, [])
    secrets_scanner.redact_files_on_disk(None, diff, findings)
    # file missing on disk -> skipped quietly
    secrets_scanner.redact_files_on_disk(tmp_path, diff, findings)
    # line number beyond EOF -> untouched
    (tmp_path / 'app.py').write_text("only one line\n")
    secrets_scanner.redact_files_on_disk(tmp_path, diff, findings)
    assert (tmp_path / 'app.py').read_text() == "only one line\n"


def test_redact_files_on_disk_preserves_missing_trailing_newline(tmp_path):
    diff = parse_diff(SECRET_PATCH)
    findings = secrets_scanner.scan_and_redact(diff)
    (tmp_path / 'app.py').write_text("a\npassword = 'hunter2hunter2'")
    secrets_scanner.redact_files_on_disk(tmp_path, diff, findings)
    text = (tmp_path / 'app.py').read_text()
    assert text.endswith('[REDACTED]') or '[REDACTED]' in text
    assert not text.endswith('\n')


def test_redact_files_on_disk_ignores_unaffected_files(tmp_path):
    diff = parse_diff(SECRET_PATCH)
    findings = secrets_scanner.scan_and_redact(diff)
    other = parse_diff(PATCH.replace('app.py', 'other.py'))
    (tmp_path / 'other.py').write_text('a\nb = 1\n')
    secrets_scanner.redact_files_on_disk(tmp_path, other, findings)
    assert (tmp_path / 'other.py').read_text() == 'a\nb = 1\n'


def test_redact_files_on_disk_survives_read_and_write_errors(tmp_path, monkeypatch):
    from pathlib import Path

    diff = parse_diff(SECRET_PATCH)
    findings = secrets_scanner.scan_and_redact(diff)
    target = tmp_path / 'app.py'
    target.write_text("a\npassword = 'hunter2hunter2'\n")

    def unreadable(self, *a, **k):
        raise OSError('denied')

    monkeypatch.setattr(Path, 'read_text', unreadable)
    secrets_scanner.redact_files_on_disk(tmp_path, diff, findings)  # read error: skipped
    monkeypatch.undo()

    monkeypatch.setattr(Path, 'write_text', unreadable)
    secrets_scanner.redact_files_on_disk(tmp_path, diff, findings)  # write error: swallowed
    monkeypatch.undo()
    assert 'hunter2' in target.read_text()
