from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, g, jsonify, request

from app.db import db
from app.tokens import require_auth

COLLECTION_NAMES = ['github_connection', 'submission', 'review', 'findings']


def _serialize(document):
    document['_id'] = str(document['_id'])
    return document


def make_collection_blueprint(name: str) -> Blueprint:
    bp = Blueprint(name, __name__, url_prefix=f'/api/{name}')
    collection = db[name]

    @bp.post('')
    @require_auth
    def create():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({'error': 'Request body must be a JSON object.'}), 400

        body['user_id'] = g.user_id
        body['created_at'] = datetime.now(timezone.utc).isoformat()
        result = collection.insert_one(body)
        return jsonify(_serialize(collection.find_one({'_id': result.inserted_id}))), 201

    @bp.get('')
    @require_auth
    def list_all():
        documents = [_serialize(doc) for doc in collection.find({'user_id': g.user_id})]
        return jsonify(documents)

    @bp.get('/<doc_id>')
    @require_auth
    def get_one(doc_id):
        try:
            object_id = ObjectId(doc_id)
        except InvalidId:
            return jsonify({'error': 'Invalid id.'}), 400

        document = collection.find_one({'_id': object_id, 'user_id': g.user_id})
        if document is None:
            return jsonify({'error': 'Not found.'}), 404
        return jsonify(_serialize(document))

    return bp
