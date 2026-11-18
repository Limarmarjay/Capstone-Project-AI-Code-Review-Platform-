"""Tests for the generic per-collection CRUD blueprints (review, findings) and app wiring."""
import pytest
from bson import ObjectId

from app import GENERIC_COLLECTION_NAMES, create_app
from app.db import db
from app.routes.collections import COLLECTION_NAMES, make_collection_blueprint
from tests.conftest import signup

# `review` and `findings` are the collections routed via the generic factory.
GENERIC = ['review', 'findings']


def test_generic_names_exclude_submission():
    assert 'submission' in COLLECTION_NAMES
    assert 'submission' not in GENERIC_COLLECTION_NAMES
    assert set(GENERIC_COLLECTION_NAMES) == set(GENERIC)


def test_factory_builds_blueprint_with_prefix():
    bp = make_collection_blueprint('review')
    assert bp.name == 'review'
    assert bp.url_prefix == '/api/review'


@pytest.mark.parametrize('name', GENERIC)
def test_endpoints_require_auth(client, name):
    assert client.post(f'/api/{name}', json={}).status_code == 401
    assert client.get(f'/api/{name}').status_code == 401
    assert client.get(f'/api/{name}/{ObjectId()}').status_code == 401


@pytest.mark.parametrize('name', GENERIC)
def test_create_stamps_user_and_timestamp(client, auth, name):
    response = client.post(f'/api/{name}', json={'note': 'hi'}, headers=auth['headers'])
    assert response.status_code == 201
    body = response.get_json()
    assert body['note'] == 'hi'
    assert body['user_id'] == auth['user']['id']
    assert body['created_at']
    assert isinstance(body['_id'], str)
    assert db[name].count_documents({}) == 1


@pytest.mark.parametrize('payload', [[1, 2], 'text', 5])
def test_create_rejects_non_object_body(client, auth, payload):
    response = client.post('/api/review', json=payload, headers=auth['headers'])
    assert response.status_code == 400


def test_create_rejects_missing_body(client, auth):
    response = client.post('/api/review', data='not json', headers=auth['headers'])
    assert response.status_code == 400


def test_client_cannot_spoof_user_id(client, auth):
    response = client.post(
        '/api/review', json={'user_id': 'someone-else'}, headers=auth['headers']
    )
    assert response.get_json()['user_id'] == auth['user']['id']


def test_list_returns_only_own_documents(client, auth):
    other = signup(client, username='other')
    other_headers = {'Authorization': f"Bearer {other['token']}"}
    client.post('/api/review', json={'n': 1}, headers=auth['headers'])
    client.post('/api/review', json={'n': 2}, headers=other_headers)

    mine = client.get('/api/review', headers=auth['headers']).get_json()
    assert [d['n'] for d in mine] == [1]


def test_list_empty(client, auth):
    assert client.get('/api/findings', headers=auth['headers']).get_json() == []


def test_get_one_round_trip(client, auth):
    created = client.post('/api/review', json={'n': 7}, headers=auth['headers']).get_json()
    response = client.get(f"/api/review/{created['_id']}", headers=auth['headers'])
    assert response.status_code == 200
    assert response.get_json() == created


def test_get_one_invalid_id(client, auth):
    response = client.get('/api/review/not-an-object-id', headers=auth['headers'])
    assert response.status_code == 400


def test_get_one_not_found(client, auth):
    response = client.get(f'/api/review/{ObjectId()}', headers=auth['headers'])
    assert response.status_code == 404


def test_get_one_hides_other_users_documents(client, auth):
    other = signup(client, username='other')
    other_headers = {'Authorization': f"Bearer {other['token']}"}
    created = client.post('/api/review', json={}, headers=other_headers).get_json()
    response = client.get(f"/api/review/{created['_id']}", headers=auth['headers'])
    assert response.status_code == 404


def test_health(client):
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.get_json() == {'status': 'ok'}


def test_create_app_registers_expected_blueprints():
    app = create_app()
    assert {'health', 'auth', 'submission', 'github', 'review', 'findings'} <= set(app.blueprints)


def test_cors_headers_present(client):
    response = client.get('/api/health', headers={'Origin': 'http://localhost:5173'})
    assert response.headers.get('Access-Control-Allow-Origin') in ('*', 'http://localhost:5173')
