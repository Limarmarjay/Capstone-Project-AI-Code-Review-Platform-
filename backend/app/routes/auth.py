from bson import ObjectId
from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from app.tokens import create_token, require_auth
from app.db import db

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')
users = db['user']


def _serialize_user(user):
    return {'id': str(user['_id']), 'username': user['username']}


@auth_bp.post('/signup')
def signup():
    body = request.get_json(silent=True) or {}
    username = body.get('username', '').strip()
    password = body.get('password', '')

    if not username or not password:
        return jsonify({'error': 'Username and password are required.'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters.'}), 400
    if users.find_one({'username': username}):
        return jsonify({'error': 'An account with this username already exists.'}), 409

    result = users.insert_one({
        'username': username,
        'password_hash': generate_password_hash(password),
    })
    token = create_token(str(result.inserted_id))
    return jsonify({'token': token, 'user': {'id': str(result.inserted_id), 'username': username}}), 201


@auth_bp.post('/login')
def login():
    body = request.get_json(silent=True) or {}
    username = body.get('username', '').strip()
    password = body.get('password', '')

    user = users.find_one({'username': username})
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Invalid username or password.'}), 401

    token = create_token(str(user['_id']))
    return jsonify({'token': token, 'user': _serialize_user(user)})


@auth_bp.get('/me')
@require_auth
def me():
    user = users.find_one({'_id': ObjectId(g.user_id)})
    if user is None:
        return jsonify({'error': 'User not found.'}), 404
    return jsonify(_serialize_user(user))
