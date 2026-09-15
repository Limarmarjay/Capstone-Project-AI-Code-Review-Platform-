"""
Run static checks against changed files.

SAFETY MODEL:
- By default this module only runs *static analysis* tools (linters,
  formatters-in-check-mode, security scanners like bandit/semgrep) against
  files on disk. It never imports, executes, or evals submitted source code.
- Static analysis subprocesses are still run with a timeout and (on POSIX)
  memory/CPU limits, since a malicious file could still try to make the
  *analysis tool itself* misbehave (e.g. a linter plugin, or a crafted file
  that triggers pathological tool behavior).
- If your platform needs to actually *execute* submitted code (e.g. to run
  its test suite), do NOT do that with plain subprocess like this module
  uses. Use a real isolation boundary instead: a locked-down container
  (gVisor/Kata/Firecracker), no network access, a non-root user, a fresh
  filesystem, and hard CPU/memory/time/pids limits enforced by the
  container runtime — not by the process itself. This module intentionally
  does not implement that, since a partial sandbox is worse than none: it
  looks safe enough to reach for, without actually being a security boundary.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

try:
    # POSIX-only. On Windows there is no equivalent, so subprocess memory
    # capping is simply unavailable and we fall back to the timeout alone.
    import resource
except ImportError:  # pragma: no cover - platform dependent
    resource = None

from .models import CheckFinding, CheckResult, FileDiff, Severity

CHECK_TIMEOUT_SECONDS = 20
MAX_CHECK_MEMORY_MB = 512


def _limit_resources() -> None:
    """preexec_fn to cap memory for the static-analysis subprocess itself."""
    max_bytes = MAX_CHECK_MEMORY_MB * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
    except (ValueError, OSError):
        pass  # Best effort; not all platforms support this.


def _run_tool(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    # preexec_fn is POSIX-only and unsupported on Windows; pass None there.
    preexec = _limit_resources if resource is not None else None
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT_SECONDS,
        preexec_fn=preexec,
        check=False,  # tool exit codes (e.g. ruff/bandit finding issues) are expected, not failures
    )


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def run_ruff(repo_path: Path, py_files: list[str]) -> CheckResult:
    """Fast Python linter, check-only (no --fix)."""
    if not py_files:
        return CheckResult(tool="ruff", ran=False, skipped_reason="no Python files changed")
    if not _tool_available("ruff"):
        return CheckResult(tool="ruff", ran=False, skipped_reason="ruff not installed")

    try:
        proc = _run_tool(["ruff", "check", "--output-format=json", *py_files], cwd=repo_path)
    except subprocess.TimeoutExpired:
        return CheckResult(tool="ruff", ran=False, error="timed out")

    findings: list[CheckFinding] = []
    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return CheckResult(tool="ruff", ran=True, error=f"could not parse ruff output: {proc.stdout[:200]}")

    for item in raw:
        findings.append(
            CheckFinding(
                tool="ruff",
                path=item.get("filename", ""),
                line=item.get("location", {}).get("row"),
                severity=Severity.WARNING,
                message=item.get("message", ""),
                rule_id=item.get("code"),
            )
        )
    return CheckResult(tool="ruff", ran=True, findings=findings)


def run_bandit(repo_path: Path, py_files: list[str]) -> CheckResult:
    """Python security scanner."""
    if not py_files:
        return CheckResult(tool="bandit", ran=False, skipped_reason="no Python files changed")
    if not _tool_available("bandit"):
        return CheckResult(tool="bandit", ran=False, skipped_reason="bandit not installed")

    try:
        proc = _run_tool(["bandit", "-f", "json", "-q", *py_files], cwd=repo_path)
    except subprocess.TimeoutExpired:
        return CheckResult(tool="bandit", ran=False, error="timed out")

    try:
        raw = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return CheckResult(tool="bandit", ran=True, error=f"could not parse bandit output: {proc.stdout[:200]}")

    severity_map = {"LOW": Severity.INFO, "MEDIUM": Severity.WARNING, "HIGH": Severity.CRITICAL}
    findings = [
        CheckFinding(
            tool="bandit",
            path=r.get("filename", ""),
            line=r.get("line_number"),
            severity=severity_map.get(r.get("issue_severity", "LOW"), Severity.INFO),
            message=r.get("issue_text", ""),
            rule_id=r.get("test_id"),
        )
        for r in raw.get("results", [])
    ]
    return CheckResult(tool="bandit", ran=True, findings=findings)


def apply_security_rules(diff_files: list[FileDiff]) -> CheckResult:
    """
    'Apply Security Rules' step: lightweight, diff-only security heuristics
    that don't require a repo checkout (so they also work for patch-only
    submissions with no files on disk). Bandit (in run_all_checks) covers
    deeper Python-specific analysis when a checkout is available; this is a
    fast, language-agnostic backstop.
    """
    import re

    rules = [
        ("disabled_tls_verification", re.compile(r"verify\s*=\s*False"), Severity.ERROR),
        ("shell_true_subprocess", re.compile(r"shell\s*=\s*True"), Severity.WARNING),
        ("eval_or_exec_call", re.compile(r"\b(eval|exec)\s*\("), Severity.ERROR),
        ("hardcoded_debug_flag", re.compile(r"\bDEBUG\s*=\s*True\b"), Severity.INFO),
    ]

    findings: list[CheckFinding] = []
    for f in diff_files:
        if f.is_binary:
            continue
        for hunk in f.hunks:
            for line in hunk.lines:
                if line.line_type != "add":
                    continue
                for rule_id, pattern, severity in rules:
                    if pattern.search(line.content):
                        findings.append(
                            CheckFinding(
                                tool="security_rules",
                                path=f.path,
                                line=line.target_line_no,
                                severity=severity,
                                message=f"Matched security rule '{rule_id}' in added code.",
                                rule_id=rule_id,
                            )
                        )
    return CheckResult(tool="security_rules", ran=True, findings=findings)


def run_all_checks(repo_path: Path | None, diff_files: list[FileDiff]) -> list[CheckResult]:
    """
    Run the registered static checks against files touched by the diff.
    Deleted files are skipped since there's nothing on disk to analyze.
    File-based tools (ruff/bandit) are skipped entirely if no repo checkout
    is available (e.g. patch-only submissions); the diff-only security
    rules still run regardless.
    """
    results = [apply_security_rules(diff_files)]

    if repo_path is None:
        results.append(CheckResult(tool="ruff", ran=False, skipped_reason="no repo checkout available"))
        results.append(CheckResult(tool="bandit", ran=False, skipped_reason="no repo checkout available"))
        return results

    active_files = [f.path for f in diff_files if f.change_type.value != "deleted" and not f.is_binary]
    py_files = [f for f in active_files if f.endswith(".py")]

    checks: list[Callable[[Path, list[str]], CheckResult]] = [run_ruff, run_bandit]
    results.extend(check(repo_path, py_files) for check in checks)
    return results