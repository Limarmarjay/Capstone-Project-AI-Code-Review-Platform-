import os

import requests
from flask import Blueprint, g, jsonify, redirect, request

from app.db import db
from app.tokens import decode_token, require_auth

github_bp = Blueprint('github', __name__, url_prefix='/api/github')

# Placeholders — replace with a real GitHub OAuth App's credentials.
GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID', 'REPLACE_WITH_CLIENT_ID')
GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET', 'REPLACE_WITH_CLIENT_SECRET')
GITHUB_CALLBACK_URL = os.environ.get('GITHUB_CALLBACK_URL', 'http://127.0.0.1:5002/api/github/callback')
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5173')

github_connections = db['github_connection']


def _get_connection(user_id: str):
    return github_connections.find_one({'user_id': user_id})


@github_bp.get('/connect')
def connect():
    token = request.args.get('token', '')
    try:
        decode_token(token)
    except Exception:
        return jsonify({'error': 'Invalid or missing token.'}), 401

    authorize_url = (
        'https://github.com/login/oauth/authorize'
        f'?client_id={GITHUB_CLIENT_ID}'
        f'&redirect_uri={GITHUB_CALLBACK_URL}'
        '&scope=repo'
        f'&state={token}'
    )
    return redirect(authorize_url)


@github_bp.get('/callback')
def callback():
    code = request.args.get('code')
    state = request.args.get('state', '')
    try:
        user_id = decode_token(state)
    except Exception:
        return redirect(f'{FRONTEND_URL}/?github_error=invalid_state')

    token_response = requests.post(
        'https://github.com/login/oauth/access_token',
        headers={'Accept': 'application/json'},
        data={
            'client_id': GITHUB_CLIENT_ID,
            'client_secret': GITHUB_CLIENT_SECRET,
            'code': code,
            'redirect_uri': GITHUB_CALLBACK_URL,
        },
        timeout=10,
    )
    access_token = token_response.json().get('access_token')
    if not access_token:
        return redirect(f'{FRONTEND_URL}/?github_error=exchange_failed')

    github_connections.update_one(
        {'user_id': user_id},
        {'$set': {'user_id': user_id, 'access_token': access_token}},
        upsert=True,
    )
    return redirect(f'{FRONTEND_URL}/?github_connected=1')


@github_bp.get('/status')
@require_auth
def status():
    return jsonify({'connected': _get_connection(g.user_id) is not None})


@github_bp.get('/repos')
@require_auth
def list_repos():
    connection = _get_connection(g.user_id)
    if not connection:
        return jsonify({'error': 'GitHub is not connected.'}), 400

    response = requests.get(
        'https://api.github.com/user/repos',
        headers={'Authorization': f'Bearer {connection["access_token"]}'},
        params={'sort': 'updated', 'per_page': 50},
        timeout=10,
    )
    return jsonify(response.json()), response.status_code


@github_bp.get('/repos/<owner>/<repo>/pulls')
@require_auth
def list_pulls(owner, repo):
    connection = _get_connection(g.user_id)
    if not connection:
        return jsonify({'error': 'GitHub is not connected.'}), 400

    response = requests.get(
        f'https://api.github.com/repos/{owner}/{repo}/pulls',
        headers={'Authorization': f'Bearer {connection["access_token"]}'},
        params={'state': 'open', 'per_page': 50},
        timeout=10,
    )
    return jsonify(response.json()), response.status_code
