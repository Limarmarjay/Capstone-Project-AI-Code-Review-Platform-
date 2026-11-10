from llm_review.client import LLMClient, MockLLMClient
from llm_review.pipeline import review
from repo_diff_pipeline import validation
from repo_diff_pipeline.diff_parser import DiffParseError, parse_diff
from repo_diff_pipeline.models import InputType, ParsedDiff

from .analysis_input import build_analysis_input
from .redactor import redact_text


MAX_CHANGED_LINES = 5_000

SOURCE_TYPES = {
    "patch": InputType.PATCH_FILE.value,
    "diff": InputType.GIT_DIFF.value,
    "pull_request": InputType.PULL_REQUEST.value,
}


class SubmissionAnalysisError(Exception):
    """Raised when an uploaded submission cannot be analyzed."""


class SanitizingClient:
    """Final redaction boundary immediately before an LLM call."""

    def __init__(self, client: LLMClient):
        self._client = client

    def complete(self, system: str, user: str) -> str:
        safe_user = redact_text(user)
        return self._client.complete(system, safe_user)


def analyze_parsed_submission(
    parsed_diff: ParsedDiff,
    *,
    submission_id: str,
    submission_type: str,
    filename: str,
    client: LLMClient | None = None,
) -> tuple[dict, dict[str, str]]:
    """
    Same as analyze_uploaded_submission, but for a diff that's already been
    validated and parsed by the caller (e.g. /api/submission) — skips
    re-running validate_input/parse_diff on the same content a second time.
    """
    if submission_type not in SOURCE_TYPES:
        raise SubmissionAnalysisError(
            f"Unsupported submission type: {submission_type}"
        )

    total_changed = (
        parsed_diff.total_additions
        + parsed_diff.total_deletions
    )

    if total_changed > MAX_CHANGED_LINES:
        raise SubmissionAnalysisError(
            "Submission exceeds the 5000-line review limit."
        )

    analysis_input, scan_issues = build_analysis_input(
        parsed_diff,
        None,
        submission_id=submission_id,
        source_type=SOURCE_TYPES[submission_type],
        repository=f"<upload:{filename}>",
    )

    safe_client = SanitizingClient(
        client or MockLLMClient()
    )

    review_output = review(
        analysis_input,
        client=safe_client,
    )

    return review_output, scan_issues


def analyze_uploaded_submission(
    content: str,
    *,
    submission_id: str,
    submission_type: str,
    filename: str,
    client: LLMClient | None = None,
) -> tuple[dict, dict[str, str]]:
    """Validates and parses raw diff/patch text, then analyzes it. Use
    analyze_parsed_submission directly if the caller already has a
    validated ParsedDiff, to avoid parsing the same content twice."""
    if submission_type not in SOURCE_TYPES:
        raise SubmissionAnalysisError(
            f"Unsupported submission type: {submission_type}"
        )

    invalid_reason = validation.validate_input(content)
    if invalid_reason:
        raise SubmissionAnalysisError(
            f"Invalid input: {invalid_reason}"
        )

    try:
        parsed_diff = parse_diff(content)
    except DiffParseError as exc:
        raise SubmissionAnalysisError(str(exc)) from exc

    return analyze_parsed_submission(
        parsed_diff,
        submission_id=submission_id,
        submission_type=submission_type,
        filename=filename,
        client=client,
    )