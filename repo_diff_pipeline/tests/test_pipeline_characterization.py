"""Characterization tests for repo_diff_pipeline.pipeline.run_review_pipeline.

These freeze CURRENT end-to-end behavior of the orchestrator (ingestion ->
validation -> parsing -> secrets -> checks -> AI review -> combine -> report)
as a baseline before the llm_review/repo_diff_pipeline reconciliation work.
They are not a statement of desired behavior.

The real LLM call (ai_reviewer.review_diff_with_retry) is always mocked, so
these run offline with no API key and no network. checks.run_all_checks is
mocked in the last test only, to make the static-finding-message content
deterministic for that scenario; the other two let it run for real (it never
shells out to ruff/bandit when there's no repo checkout, only the pure-Python
apply_security_rules heuristic runs).
"""
from __future__ import annotations

import os
import subprocess

from repo_diff_pipeline.models import (
    AIReviewResult,
    CheckFinding,
    CheckResult,
    ReviewComment,
    Severity,
)
from repo_diff_pipeline.pipeline import run_review_pipeline

CLEAN_PATCH = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,5 @@
 def add(a, b):
     return a + b
+
+def multiply(a, b):
+    return a * b
"""

RISKY_PATCH = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,1 +1,2 @@
 import subprocess
+subprocess.run(cmd, shell=True)
"""


def _init_git_repo(tmp_path):
    """Create a tiny real git repo with two commits, for --repo-path-style
    ingestion. Returns (repo_path, base_sha, head_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Characterization Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Characterization Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True)

    git("init", "-q")
    (repo / "app.py").write_text("def handler():\n    return True\n")
    git("add", "app.py")
    git("commit", "-q", "-m", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo / "app.py").write_text(
        "def handler():\n"
        "    token = 'hunter2_fake_password'\n"  # dummy value, not a real secret
        "    return True\n"
    )
    git("add", "app.py")
    git("commit", "-q", "-m", "head")
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()

    return str(repo), base_sha, head_sha


def test_happy_path_patch_text_combines_static_and_ai(monkeypatch):
    """Patch-file submission (no repo checkout): a real static finding
    (apply_security_rules finds nothing here) plus a mocked AI comment
    should both surface in the final report."""
    fake_ai_result = AIReviewResult(
        summary="Looks reasonable overall.",
        comments=[
            ReviewComment(path="app.py", line=4, severity=Severity.INFO, comment="Consider a docstring."),
        ],
    )
    monkeypatch.setattr(
        "repo_diff_pipeline.pipeline.ai_reviewer.review_diff_with_retry",
        lambda *a, **k: fake_ai_result,
    )

    report = run_review_pipeline(patch_text=CLEAN_PATCH, persist=False)

    assert report.blocked is False
    assert report.ai_review is fake_ai_result
    assert any(f.source == "ai" for f in report.combined_findings)
    assert report.summary_report.total_findings == len(report.combined_findings)
    assert report.persisted_id is None  # persist=False


def test_security_rule_finding_survives_to_report_with_no_checkout(monkeypatch):
    """Patch-file submission that trips apply_security_rules: confirms the
    diff-only security heuristic's finding reaches combined_findings even
    with no repo checkout, and that ruff/bandit are correctly reported as
    skipped (not run, not errored) rather than silently absent."""
    monkeypatch.setattr(
        "repo_diff_pipeline.pipeline.ai_reviewer.review_diff_with_retry",
        lambda *a, **k: AIReviewResult(summary="No additional issues found.", comments=[]),
    )

    report = run_review_pipeline(patch_text=RISKY_PATCH, persist=False)

    assert report.blocked is False
    security_findings = [f for f in report.combined_findings if f.source == "static:security_rules"]
    assert security_findings, "expected the shell=True heuristic to fire"
    assert security_findings[0].rule_id == "shell_true_subprocess"

    ruff_result = next(r for r in report.check_results if r.tool == "ruff")
    bandit_result = next(r for r in report.check_results if r.tool == "bandit")
    assert ruff_result.ran is False and ruff_result.skipped_reason == "no repo checkout available"
    assert bandit_result.ran is False and bandit_result.skipped_reason == "no repo checkout available"


def test_repo_path_static_finding_message_carries_secret_to_ai_call(monkeypatch, tmp_path):
    """Characterizes a KNOWN CURRENT GAP, not desired behavior: for a
    --repo-path submission, on-disk redaction only ever runs for disposable
    temp clones (see secrets_scanner.redact_files_on_disk / pipeline.py),
    never for a user-supplied repo_path. If a static tool's own finding
    message happens to contain a secret (e.g. bandit quoting a hardcoded
    password literal), nothing in the pipeline redacts that message before
    it is handed to the AI reviewer or placed in the final report.

    This test pins today's behavior by injecting a fake CheckResult whose
    message contains a dummy secret and asserting it survives, unredacted,
    into both the AI reviewer's input and the final combined findings. It
    should start FAILING once that leak is fixed -- at that point, update
    or delete this test rather than leaving it green by accident.
    """
    repo_path, base_sha, head_sha = _init_git_repo(tmp_path)

    fake_secret = "hunter2_fake_password"
    leaking_result = CheckResult(
        tool="bandit",
        ran=True,
        findings=[
            CheckFinding(
                tool="bandit",
                path="app.py",
                line=2,
                severity=Severity.CRITICAL,
                message=f"Possible hardcoded password: '{fake_secret}'",
                rule_id="B105",
            )
        ],
    )
    monkeypatch.setattr(
        "repo_diff_pipeline.pipeline.checks.run_all_checks",
        lambda *a, **k: [leaking_result],
    )

    captured = {}

    def fake_ai_review(diff, check_results, *a, **k):
        captured["check_results"] = check_results
        return AIReviewResult(summary="stub", comments=[])

    monkeypatch.setattr("repo_diff_pipeline.pipeline.ai_reviewer.review_diff_with_retry", fake_ai_review)

    report = run_review_pipeline(
        repo_path=repo_path, base_ref=base_sha, head_ref=head_sha, persist=False
    )

    assert report.blocked is False
    # Reached the AI reviewer's input unredacted:
    assert fake_secret in captured["check_results"][0].findings[0].message
    # And made it into the final report:
    assert any(fake_secret in f.message for f in report.combined_findings)
