"""Tests for ingestion.py: dispatch on submission type, using real local repos and a mocked GitHub."""
import os
import subprocess
from contextlib import contextmanager

import pytest

from repo_diff_pipeline import ingestion
from repo_diff_pipeline.models import InputType
from repo_diff_pipeline.repository import RepoHandle


def test_patch_text_ingested_without_repo():
    with ingestion.ingest_submission(patch_text='diff --git a b') as r:
        assert r.input_type is InputType.PATCH_FILE
        assert r.repo_path is None and not r.is_temp_checkout
        assert r.diff_text == 'diff --git a b'


@pytest.mark.parametrize('text', ['', '   \n'])
def test_empty_patch_rejected(text):
    with pytest.raises(ingestion.IngestionError, match='empty'):
        with ingestion.ingest_submission(patch_text=text):
            pass


def test_no_source_rejected():
    with pytest.raises(ingestion.IngestionError, match='No submission'):
        with ingestion.ingest_submission():
            pass


def test_git_diff_requires_refs():
    with pytest.raises(ingestion.IngestionError, match='base_ref and head_ref'):
        with ingestion.ingest_submission(repo_path='.', base_ref='a'):
            pass


def test_local_repo_diff_is_not_temp_checkout(tmp_path):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e.x',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e.x'}
    run = lambda *a: subprocess.run(['git', *a], cwd=tmp_path, env=env, check=True, capture_output=True)
    run('init', '-q', '-b', 'main')
    (tmp_path / 'a.py').write_text('1\n')
    run('add', '.'); run('commit', '-qm', 'a')
    run('checkout', '-qb', 'f')
    (tmp_path / 'a.py').write_text('2\n')
    run('commit', '-qam', 'b')
    with ingestion.ingest_submission(repo_path=str(tmp_path), base_ref='main', head_ref='f') as r:
        assert r.input_type is InputType.GIT_DIFF
        assert r.is_temp_checkout is False
        assert '+2' in r.diff_text
        assert (r.base_ref, r.head_ref) == ('main', 'f')


def test_remote_repo_is_temp_checkout(monkeypatch, tmp_path):
    @contextmanager
    def fake_remote(url, ref):
        yield RepoHandle(path=tmp_path, source=url)

    monkeypatch.setattr(ingestion.repository, 'ingest_remote_repo', fake_remote)
    monkeypatch.setattr(ingestion.repository, 'get_diff_text', lambda *a: 'D')
    with ingestion.ingest_submission(repo_url='https://x/y.git', base_ref='a', head_ref='b') as r:
        assert r.is_temp_checkout is True and r.diff_text == 'D' and r.repo_ref == 'https://x/y.git'


def test_pr_requires_connected_github(monkeypatch):
    monkeypatch.setattr(ingestion.github_client, 'check_github_connected', lambda t: False)
    with pytest.raises(ingestion.IngestionError, match='not connected'):
        with ingestion.ingest_submission(pr_url='https://github.com/o/r/pull/1', github_token=None):
            pass


def test_pr_ingestion_happy_path(monkeypatch, tmp_path):
    gc = ingestion.github_client
    monkeypatch.setattr(gc, 'check_github_connected', lambda t: True)

    def meta(ref, token):
        ref.base_sha, ref.head_sha = 'BASE', 'HEAD'
        return ref

    monkeypatch.setattr(gc, 'fetch_pr_metadata', meta)
    monkeypatch.setattr(gc, 'fetch_pr_diff', lambda ref, token: 'PRDIFF')
    seen = {}

    @contextmanager
    def fake_remote(url, ref):
        seen.update(url=url, ref=ref)
        yield RepoHandle(path=tmp_path, source=url)

    monkeypatch.setattr(ingestion.repository, 'ingest_remote_repo', fake_remote)
    with ingestion.ingest_submission(pr_url='https://github.com/o/r/pull/7', github_token='t') as r:
        assert r.input_type is InputType.PULL_REQUEST
        assert r.repo_ref == 'o/r#7' and r.diff_text == 'PRDIFF'
        assert (r.base_ref, r.head_ref) == ('BASE', 'HEAD')
        assert r.is_temp_checkout and r.metadata == {'pr_number': 7, 'owner': 'o', 'repo': 'r'}
    assert seen == {'url': 'https://github.com/o/r.git', 'ref': 'HEAD'}
