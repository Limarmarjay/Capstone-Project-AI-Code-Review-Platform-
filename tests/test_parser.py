# Run from the project's top folder with:   python3 -m pytest tests/test_parser.py -v
# Save evidence with:                        python3 -m pytest tests/test_parser.py -v > test_results.txt

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repo_diff_pipeline import github_client
from repo_diff_pipeline.cli import _json_default
from repo_diff_pipeline.diff_parser import DiffParseError, parse_diff
from repo_diff_pipeline.ingestion import ingest_submission
from repo_diff_pipeline.models import ChangeType
from repo_diff_pipeline.validation import (
    MAX_RAW_DIFF_BYTES,
    validate_input,
    validate_python_only,
)

FIXTURES = Path(__file__).parent / "fixtures"


class UploadRejected(Exception):
    """The pipeline's rejection reason (validate_* return a string, they don't raise)."""


def to_json(parsed):
    """ParsedDiff -> hand-off JSON string, exactly as the CLI/storage serialize it."""
    return json.dumps(parsed, default=_json_default)


def handle_upload(path):
    """Uploaded file -> ingestion -> validation -> parser -> JSON, as /api/submission does."""
    with ingest_submission(patch_text=Path(path).read_text()) as ingested:
        reason = validate_input(ingested.diff_text)
        if reason:
            raise UploadRejected(reason)
        try:
            parsed = parse_diff(ingested.diff_text)
        except DiffParseError as e:
            raise UploadRejected(str(e)) from e
        reason = validate_python_only(parsed.files)
        if reason:
            raise UploadRejected(reason)
        return to_json(parsed)


def _pr_files_to_diff(api_response):
    """Rebuild the unified diff GitHub serves for a PR from its 'PR files' JSON."""
    return "".join(
        f"diff --git a/{f['filename']} b/{f['filename']}\n"
        f"--- a/{f['filename']}\n+++ b/{f['filename']}\n{f['patch']}\n"
        for f in api_response
    )


def _fake_github_diff(monkeypatch, diff_text):
    class _Resp:
        status_code = 200
        text = diff_text

    monkeypatch.setattr(github_client.requests, "get", lambda *_args, **_kwargs: _Resp())

# UNIT TESTS

def test_UT1_parser_extracts_two_files():
    """UT1 (FR1): diff parser returns 2 files with correct line-level detail."""
    parsed = parse_diff((FIXTURES / "two_files.diff").read_text())

    assert [f.path for f in parsed.files] == ["app.py", "utils/helpers.py"]
    app = parsed.files[0]
    assert app.change_type == ChangeType.MODIFIED
    assert (app.additions, app.deletions) == (3, 1)
    hunk = app.hunks[0]
    assert (hunk.source_start, hunk.target_start) == (1, 1)
    assert [l.target_line_no for l in hunk.added_lines] == [2, 3, 4]
    assert [l.source_line_no for l in hunk.removed_lines] == [2]


def test_UT2_github_pr_parser_matches_upload_format(monkeypatch):
    """UT2 (FR1): a (mocked) GitHub PR response parses to the same structure as an upload."""
    api_response = json.loads((FIXTURES / "pr_response.json").read_text())
    _fake_github_diff(monkeypatch, _pr_files_to_diff(api_response))

    pr_ref = github_client.parse_pr_url("https://github.com/alice/project/pull/42")
    result = parse_diff(github_client.fetch_pr_diff(pr_ref, "ghp_test"))

    assert [f.path for f in result.files] == ["app.py", "utils/helpers.py"]
    assert (result.files[0].additions, result.files[0].deletions) == (3, 1)
    # same structure as the upload path, so both feed the same hand-off
    assert result == parse_diff((FIXTURES / "two_files.diff").read_text())


def test_UT3_json_output_matches_handoff_format():
    """UT3 (FR4): parsed diff becomes valid JSON in the hand-off format."""
    data = json.loads(to_json(parse_diff((FIXTURES / "two_files.diff").read_text())))

    assert [f["path"] for f in data["files"]] == ["app.py", "utils/helpers.py"]
    first = data["files"][0]
    assert first["change_type"] == "modified"
    hunk = first["hunks"][0]
    assert {"source_start", "source_length", "target_start", "target_length", "lines"} <= hunk.keys()
    assert {"line_type", "content", "source_line_no", "target_line_no"} <= hunk["lines"][0].keys()
    assert [l["line_type"] for l in hunk["lines"]].count("add") == 3


# INTEGRATION TESTS

def test_IT1_patch_upload_to_json(tmp_path):
    """IT1 (FR1, FR4): .patch upload flows through validation -> parser -> JSON."""
    upload = tmp_path / "changes.patch"
    shutil.copy(FIXTURES / "two_files.diff", upload)

    data = json.loads(handle_upload(upload))

    assert [f["path"] for f in data["files"]] == ["app.py", "utils/helpers.py"]


TEST_TOKEN = "ghp_test_token"
TEST_PR_URL = "https://github.com/alice/project/pull/42"


class _FakeGitHubResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _fake_github_api(monkeypatch, diff_text):
    """Mock GitHub: accepts only TEST_TOKEN, serves the PR diff. Records each request."""
    calls = []

    def fake_get(url, headers, **_kwargs):
        calls.append((url, headers))
        if headers.get("Authorization") != f"Bearer {TEST_TOKEN}":
            return _FakeGitHubResponse(401, "Bad credentials")
        if url.endswith("/user"):
            return _FakeGitHubResponse(200, "{}")
        if url.endswith("/pulls/42"):
            return _FakeGitHubResponse(200, diff_text)
        return _FakeGitHubResponse(404, "Not Found")

    monkeypatch.setattr(github_client.requests, "get", fake_get)
    return calls


def test_IT2_github_pr_to_json(monkeypatch):
    """IT2 (FR1, FR4): token -> (mocked) GitHub auth -> PR retrieval -> parser -> JSON."""
    diff_text = (FIXTURES / "two_files.diff").read_text()
    calls = _fake_github_api(monkeypatch, diff_text)

    # Authentication succeeds with the test token and fails with a wrong one
    assert github_client.check_github_connected(TEST_TOKEN)
    assert not github_client.check_github_connected("ghp_wrong_token")

    # Retrieval -> parser -> JSON
    pr_ref = github_client.parse_pr_url(TEST_PR_URL)
    data = json.loads(to_json(parse_diff(github_client.fetch_pr_diff(pr_ref, TEST_TOKEN))))

    assert [f["path"] for f in data["files"]] == ["app.py", "utils/helpers.py"]
    assert (data["files"][0]["hunks"][0]["lines"][0]["line_type"]) in ("context", "add", "del")
    # every request went to the GitHub API with the token; nothing hit the network
    assert all(url.startswith(github_client.GITHUB_API) for url, _ in calls)


# NEGATIVE EDGE CASE TESTS

def test_NE1_rejects_oversized_file(tmp_path):
    """NE1 (FR2): an 11 MB .diff is rejected before parsing, with the limit in the error.

    NOTE: the code's limit is MAX_RAW_DIFF_BYTES (5 MB), reported in bytes — not the
    10 MB stated in FR2. This test asserts what the code does today.
    """
    big_file = tmp_path / "over_10mb.diff"
    header = (FIXTURES / "two_files.diff").read_text()
    big_file.write_text(header + "+# padding line\n" * (11 * 1024 * 1024 // 16))
    assert big_file.stat().st_size > 10 * 1024 * 1024  # sanity check

    with pytest.raises(UploadRejected) as err:
        handle_upload(big_file)

    assert "exceeds maximum size" in str(err.value)
    assert str(MAX_RAW_DIFF_BYTES) in str(err.value)


def test_NE2_rejects_non_python_diff(tmp_path):
    """NE2 (FR3): a diff that only changes a non-.py file is rejected."""
    upload = tmp_path / "non_python.diff"
    shutil.copy(FIXTURES / "non_python.diff", upload)

    with pytest.raises(UploadRejected) as err:
        handle_upload(upload)

    assert "Only Python (.py) files are supported" in str(err.value)
    assert "index.js" in str(err.value)
