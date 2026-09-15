"""
Safe repository ingestion.

Design goals:
- Never execute anything from the submitted repo's content (hooks, scripts).
- Restrict what can be fetched (URL scheme allowlist, no local file:// paths
  unless explicitly operating on an already-trusted local path).
- Always use isolated temp directories that get cleaned up.
- Bound network operations with timeouts and shallow clones.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_URL_SCHEMES = {"https", "git", "ssh"}
CLONE_TIMEOUT_SECONDS = 60
GIT_ENV = {
    # Prevent git from running any repo-provided hooks or opening prompts.
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ALLOW_PROTOCOL": "https:git:ssh",
}


class RepositoryError(Exception):
    pass


class UnsafeSourceError(RepositoryError):
    pass


@dataclass
class RepoHandle:
    path: Path
    source: str


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise UnsafeSourceError(
            f"Refusing to clone URL with scheme '{parsed.scheme}'. "
            f"Allowed: {sorted(ALLOWED_URL_SCHEMES)}"
        )
    if not parsed.netloc:
        raise UnsafeSourceError("Refusing to clone URL with no host component.")


_GIT_BIN = shutil.which("git") or "git"


def _run_git(args: list[str], cwd: Path | None = None, timeout: int = CLONE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [_GIT_BIN, *args],
            cwd=cwd,
            env=GIT_ENV,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RepositoryError(f"git {' '.join(args)} failed: {e.stderr.strip()}") from e
    except subprocess.TimeoutExpired as e:
        raise RepositoryError(f"git {' '.join(args)} timed out after {timeout}s") from e


@contextmanager
def ingest_remote_repo(url: str, ref: str = "HEAD") -> Iterator[RepoHandle]:
    """
    Shallow-clone a remote repo into an isolated temp dir for inspection.
    The directory (and everything under it) is deleted on exit regardless
    of success or failure.
    """
    _validate_remote_url(url)
    tmp_dir = Path(tempfile.mkdtemp(prefix="submission_repo_"))
    try:
        _run_git(["clone", "--no-hardlinks", "--depth", "1", "--branch", ref, url, str(tmp_dir)])
    except RepositoryError:
        # Some refs (arbitrary commit SHAs) aren't branch names; fall back to
        # a full-ish fetch of just that ref.
        _run_git(["init", str(tmp_dir)], cwd=None)
        _run_git(["remote", "add", "origin", url], cwd=tmp_dir)
        _run_git(["fetch", "--depth", "1", "origin", ref], cwd=tmp_dir)
        _run_git(["checkout", "FETCH_HEAD"], cwd=tmp_dir)
    try:
        yield RepoHandle(path=tmp_dir, source=url)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@contextmanager
def ingest_local_repo(path: str) -> Iterator[RepoHandle]:
    """
    Use an already-trusted local path (e.g. a repo your platform already
    manages) without cloning. Caller is responsible for that path's trust
    level — this function does not copy or sandbox it further.
    """
    p = Path(path).resolve()
    if not p.is_dir():
        raise RepositoryError(f"Local path does not exist or is not a directory: {p}")
    if not (p / ".git").exists():
        raise RepositoryError(f"Not a git repository: {p}")
    yield RepoHandle(path=p, source=str(p))


def get_diff_text(repo: RepoHandle, base_ref: str, head_ref: str) -> str:
    """Return the unified diff text between two refs in the given repo."""
    result = _run_git(
        ["diff", "--unified=3", "--no-color", f"{base_ref}...{head_ref}"],
        cwd=repo.path,
        timeout=30,
    )
    return result.stdout


def list_changed_files(repo: RepoHandle, base_ref: str, head_ref: str) -> list[str]:
    result = _run_git(
        ["diff", "--name-only", f"{base_ref}...{head_ref}"],
        cwd=repo.path,
        timeout=30,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]
