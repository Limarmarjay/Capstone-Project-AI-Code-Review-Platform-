from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.tokens import JWT_ALGORITHM, JWT_SECRET, create_token, decode_token


def test_create_and_decode_round_trip():
    token = create_token('user-123')
    assert decode_token(token) == 'user-123'


def test_decode_rejects_expired_token():
    expired = jwt.encode(
        {'sub': 'user-123', 'exp': datetime.now(timezone.utc) - timedelta(hours=1)},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(expired)


def test_decode_rejects_tampered_token():
    token = create_token('user-123')
    # Flip a character in the middle of the signature. (The last base64 char
    # only carries padding bits, so changing it can leave the bytes intact.)
    header, payload, signature = token.split('.')
    mid = len(signature) // 2
    flipped = 'A' if signature[mid] != 'A' else 'B'
    tampered = '.'.join([header, payload, signature[:mid] + flipped + signature[mid + 1:]])
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(tampered)


def test_decode_rejects_wrong_secret():
    token = jwt.encode(
        {'sub': 'user-123', 'exp': datetime.now(timezone.utc) + timedelta(hours=1)},
        'a-different-secret',
        algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token)


def test_token_expiry_is_24_hours():
    payload = jwt.decode(create_token('u'), JWT_SECRET, algorithms=[JWT_ALGORITHM])
    remaining = payload['exp'] - datetime.now(timezone.utc).timestamp()
    assert 23.9 * 3600 < remaining <= 24 * 3600


# --- require_auth decorator, exercised through a real protected route -------

def test_require_auth_missing_header(client):
    response = client.get('/api/auth/me')
    assert response.status_code == 401
    assert 'Authorization' in response.get_json()['error']


def test_require_auth_wrong_scheme(client):
    response = client.get('/api/auth/me', headers={'Authorization': 'Basic abc'})
    assert response.status_code == 401


def test_require_auth_invalid_token(client):
    response = client.get('/api/auth/me', headers={'Authorization': 'Bearer not-a-jwt'})
    assert response.status_code == 401
    assert response.get_json()['error'] == 'Invalid token.'


def test_require_auth_expired_token(client):
    expired = jwt.encode(
        {'sub': 'x', 'exp': datetime.now(timezone.utc) - timedelta(minutes=1)},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    response = client.get('/api/auth/me', headers={'Authorization': f'Bearer {expired}'})
    assert response.status_code == 401
    assert response.get_json()['error'] == 'Token expired.'
