"""
'/api/github' — lets a user store a GitHub Personal Access Token and browse
their repos/PRs through it. Matches repo_diff_pipeline.github_client's
token-based auth (check_github_connected / fetch_pr_*) rather than a full
OAuth App flow, since that's what the PR-ingestion pipeline already expects.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests
from flask import Blueprint, g, jsonify, request

from app.crypto import decrypt_token, encrypt_token
from app.db import db
from app.tokens import require_auth

# repo_diff_pipeline lives at the repo root, a sibling of backend/ — make it
# importable regardless of the working directory the Flask app starts from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_diff_pipeline import github_client  # noqa: E402

github_bp = Blueprint('github', __name__, url_prefix='/api/github')
github_connections = db['GitHub_connection']

GITHUB_API = 'https://api.github.com'
REQUEST_TIMEOUT = 15


def _get_connection(user_id: str):
    return github_connections.find_one({'user_id': user_id})


def _get_decrypted_token(user_id: str) -> str | None:
    connection = _get_connection(user_id)
    return decrypt_token(connection['token']) if connection else None


@github_bp.post('/connect')
@require_auth
def connect():
    body = request.get_json(silent=True) or {}
    token = (body.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'A personal access token is required.'}), 400

    if not github_client.check_github_connected(token):
        return jsonify({'error': 'That token could not be verified with GitHub.'}), 400

    github_connections.update_one(
        {'user_id': g.user_id},
        {'$set': {'user_id': g.user_id, 'token': encrypt_token(token)}},
        upsert=True,
    )
    return jsonify({'connected': True}), 201


@github_bp.get('/status')
@require_auth
def status():
    return jsonify({'connected': _get_connection(g.user_id) is not None})


@github_bp.get('/repos')
@require_auth
def list_repos():
    token = _get_decrypted_token(g.user_id)
    if not token:
        return jsonify({'error': 'GitHub is not connected.'}), 400

    response = requests.get(
        f'{GITHUB_API}/user/repos',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
        },
        params={'sort': 'updated', 'per_page': 50},
        timeout=REQUEST_TIMEOUT,
    )
    return jsonify(response.json()), response.status_code


@github_bp.get('/repos/<owner>/<repo>/pulls')
@require_auth
def list_pulls(owner, repo):
    token = _get_decrypted_token(g.user_id)
    if not token:
        return jsonify({'error': 'GitHub is not connected.'}), 400

    response = requests.get(
        f'{GITHUB_API}/repos/{owner}/{repo}/pulls',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
        },
        params={'state': 'open', 'per_page': 50},
        timeout=REQUEST_TIMEOUT,
    )
    return jsonify(response.json()), response.status_code
