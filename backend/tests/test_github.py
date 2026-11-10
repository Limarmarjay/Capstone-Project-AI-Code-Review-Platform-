"""
Tests for the /api/github routes: storing a Personal Access Token and
browsing repos/PRs through it. All calls out to GitHub's REST API are
monkeypatched — these tests never hit the real network.
"""
from app.crypto import decrypt_token
from app.db import db
from app.routes import github as github_routes


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data


def test_connect_requires_auth(client):
    resp = client.post('/api/github/connect', json={'token': 'ghp_whatever'})
    assert resp.status_code == 401


def test_connect_rejects_missing_token(client, auth):
    resp = client.post('/api/github/connect', json={}, headers=auth['headers'])
    assert resp.status_code == 400


def test_connect_rejects_invalid_token(client, auth, monkeypatch):
    monkeypatch.setattr(github_routes.github_client, 'check_github_connected', lambda token: False)

    resp = client.post('/api/github/connect', json={'token': 'bad-token'}, headers=auth['headers'])
    assert resp.status_code == 400
    assert db['GitHub_connection'].find_one({'user_id': auth['user']['id']}) is None


def test_connect_stores_token_on_success(client, auth, monkeypatch):
    monkeypatch.setattr(github_routes.github_client, 'check_github_connected', lambda token: True)

    resp = client.post('/api/github/connect', json={'token': 'ghp_good'}, headers=auth['headers'])
    assert resp.status_code == 201
    assert resp.get_json() == {'connected': True}

    stored = db['GitHub_connection'].find_one({'user_id': auth['user']['id']})
    assert stored['token'] != 'ghp_good'  # must not be stored in plaintext
    assert decrypt_token(stored['token']) == 'ghp_good'


def test_connect_upserts_on_reconnect(client, auth, monkeypatch):
    monkeypatch.setattr(github_routes.github_client, 'check_github_connected', lambda token: True)

    client.post('/api/github/connect', json={'token': 'ghp_first'}, headers=auth['headers'])
    client.post('/api/github/connect', json={'token': 'ghp_second'}, headers=auth['headers'])

    docs = list(db['GitHub_connection'].find({'user_id': auth['user']['id']}))
    assert len(docs) == 1
    assert decrypt_token(docs[0]['token']) == 'ghp_second'


def test_status_requires_auth(client):
    resp = client.get('/api/github/status')
    assert resp.status_code == 401


def test_status_false_when_not_connected(client, auth):
    resp = client.get('/api/github/status', headers=auth['headers'])
    assert resp.status_code == 200
    assert resp.get_json() == {'connected': False}


def test_status_true_once_connected(client, auth, monkeypatch):
    monkeypatch.setattr(github_routes.github_client, 'check_github_connected', lambda token: True)
    client.post('/api/github/connect', json={'token': 'ghp_good'}, headers=auth['headers'])

    resp = client.get('/api/github/status', headers=auth['headers'])
    assert resp.get_json() == {'connected': True}


def test_repos_requires_connection(client, auth):
    resp = client.get('/api/github/repos', headers=auth['headers'])
    assert resp.status_code == 400


def test_repos_returns_github_response(client, auth, monkeypatch):
    monkeypatch.setattr(github_routes.github_client, 'check_github_connected', lambda token: True)
    client.post('/api/github/connect', json={'token': 'ghp_good'}, headers=auth['headers'])

    fake_repos = [{'id': 1, 'full_name': 'alice/project'}]
    monkeypatch.setattr(
        github_routes.requests, 'get', lambda *args, **kwargs: FakeResponse(fake_repos)
    )

    resp = client.get('/api/github/repos', headers=auth['headers'])
    assert resp.status_code == 200
    assert resp.get_json() == fake_repos


def test_pulls_requires_connection(client, auth):
    resp = client.get('/api/github/repos/alice/project/pulls', headers=auth['headers'])
    assert resp.status_code == 400


def test_pulls_returns_github_response(client, auth, monkeypatch):
    monkeypatch.setattr(github_routes.github_client, 'check_github_connected', lambda token: True)
    client.post('/api/github/connect', json={'token': 'ghp_good'}, headers=auth['headers'])

    fake_pulls = [{'id': 10, 'number': 42, 'title': 'Fix bug', 'html_url': 'https://github.com/alice/project/pull/42'}]
    monkeypatch.setattr(
        github_routes.requests, 'get', lambda *args, **kwargs: FakeResponse(fake_pulls)
    )

    resp = client.get('/api/github/repos/alice/project/pulls', headers=auth['headers'])
    assert resp.status_code == 200
    assert resp.get_json() == fake_pulls


def test_repos_scoped_per_user(client, auth, monkeypatch):
    """A second user with no connection of their own gets 400, not the first user's repos."""
    monkeypatch.setattr(github_routes.github_client, 'check_github_connected', lambda token: True)
    client.post('/api/github/connect', json={'token': 'ghp_good'}, headers=auth['headers'])

    other_signup = client.post(
        '/api/auth/signup', json={'username': 'bob', 'password': 'password123'}
    ).get_json()
    other_headers = {'Authorization': f"Bearer {other_signup['token']}"}

    resp = client.get('/api/github/repos', headers=other_headers)
    assert resp.status_code == 400
