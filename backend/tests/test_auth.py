def test_signup_creates_user_and_returns_token(client):
    resp = client.post('/api/auth/signup', json={'username': 'alice', 'password': 'password123'})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['user']['username'] == 'alice'
    assert data['token']


def test_signup_rejects_short_password(client):
    resp = client.post('/api/auth/signup', json={'username': 'alice', 'password': 'short'})
    assert resp.status_code == 400


def test_signup_rejects_duplicate_username(client):
    client.post('/api/auth/signup', json={'username': 'alice', 'password': 'password123'})
    resp = client.post('/api/auth/signup', json={'username': 'alice', 'password': 'password456'})
    assert resp.status_code == 409


def test_login_with_correct_credentials(client):
    client.post('/api/auth/signup', json={'username': 'alice', 'password': 'password123'})
    resp = client.post('/api/auth/login', json={'username': 'alice', 'password': 'password123'})
    assert resp.status_code == 200
    assert resp.get_json()['token']


def test_login_with_wrong_password_is_rejected(client):
    client.post('/api/auth/signup', json={'username': 'alice', 'password': 'password123'})
    resp = client.post('/api/auth/login', json={'username': 'alice', 'password': 'wrong-password'})
    assert resp.status_code == 401


def test_me_requires_auth(client):
    resp = client.get('/api/auth/me')
    assert resp.status_code == 401


def test_me_returns_current_user(client, auth):
    resp = client.get('/api/auth/me', headers=auth['headers'])
    assert resp.status_code == 200
    assert resp.get_json()['username'] == auth['user']['username']
