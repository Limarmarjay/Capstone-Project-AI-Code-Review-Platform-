"""Tests for storage.py (SQLite persistence) using a temp DB."""
import json
import sqlite3

import pytest

from repo_diff_pipeline import storage
from repo_diff_pipeline.models import (
    ChangeType, FileDiff, ParsedDiff, Severity, SubmissionReport, SummaryReport,
)


def _report(**kw):
    base = dict(
        repo_ref='o/r#1', base_ref='b', head_ref='h',
        diff=ParsedDiff(files=[FileDiff(path='a.py', change_type=ChangeType.ADDED)]),
        summary_report=SummaryReport(text='t', total_findings=3, findings_by_severity={Severity.ERROR.value: 3}),
    )
    base.update(kw)
    return SubmissionReport(**base)


def test_init_db_creates_table_and_is_idempotent(tmp_path):
    db = str(tmp_path / 'r.db')
    storage.init_db(db)
    storage.init_db(db)
    conn = sqlite3.connect(db)
    try:
        tables = conn.execute("select name from sqlite_master where type='table'").fetchall()
    finally:
        conn.close()
    assert ('review_results',) in tables


def test_save_then_get_round_trip(tmp_path):
    db = str(tmp_path / 'r.db')
    report_id = storage.save_report(_report(), db)
    row = storage.get_report(report_id, db)
    assert row['id'] == report_id
    assert (row['repo_ref'], row['base_ref'], row['head_ref']) == ('o/r#1', 'b', 'h')
    assert row['blocked'] == 0 and row['total_findings'] == 3
    assert row['report']['diff']['files'][0]['change_type'] == 'added'  # enums serialised by value
    assert 'report_json' not in row


def test_save_blocked_report_without_summary(tmp_path):
    db = str(tmp_path / 'r.db')
    report_id = storage.save_report(_report(summary_report=None, blocked=True, block_reason='x'), db)
    row = storage.get_report(report_id, db)
    assert row['blocked'] == 1 and row['total_findings'] == 0
    assert row['report']['block_reason'] == 'x'


def test_ids_are_unique(tmp_path):
    db = str(tmp_path / 'r.db')
    assert storage.save_report(_report(), db) != storage.save_report(_report(), db)


def test_get_report_unknown_id(tmp_path):
    db = str(tmp_path / 'r.db')
    storage.init_db(db)
    assert storage.get_report('missing', db) is None


def test_json_default_rejects_unknown_types():
    with pytest.raises(TypeError):
        json.dumps(object(), default=storage._json_default)


def test_json_default_handles_enums_and_dataclasses():
    assert storage._json_default(Severity.INFO) == 'info'
    assert storage._json_default(SummaryReport('t', 0))['text'] == 't'
