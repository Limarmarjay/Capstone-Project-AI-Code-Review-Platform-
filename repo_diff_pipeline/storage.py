"""'Database Persistence' section: save results, and let a display layer fetch them back."""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum

from .models import SubmissionReport

DEFAULT_DB_PATH = "review_results.db"


def _json_default(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_results (
                id TEXT PRIMARY KEY,
                repo_ref TEXT NOT NULL,
                base_ref TEXT,
                head_ref TEXT,
                blocked INTEGER NOT NULL,
                total_findings INTEGER,
                created_at TEXT NOT NULL,
                report_json TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_report(report: SubmissionReport, db_path: str = DEFAULT_DB_PATH) -> str:
    """'Save Results' step. Returns the generated report id."""
    init_db(db_path)
    report_id = str(uuid.uuid4())
    total_findings = report.summary_report.total_findings if report.summary_report else 0

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO review_results
                (id, repo_ref, base_ref, head_ref, blocked, total_findings, created_at, report_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                report.repo_ref,
                report.base_ref,
                report.head_ref,
                int(report.blocked),
                total_findings,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(report, default=_json_default),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return report_id


def get_report(report_id: str, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    """'Display Review Results' step reads back via this."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM review_results WHERE id = ?", (report_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    result = dict(row)
    result["report"] = json.loads(result.pop("report_json"))
    return result
