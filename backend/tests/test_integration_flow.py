"""
End-to-end integration tests through the real stack: Flask routes -> JWT auth ->
repo_diff_pipeline (ingest/validate/parse) -> Bandit (real subprocess) ->
llm_review (mock LLM) -> MongoDB. Only the LLM and GitHub are faked.
"""
from bson import ObjectId

from app.db import db

RISKY_DIFF = (
    "diff --git a/risky.py b/risky.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/risky.py\n"
    "@@ -0,0 +1,3 @@\n"
    "+import subprocess\n"
    "+def run(cmd):\n"
    "+    subprocess.call(cmd, shell=True)\n"
)

SECRET_DIFF = (
    "diff --git a/cfg.py b/cfg.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/cfg.py\n"
    "@@ -0,0 +1 @@\n"
    "+password = 'CANARY_7f3a9e_do_not_leak'\n"
)


def test_full_user_journey(client):
    # 1. sign up, then log in again with the same credentials
    signup = client.post('/api/auth/signup', json={'username': 'journey', 'password': 'password123'})
    assert signup.status_code == 201
    login = client.post('/api/auth/login', json={'username': 'journey', 'password': 'password123'})
    headers = {'Authorization': f"Bearer {login.get_json()['token']}"}
    assert client.get('/api/auth/me', headers=headers).get_json()['username'] == 'journey'

    # 2. submit a risky patch
    created = client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'filename': 'risky.patch', 'content': RISKY_DIFF, 'size': len(RISKY_DIFF)},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.get_json()
    assert body['status'] == 'parsed'
    assert body['files'] == [
        {'path': 'risky.py', 'change_type': 'added', 'additions': 3, 'deletions': 0, 'is_binary': False}
    ]

    # 3. the review contains the real Bandit finding, combined with the LLM finding
    findings = body['review']['findings']
    assert any(f['static_finding_ids'] and f['static_finding_ids'][0].startswith('bandit:') for f in findings)
    assert body['review']['overall_score'] < 100
    assert 'risky.py' in body['report']
    assert len(body['inline_comments']) == len(findings)

    # 4. everything was persisted and is retrievable only by its owner
    review = db['review'].find_one({'_id': ObjectId(body['review_id'])})
    assert review['status'] == 'completed' and review['submission_id'] == body['_id']
    assert review['total_findings'] == len(findings)
    assert sum(review['severity_counts'].values()) == len(findings)
    stored = list(db['findings'].find({'review_id': body['review_id']}))
    assert len(stored) == len(findings)
    assert all(doc['file'] == 'risky.py' and doc['body'] for doc in stored)

    fetched = client.get(f"/api/submission/{body['_id']}", headers=headers)
    assert fetched.status_code == 200 and fetched.get_json()['filename'] == 'risky.patch'
    assert [s['_id'] for s in client.get('/api/submission', headers=headers).get_json()] == [body['_id']]

    # 5. another user sees none of it
    other = client.post('/api/auth/signup', json={'username': 'intruder', 'password': 'password123'}).get_json()
    other_headers = {'Authorization': f"Bearer {other['token']}"}
    assert client.get('/api/submission', headers=other_headers).get_json() == []
    assert client.get(f"/api/submission/{body['_id']}", headers=other_headers).status_code == 404
    assert client.get(f"/api/review/{body['review_id']}", headers=other_headers).status_code == 404
    own_review = client.get(f"/api/review/{body['review_id']}", headers=headers)
    assert own_review.status_code == 200 and own_review.get_json()['status'] == 'completed'


def test_secret_in_submission_never_reaches_response_or_findings(client, auth):
    response = client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'content': SECRET_DIFF},
        headers=auth['headers'],
    )
    assert response.status_code == 201
    body = response.get_json()
    leaked_in_response = 'CANARY_7f3a9e_do_not_leak' in str(body['review']) + body['report'] + str(body['inline_comments'])
    assert not leaked_in_response
    stored = str(list(db['findings'].find({'review_id': body['review_id']})))
    assert 'CANARY_7f3a9e_do_not_leak' not in stored


def test_blocked_and_successful_submissions_coexist_in_listing(client, auth):
    ok = client.post('/api/submission', json={'submission_type': 'patch', 'content': RISKY_DIFF}, headers=auth['headers'])
    blocked = client.post('/api/submission', json={'submission_type': 'patch', 'content': 'plain prose'}, headers=auth['headers'])
    assert (ok.status_code, blocked.status_code) == (201, 400)

    listing = client.get('/api/submission', headers=auth['headers']).get_json()
    assert sorted(s['status'] for s in listing) == ['blocked', 'parsed']
    assert db['review'].count_documents({}) == 1  # only the parsed one started a review


def test_pull_request_journey_uses_stored_encrypted_token(client, auth, monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path

    from app.routes import github as github_routes
    from repo_diff_pipeline import github_client, repository

    monkeypatch.setattr(github_routes.github_client, 'check_github_connected', lambda t: True)
    assert client.post('/api/github/connect', json={'token': 'ghp_real_token'}, headers=auth['headers']).status_code in (200, 201)

    stored = db['GitHub_connection'].find_one({'user_id': auth['user']['id']})
    assert 'ghp_real_token' not in str(stored)  # encrypted at rest

    seen = {}
    monkeypatch.setattr(github_client, 'check_github_connected', lambda t: seen.setdefault('token', t) is not None)
    monkeypatch.setattr(github_client, 'fetch_pr_metadata', lambda ref, t: (setattr(ref, 'base_sha', 'b'), setattr(ref, 'head_sha', 'h'), ref)[2])
    monkeypatch.setattr(github_client, 'fetch_pr_diff', lambda ref, t: RISKY_DIFF)

    @contextmanager
    def fake_remote(url, ref='HEAD'):
        yield repository.RepoHandle(path=Path('/tmp/none'), source=url)

    monkeypatch.setattr(repository, 'ingest_remote_repo', fake_remote)

    response = client.post(
        '/api/submission',
        json={'submission_type': 'pull_request', 'pr_url': 'https://github.com/o/r/pull/9'},
        headers=auth['headers'],
    )
    assert response.status_code == 201
    assert seen['token'] == 'ghp_real_token'  # decrypted for the call
    assert response.get_json()['repo_ref'] == 'o/r#9'
