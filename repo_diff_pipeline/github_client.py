"""
Minimal GitHub REST client for the "GitHub Repository Handling" branch:
check that a token is configured ("GitHub Connected?"), and fetch a pull
request's diff text without needing a local clone.

Only ever performs read requests (GET). Never executes anything returned
by the API.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT = 15

PR_URL_RE = re.compile(
    r"github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)"
)


class GitHubError(Exception):
    pass


class GitHubNotConnectedError(GitHubError):
    """Raised when no valid token is configured — maps to 'GitHub Connected? No'."""


@dataclass
class PullRequestRef:
    owner: str
    repo: str
    number: int
    base_sha: str | None = None
    head_sha: str | None = None


def parse_pr_url(pr_url: str) -> PullRequestRef:
    match = PR_URL_RE.search(pr_url)
    if not match:
        raise GitHubError(f"Could not parse a GitHub PR reference from: {pr_url}")
    return PullRequestRef(
        owner=match.group("owner"), repo=match.group("repo"), number=int(match.group("number"))
    )


def check_github_connected(token: str | None) -> bool:
    """Corresponds to the 'GitHub Connected?' decision node."""
    if not token:
        return False
    try:
        resp = requests.get(
            f"{GITHUB_API}/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=REQUEST_TIMEOUT,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def fetch_pr_metadata(pr_ref: PullRequestRef, token: str) -> PullRequestRef:
    resp = requests.get(
        f"{GITHUB_API}/repos/{pr_ref.owner}/{pr_ref.repo}/pulls/{pr_ref.number}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise GitHubError(f"Failed to fetch PR metadata ({resp.status_code}): {resp.text[:200]}")
    data = resp.json()
    pr_ref.base_sha = data["base"]["sha"]
    pr_ref.head_sha = data["head"]["sha"]
    return pr_ref


def fetch_pr_diff(pr_ref: PullRequestRef, token: str) -> str:
    """Corresponds to 'Receive PR Code changes'."""
    resp = requests.get(
        f"{GITHUB_API}/repos/{pr_ref.owner}/{pr_ref.repo}/pulls/{pr_ref.number}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3.diff"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise GitHubError(f"Failed to fetch PR diff ({resp.status_code}): {resp.text[:200]}")
    return resp.text


def clone_url_for_pr(pr_ref: PullRequestRef) -> str:
    return f"https://github.com/{pr_ref.owner}/{pr_ref.repo}.git"
