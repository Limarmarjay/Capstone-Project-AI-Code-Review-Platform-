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
    tampered = token[:-1] + ('a' if token[-1] != 'a' else 'b')
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
