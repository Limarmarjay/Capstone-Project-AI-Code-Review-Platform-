"""
'Secrets Found?' / 'Redact Secrets' section of the workflow.

Scans only *added* lines (new content introduced by the submission) for
common secret patterns, and redacts them in place before the diff is ever
sent to static-analysis tools' output, the AI reviewer, or storage. This
is defensive: the goal is to stop credentials from being echoed back out
in review comments, logs, or the persisted report.

This is pattern-based and best-effort, not a guarantee — treat a "no
secrets found" result as "none matched known patterns," not as a
certified clean bill of health.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import FileDiff, ParsedDiff, SecretFinding

# (rule_id, compiled pattern). Patterns aim for recognizable, high-signal
# credential formats rather than generic "looks like a password" guessing.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?")),
    ("generic_api_key", re.compile(r"(?i)\b(api[_-]?key|apikey)\b\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}['\"]?")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN (RSA|EC|OPENSSH|PGP|DSA) PRIVATE KEY-----")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("generic_secret_assignment", re.compile(r"(?i)\b(secret|password|passwd|pwd)\b\s*[=:]\s*['\"][^'\"]{8,}['\"]")),
]

REDACTION_TEXT = "[REDACTED]"


def scan_and_redact(diff: ParsedDiff) -> list[SecretFinding]:
    """
    Mutates `diff` in place, replacing matched secret substrings with
    REDACTION_TEXT on added lines. Returns the list of findings (pre-redaction
    metadata only — never includes the secret value itself).
    """
    findings: list[SecretFinding] = []
    for file_diff in diff.files:
        findings.extend(_scan_file(file_diff))
    return findings


def _scan_file(file_diff: FileDiff) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for hunk in file_diff.hunks:
        for line in hunk.lines:
            if line.line_type != "add":
                continue
            redacted_content = line.content
            for rule_id, pattern in _PATTERNS:
                if pattern.search(redacted_content):
                    redacted_content = pattern.sub(REDACTION_TEXT, redacted_content)
                    findings.append(
                        SecretFinding(
                            path=file_diff.path,
                            line=line.target_line_no,
                            rule_id=rule_id,
                            redacted=True,
                        )
                    )
            line.content = redacted_content
    return findings


def redact_files_on_disk(repo_root: Path, diff: ParsedDiff, findings: list[SecretFinding]) -> None:
    """
    If a repo checkout exists (git-diff / PR submissions), the checked-out
    files still contain the raw secret even after `scan_and_redact` cleans
    the in-memory diff. Static-analysis tools run against those files
    directly, so without this step a secret redacted from the diff could
    still leak back out through a tool's finding message (e.g. bandit
    quoting the literal value it flagged). Rewrite the specific redacted
    lines in place, matched by line number, before any checks run.
    """
    if not findings or repo_root is None:
        return
    affected_paths = {f.path for f in findings}
    for file_diff in diff.files:
        if file_diff.path not in affected_paths:
            continue
        full_path = Path(repo_root) / file_diff.path
        if not full_path.is_file():
            continue
        try:
            on_disk_lines = full_path.read_text(errors="ignore").splitlines(keepends=True)
        except OSError:
            continue

        changed = False
        for hunk in file_diff.hunks:
            for line in hunk.lines:
                if line.line_type != "add" or REDACTION_TEXT not in line.content or not line.target_line_no:
                    continue
                idx = line.target_line_no - 1
                if 0 <= idx < len(on_disk_lines):
                    newline = "\n" if on_disk_lines[idx].endswith("\n") else ""
                    on_disk_lines[idx] = line.content + newline
                    changed = True

        if changed:
            try:
                full_path.write_text("".join(on_disk_lines))
            except OSError:
                pass
