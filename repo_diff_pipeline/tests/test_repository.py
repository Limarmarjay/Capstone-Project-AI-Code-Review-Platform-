"""Tests for repository.py. Uses real throwaway git repos on disk; no network."""
import os
import subprocess
from pathlib import Path

import pytest

from repo_diff_pipeline import repository as repo


def _git(cwd, *args):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e.x',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e.x'}
    subprocess.run(['git', *args], cwd=cwd, env=env, check=True, capture_output=True)


@pytest.fixture()
def git_repo(tmp_path):
    root = tmp_path / 'r'
    root.mkdir()
    _git(root, 'init', '-q', '-b', 'main')
    (root / 'a.py').write_text('x = 1\n')
    _git(root, 'add', '.')
    _git(root, 'commit', '-qm', 'base')
    _git(root, 'checkout', '-qb', 'feature')
    (root / 'a.py').write_text('x = 2\n')
    (root / 'b.py').write_text('y = 1\n')
    _git(root, 'add', '.')
    _git(root, 'commit', '-qm', 'change')
    return root


# --- URL validation -------------------------------------------------------------

@pytest.mark.parametrize('url', ['git://host/r'])
def test_allowed_urls(url):
    repo._validate_remote_url(url)


@pytest.mark.parametrize('url', ['ext::sh -c id', 'http://h/r'])
def test_disallowed_url_schemes(url):
    with pytest.raises(repo.UnsafeSourceError):
        repo._validate_remote_url(url)


def test_url_without_host_rejected():
    with pytest.raises(repo.UnsafeSourceError, match='no host'):
        repo._validate_remote_url('https://')


def test_unsafe_error_is_repository_error():
    assert issubclass(repo.UnsafeSourceError, repo.RepositoryError)


# --- _run_git --------------------------------------------------------------------

def test_run_git_success(git_repo):
    assert repo._run_git(['rev-parse', '--abbrev-ref', 'HEAD'], cwd=git_repo).stdout.strip() == 'feature'


def test_run_git_failure_wraps_stderr(tmp_path):
    with pytest.raises(repo.RepositoryError, match='failed'):
        repo._run_git(['rev-parse', 'HEAD'], cwd=tmp_path)


def test_run_git_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired('git', 1)

    monkeypatch.setattr(repo.subprocess, 'run', boom)
    with pytest.raises(repo.RepositoryError, match='timed out'):
        repo._run_git(['status'], timeout=1)


def test_run_git_uses_restricted_env(monkeypatch):
    seen = {}
    monkeypatch.setattr(repo.subprocess, 'run', lambda *a, **k: seen.update(k))
    repo._run_git(['status'])
    assert seen['env']['GIT_TERMINAL_PROMPT'] == '0'
    assert seen['env']['GIT_ALLOW_PROTOCOL'] == 'https:git:ssh'


# --- local repo -------------------------------------------------------------------

def test_ingest_local_repo_yields_handle(git_repo):
    with repo.ingest_local_repo(str(git_repo)) as handle:
        assert handle.path == git_repo.resolve()
    assert git_repo.exists()  # local repos are never deleted


def test_ingest_local_repo_missing_dir(tmp_path):
    with pytest.raises(repo.RepositoryError, match='not a directory'):
        with repo.ingest_local_repo(str(tmp_path / 'nope')):
            pass


def test_ingest_local_repo_not_git(tmp_path):
    with pytest.raises(repo.RepositoryError, match='Not a git repository'):
        with repo.ingest_local_repo(str(tmp_path)):
            pass


def test_get_diff_text_and_changed_files(git_repo):
    handle = repo.RepoHandle(path=git_repo, source=str(git_repo))
    diff = repo.get_diff_text(handle, 'main', 'feature')
    assert '+x = 2' in diff and 'b.py' in diff
    assert repo.list_changed_files(handle, 'main', 'feature') == ['a.py', 'b.py']


def test_diff_with_bad_ref_raises(git_repo):
    handle = repo.RepoHandle(path=git_repo, source='s')
    with pytest.raises(repo.RepositoryError):
        repo.get_diff_text(handle, 'main', 'does-not-exist')


# --- remote clone (against a local bare "remote", git calls intercepted for scheme) --

def test_remote_rejects_unsafe_url_before_touching_disk(monkeypatch):
    monkeypatch.setattr(repo.tempfile, 'mkdtemp', lambda **k: pytest.fail('must validate first'))
    with pytest.raises(repo.UnsafeSourceError):
        with repo.ingest_remote_repo('file:///etc'):
            pass


def test_remote_clone_success_and_cleanup(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(repo, '_run_git', lambda args, **k: calls.append(args))
    with repo.ingest_remote_repo('https://example.com/r.git', ref='main') as handle:
        path = handle.path
        assert path.exists() and handle.source == 'https://example.com/r.git'
    assert not path.exists()
    assert calls[0][:4] == ['clone', '--no-hardlinks', '--depth', '1'] and 'main' in calls[0]


def test_remote_clone_falls_back_to_fetch_for_sha(monkeypatch):
    calls = []

    def fake(args, cwd=None, timeout=0):
        calls.append(args[0])
        if args[0] == 'clone':
            raise repo.RepositoryError('not a branch')

    monkeypatch.setattr(repo, '_run_git', fake)
    with repo.ingest_remote_repo('https://example.com/r.git', ref='abc123'):
        pass
    assert calls == ['clone', 'init', 'remote', 'fetch', 'checkout']


def test_remote_cleans_up_even_if_body_raises(monkeypatch):
    monkeypatch.setattr(repo, '_run_git', lambda *a, **k: None)
    holder = {}
    with pytest.raises(ValueError):
        with repo.ingest_remote_repo('https://example.com/r.git') as handle:
            holder['p'] = handle.path
            raise ValueError('boom')
    assert not holder['p'].exists()
