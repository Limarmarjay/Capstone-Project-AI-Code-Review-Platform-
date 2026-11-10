"""
Tests for the /api/submission route: the frontend-input -> repo_diff_pipeline
wiring (ingestion, format validation, Python-only scope check, diff parsing),
followed in the same request by static analysis / security checks / LLM
review (Static_checks_and_sec_rules) against that same parsed diff, split
into 'review' (overall per-submission info) and 'findings' (individual
issues) documents in Mongo.
"""
from bson import ObjectId

from app.db import db

PYTHON_DIFF = (
    "diff --git a/hello.py b/hello.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/hello.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def hello():\n"
    "+    return 'hi'\n"
)

RISKY_PYTHON_DIFF = (
    "diff --git a/risky.py b/risky.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/risky.py\n"
    "@@ -0,0 +1,3 @@\n"
    "+import subprocess\n"
    "+def run(cmd):\n"
    "+    subprocess.call(cmd, shell=True)\n"
)

MIXED_DIFF = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1 +1 @@\n"
    "-a\n"
    "+b\n"
    "diff --git a/style.css b/style.css\n"
    "--- a/style.css\n"
    "+++ b/style.css\n"
    "@@ -1 +1 @@\n"
    "-a\n"
    "+b\n"
)


def test_submission_requires_auth(client):
    resp = client.post('/api/submission', json={'submission_type': 'patch', 'content': PYTHON_DIFF})
    assert resp.status_code == 401


def test_submission_rejects_bad_type(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'github_pr', 'content': PYTHON_DIFF},
        headers=auth['headers'],
    )
    assert resp.status_code == 400


def test_submission_rejects_missing_content(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'patch'},
        headers=auth['headers'],
    )
    assert resp.status_code == 400


def test_python_only_patch_is_parsed(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'filename': 'hello.patch', 'content': PYTHON_DIFF},
        headers=auth['headers'],
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['status'] == 'parsed'
    assert data['files'] == [
        {'path': 'hello.py', 'change_type': 'added', 'additions': 2, 'deletions': 0, 'is_binary': False}
    ]
    assert data['review']['review_status'] == 'completed'
    assert data['review']['llm_status'] == 'success'


def test_review_includes_bandit_and_llm_findings_in_one_request(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'filename': 'risky.patch', 'content': RISKY_PYTHON_DIFF},
        headers=auth['headers'],
    )
    assert resp.status_code == 201
    review = resp.get_json()['review']

    sources = {finding['source'] for finding in review['findings']}
    assert 'static' in sources  # bandit picked up the shell=True call

    static_finding_ids = {
        fid for finding in review['findings'] for fid in finding['static_finding_ids']
    }
    assert any('B602' in fid for fid in static_finding_ids)  # subprocess with shell=True


def test_review_and_findings_are_persisted_in_their_own_collections(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'filename': 'risky.patch', 'content': RISKY_PYTHON_DIFF},
        headers=auth['headers'],
    )
    assert resp.status_code == 201
    data = resp.get_json()
    review_id = data['review_id']

    # submission holds intake info only — never review/findings data.
    submission_doc = db['submission'].find_one({'_id': ObjectId(data['_id'])})
    assert 'review' not in submission_doc
    assert 'scan_issues' not in submission_doc

    # review holds one document with overall per-submission review info.
    review_doc = db['review'].find_one({'_id': ObjectId(review_id)})
    assert review_doc['user_id'] == auth['user']['id']
    assert review_doc['submission_id'] == data['_id']
    assert review_doc['status'] == 'completed'
    assert review_doc['started_at'] is not None
    assert review_doc['completed_at'] is not None
    assert review_doc['total_findings'] == 3
    assert review_doc['severity_counts'] == {
        'critical': 0, 'high': 1, 'medium': 0, 'low': 2,
    }
    assert review_doc['review_score'] == data['review']['overall_score']
    assert review_doc['summary'] == data['review']['summary']

    # findings holds one document per issue, each pointing back at its review.
    finding_docs = list(db['findings'].find({'review_id': review_id}))
    assert len(finding_docs) == 3
    bandit_docs = [f for f in finding_docs if f['tool'] == 'bandit']
    assert any(f['severity'] == 'high' and 'shell=True' in f['explanation'] for f in bandit_docs)
    for f in finding_docs:
        assert set(f.keys()) >= {
            'review_id', 'file', 'line', 'category', 'severity', 'source', 'tool',
            'explanation', 'suggestion',
        }


def test_response_includes_summary_report_and_inline_comments(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'filename': 'risky.patch', 'content': RISKY_PYTHON_DIFF},
        headers=auth['headers'],
    )
    assert resp.status_code == 201
    data = resp.get_json()

    assert 'Overall Score: 74/100' in data['report']
    assert 'Findings (3 total):' in data['report']
    assert 'B602' in data['report']

    assert len(data['inline_comments']) == 3
    high_comment = next(c for c in data['inline_comments'] if c['severity'] == 'high')
    assert high_comment['file'] == 'risky.py'
    assert high_comment['line'] == 3
    assert 'shell=True' in high_comment['body']

    review_doc = db['review'].find_one({'_id': ObjectId(data['review_id'])})
    assert review_doc['report'] == data['report']

    finding_docs = list(db['findings'].find({'review_id': data['review_id']}))
    assert all(f['body'] for f in finding_docs)


def test_blocked_submission_never_starts_a_review(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'content': 'not a diff at all'},
        headers=auth['headers'],
    )
    assert resp.status_code == 400
    submission_id = resp.get_json()['submission']['_id']
    assert db['review'].find_one({'submission_id': submission_id}) is None


def test_non_python_file_is_blocked(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'diff', 'filename': 'mixed.diff', 'content': MIXED_DIFF},
        headers=auth['headers'],
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert 'style.css' in data['error']
    assert data['submission']['status'] == 'blocked'


def test_garbage_content_is_blocked(client, auth):
    resp = client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'content': 'not a diff at all'},
        headers=auth['headers'],
    )
    assert resp.status_code == 400
    assert 'error' in resp.get_json()


def test_list_only_returns_own_submissions(client, auth):
    client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'content': PYTHON_DIFF},
        headers=auth['headers'],
    )

    other_signup = client.post(
        '/api/auth/signup', json={'username': 'bob', 'password': 'password123'}
    ).get_json()
    other_headers = {'Authorization': f"Bearer {other_signup['token']}"}

    resp = client.get('/api/submission', headers=auth['headers'])
    assert len(resp.get_json()) == 1

    resp = client.get('/api/submission', headers=other_headers)
    assert resp.get_json() == []


def test_get_one_not_found_for_other_users_submission(client, auth):
    created = client.post(
        '/api/submission',
        json={'submission_type': 'patch', 'content': PYTHON_DIFF},
        headers=auth['headers'],
    ).get_json()

    other_signup = client.post(
        '/api/auth/signup', json={'username': 'bob', 'password': 'password123'}
    ).get_json()
    other_headers = {'Authorization': f"Bearer {other_signup['token']}"}

    resp = client.get(f"/api/submission/{created['_id']}", headers=other_headers)
    assert resp.status_code == 404
