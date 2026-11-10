"""
Tests for 'pull_request' submissions on /api/submission: fetching a PR's
diff through the user's stored GitHub token and running it through the
same ingestion/parsing/analysis pipeline as a pasted patch/diff. All
GitHub and git-clone calls are monkeypatched — no real network or git.
"""
from contextlib import contextmanager
from pathlib import Path

from bson import ObjectId

from app.db import db
from app.routes import github as github_routes
from repo_diff_pipeline import github_client, repository

PYTHON_DIFF = (
    "diff --git a/hello.py b/hello.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/hello.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def hello():\n"
    "+    return 'hi'\n"
)

PR_URL = 'https://github.com/alice/project/pull/42'


def _connect_github(client, auth, monkeypatch):
    monkeypatch.setattr(github_routes.github_client, 'check_github_connected', lambda token: True)
    client.post('/api/github/connect', json={'token': 'ghp_good'}, headers=auth['headers'])


def _stub_pr_fetch(monkeypatch, diff_text=PYTHON_DIFF):
    def fake_fetch_pr_metadata(pr_ref, token):
        pr_ref.base_sha = 'base-sha'
        pr_ref.head_sha = 'head-sha'
        return pr_ref

    @contextmanager
    def fake_ingest_remote_repo(url, ref='HEAD'):
        yield repository.RepoHandle(path=Path('/tmp/fake-repo'), source=url)

    monkeypatch.setattr(github_client, 'fetch_pr_metadata', fake_fetch_pr_metadata)
    monkeypatch.setattr(github_client, 'fetch_pr_diff', lambda pr_ref, token: diff_text)
    monkeypatch.setattr(repository, 'ingest_remote_repo', fake_ingest_remote_repo)


def test_pull_request_submission_requires_github_connection(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'pull_request', 'pr_url': PR_URL},
        headers=auth['headers'],
    )
    assert resp.status_code == 400
    assert 'not connected' in resp.get_json()['error'].lower()


def test_pull_request_submission_requires_pr_url(client, auth, monkeypatch):
    _connect_github(client, auth, monkeypatch)

    resp = client.post(
        '/api/submission',
        json={'submission_type': 'pull_request'},
        headers=auth['headers'],
    )
    assert resp.status_code == 400


def test_pull_request_submission_is_parsed_via_stored_token(client, auth, monkeypatch):
    _connect_github(client, auth, monkeypatch)
    _stub_pr_fetch(monkeypatch)

    resp = client.post(
        '/api/submission',
        json={'submission_type': 'pull_request', 'pr_url': PR_URL},
        headers=auth['headers'],
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['status'] == 'parsed'
    assert data['submission_type'] == 'pull_request'
    assert data['filename'] == PR_URL
    assert data['review']['review_status'] == 'completed'

    stored = db['submission'].find_one({'_id': ObjectId(data['_id'])})
    assert stored['content'] is None


def test_pull_request_fetch_failure_blocks_submission(client, auth, monkeypatch):
    _connect_github(client, auth, monkeypatch)

    def fake_fetch_pr_metadata(pr_ref, token):
        raise github_client.GitHubError('Failed to fetch PR metadata (404): not found')

    monkeypatch.setattr(github_client, 'fetch_pr_metadata', fake_fetch_pr_metadata)

    resp = client.post(
        '/api/submission',
        json={'submission_type': 'pull_request', 'pr_url': PR_URL},
        headers=auth['headers'],
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert 'Failed to fetch PR metadata' in data['error']
    assert data['submission']['status'] == 'blocked'
