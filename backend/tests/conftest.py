"""
Shared pytest fixtures for the backend test suite.

Runs against a real local MongoDB instance (same one used in dev), but
pointed at a separate database so tests never touch real data, and wiped
clean after every test.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Use a dedicated test database, isolated from the dev/prod one. Must be set
# before `app.db` is imported for the first time, since it reads env vars at
# module load time.
os.environ.setdefault('MONGO_DB_NAME', 'Capstone_Proj_Database_test')
os.environ.setdefault('JWT_SECRET', 'test-secret-do-not-use-in-prod')
os.environ.setdefault('LLM_CLIENT', 'mock')
# A real Fernet key (app.crypto requires one), but test-only — never used outside this suite.
os.environ.setdefault('ENCRYPTION_KEY', 'ZgmSwe8BrrHlZA4d4-rru5rSBnb6YEr06zcazJyiCOg=')

_TESTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _TESTS_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent

# `app` lives under backend/, and submission.py pulls in modules from
# Static_checks_and_sec_rules/ and the repo root — make all three importable
# regardless of where pytest is invoked from.
for _path in (_BACKEND_DIR, _REPO_ROOT, _REPO_ROOT / 'Static_checks_and_sec_rules'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from app.db import db as mongo_db  # noqa: E402

_MANAGED_COLLECTIONS = ('user', 'submission', 'review', 'findings', 'GitHub_connection')


@pytest.fixture()
def app():
    application = create_app()
    application.testing = True
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_db():
    """Wipe the test database's managed collections before and after each test."""
    for name in _MANAGED_COLLECTIONS:
        mongo_db[name].delete_many({})
    yield
    for name in _MANAGED_COLLECTIONS:
        mongo_db[name].delete_many({})


def signup(client, username='tester', password='password123'):
    response = client.post('/api/auth/signup', json={'username': username, 'password': password})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


@pytest.fixture()
def auth(client):
    """A freshly signed-up user + ready-to-use Authorization header."""
    data = signup(client)
    token = data['token']
    return {
        'token': token,
        'user': data['user'],
        'headers': {'Authorization': f'Bearer {token}'},
    }
