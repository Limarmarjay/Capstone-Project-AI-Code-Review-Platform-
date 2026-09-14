"""
'Diff/Patch/PR Ingestion' section of the workflow: dispatches on submission
type (pull request vs. text patch file vs. plain git diff) and produces a
uniform IngestionResult for the rest of the pipeline.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import github_client, repository
from .models import InputType


class IngestionError(Exception):
    """Maps to the 'Display "Invalid input"' / connection-failure paths."""


@dataclass
class IngestionResult:
    input_type: InputType
    diff_text: str
    repo_ref: str
    base_ref: str
    head_ref: str
    repo_path: str | None = None  # None if no on-disk checkout (patch-only submissions)
    # True only for throwaway temp clones we created and will delete. False for
    # a user-supplied --repo-path, which is a real working tree we must not modify.
    is_temp_checkout: bool = False
    metadata: dict = field(default_factory=dict)


@contextmanager
def ingest_submission(
    *,
    pr_url: str | None = None,
    patch_text: str | None = None,
    repo_url: str | None = None,
    repo_path: str | None = None,
    base_ref: str | None = None,
    head_ref: str | None = None,
    github_token: str | None = None,
) -> Iterator[IngestionResult]:
    """
    Exactly one of (pr_url), (patch_text), or (repo_url/repo_path + refs)
    should be provided — this mirrors the "Pull Request? / Patch file? /
    git diff" branch in the flowchart. Yields an IngestionResult; cleans up
    any temp checkout on exit.
    """
    if pr_url:
        yield from _ingest_pull_request(pr_url, github_token)
    elif patch_text is not None:
        yield _ingest_patch_file(patch_text)
    elif repo_url or repo_path:
        yield from _ingest_git_diff(repo_url, repo_path, base_ref, head_ref)
    else:
        raise IngestionError(
            "No submission provided: pass pr_url, patch_text, or repo_url/repo_path+refs."
        )


def _ingest_patch_file(patch_text: str) -> IngestionResult:
    """'Receive Text-based patch file' — no repo context, checks run in a reduced mode."""
    if not patch_text.strip():
        raise IngestionError("Patch file is empty.")
    return IngestionResult(
        input_type=InputType.PATCH_FILE,
        diff_text=patch_text,
        repo_ref="<patch-file>",
        base_ref="<unknown>",
        head_ref="<unknown>",
        repo_path=None,
        metadata={"note": "no on-disk repo; static checks limited to diff content"},
    )


def _ingest_git_diff(
    repo_url: str | None, repo_path: str | None, base_ref: str | None, head_ref: str | None
) -> Iterator[IngestionResult]:
    """'Receive Git Diff' branch."""
    if not base_ref or not head_ref:
        raise IngestionError("base_ref and head_ref are required for git-diff submissions.")

    ctx = repository.ingest_remote_repo(repo_url, ref=head_ref) if repo_url else repository.ingest_local_repo(repo_path)
    with ctx as repo:
        diff_text = repository.get_diff_text(repo, base_ref, head_ref)
        yield IngestionResult(
            input_type=InputType.GIT_DIFF,
            diff_text=diff_text,
            repo_ref=repo.source,
            base_ref=base_ref,
            head_ref=head_ref,
            repo_path=str(repo.path),
            is_temp_checkout=bool(repo_url),  # only remote clones are disposable
        )


def _ingest_pull_request(pr_url: str, github_token: str | None) -> Iterator[IngestionResult]:
    """'GitHub Repository Handling' branch: connected check -> fetch PR diff -> checkout for static checks."""
    if not github_client.check_github_connected(github_token):
        raise IngestionError(
            "GitHub is not connected: no valid token configured. "
            "Set a GITHUB_TOKEN with repo read access and retry."
        )

    pr_ref = github_client.parse_pr_url(pr_url)
    pr_ref = github_client.fetch_pr_metadata(pr_ref, github_token)
    diff_text = github_client.fetch_pr_diff(pr_ref, github_token)
    clone_url = github_client.clone_url_for_pr(pr_ref)

    # Check out the PR head so static checks have real files to inspect.
    with repository.ingest_remote_repo(clone_url, ref=pr_ref.head_sha) as repo:
        yield IngestionResult(
            input_type=InputType.PULL_REQUEST,
            diff_text=diff_text,
            repo_ref=f"{pr_ref.owner}/{pr_ref.repo}#{pr_ref.number}",
            base_ref=pr_ref.base_sha or "base",
            head_ref=pr_ref.head_sha or "head",
            repo_path=str(repo.path),
            is_temp_checkout=True,
            metadata={"pr_number": pr_ref.number, "owner": pr_ref.owner, "repo": pr_ref.repo},
        )
