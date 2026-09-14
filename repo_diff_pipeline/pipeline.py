from __future__ import annotations

import logging

from . import (
    ai_reviewer,
    checks,
    ingestion,
    report,
    secrets_scanner,
    storage,
    validation,
)
from .diff_parser import DiffParseError, parse_diff
from .models import ParsedDiff, SubmissionReport

log = logging.getLogger(__name__)

MAX_REASONABLE_CHANGED_LINES = 5_000


def run_review_pipeline(
    *,
    pr_url: str | None = None,
    patch_text: str | None = None,
    repo_url: str | None = None,
    repo_path: str | None = None,
    base_ref: str | None = None,
    head_ref: str | None = None,
    github_token: str | None = None,
    run_ai_review: bool = True,
    db_path: str = storage.DEFAULT_DB_PATH,
    persist: bool = True,
) -> SubmissionReport:
    """
    Runs the complete workflow end to end. Provide exactly one submission
    source: pr_url, patch_text, or repo_url/repo_path (+ base_ref/head_ref).
    """
    # --- Diff/Patch/PR Ingestion ---
    try:
        with ingestion.ingest_submission(
            pr_url=pr_url,
            patch_text=patch_text,
            repo_url=repo_url,
            repo_path=repo_path,
            base_ref=base_ref,
            head_ref=head_ref,
            github_token=github_token,
        ) as ingested:
            return _run_from_ingested(ingested, run_ai_review, db_path, persist)
    except ingestion.IngestionError as e:
        return _blocked_report("<unknown>", base_ref or "", head_ref or "", str(e))


def _run_from_ingested(ingested: ingestion.IngestionResult, run_ai_review: bool, db_path: str, persist: bool) -> SubmissionReport:
    # --- Input Validation ---
    invalid_reason = validation.validate_input(ingested.diff_text)
    if invalid_reason:
        return _blocked_report(
            ingested.repo_ref, ingested.base_ref, ingested.head_ref,
            f'Invalid input: {invalid_reason}', input_type=ingested.input_type,
        )

    # --- Diff Parsing ---
    try:
        parsed_diff: ParsedDiff = parse_diff(ingested.diff_text)
    except DiffParseError as e:
        return _blocked_report(
            ingested.repo_ref, ingested.base_ref, ingested.head_ref, str(e), input_type=ingested.input_type,
        )

    total_changed = parsed_diff.total_additions + parsed_diff.total_deletions
    if total_changed > MAX_REASONABLE_CHANGED_LINES:
        return _blocked_report(
            ingested.repo_ref, ingested.base_ref, ingested.head_ref,
            f"Submission changes {total_changed} lines, exceeding the "
            f"{MAX_REASONABLE_CHANGED_LINES}-line review limit. Split it into smaller submissions.",
            diff=parsed_diff, input_type=ingested.input_type,
        )

    # --- Secrets Found? / Redact Secrets ---
    secret_findings = secrets_scanner.scan_and_redact(parsed_diff)
    if secret_findings:
        log.warning("Redacted %d potential secret(s) before further processing.", len(secret_findings))
        # On-disk redaction rewrites real files, so it only runs on disposable
        # temp clones (remote/PR submissions). A --repo-path checkout is the
        # user's own working tree and must never be mutated; there the secret
        # stays on disk and only the in-memory diff is redacted.
        if ingested.repo_path and ingested.is_temp_checkout:
            secrets_scanner.redact_files_on_disk(ingested.repo_path, parsed_diff, secret_findings)

    # --- Static Analysis & Security Checks ---
    check_results = checks.run_all_checks(ingested.repo_path, parsed_diff.files)

    # --- Run LLM Review / Valid LLM Response? (with retry) ---
    ai_review = None
    if run_ai_review:
        ai_review = ai_reviewer.review_diff_with_retry(parsed_diff, check_results)

    # --- Combine Findings / Assign Severity Levels ---
    combined = report.combine_findings(check_results, ai_review)

    # --- Generate Inline Comments / Generate Summary Report ---
    inline_comments = report.generate_inline_comments(combined)
    summary = report.generate_summary_report(combined, ai_review)

    submission_report = SubmissionReport(
        repo_ref=ingested.repo_ref,
        base_ref=ingested.base_ref,
        head_ref=ingested.head_ref,
        diff=parsed_diff,
        input_type=ingested.input_type,
        secrets_found=secret_findings,
        check_results=check_results,
        ai_review=ai_review,
        combined_findings=combined,
        inline_comments=inline_comments,
        summary_report=summary,
    )

    # --- Database Persistence ---
    if persist:
        submission_report.persisted_id = storage.save_report(submission_report, db_path)

    return submission_report


def _blocked_report(repo_ref, base_ref, head_ref, reason, diff: ParsedDiff | None = None, input_type=None) -> SubmissionReport:
    return SubmissionReport(
        repo_ref=repo_ref,
        base_ref=base_ref,
        head_ref=head_ref,
        diff=diff or ParsedDiff(files=[]),
        input_type=input_type,
        blocked=True,
        block_reason=reason,
    )
