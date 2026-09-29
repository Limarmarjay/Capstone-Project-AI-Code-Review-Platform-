"""
'/api/submission' — receives a patch file, git diff, or GitHub PR pick from
the frontend and runs it through repo_diff_pipeline's ingestion, format
validation, Python-only scope check, and diff parsing.

Static analysis / security checks and AI review are later stages, not
wired in here yet.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, g, jsonify, request

from app.db import db
from app.routes.github import github_connections
from app.tokens import require_auth

# repo_diff_pipeline lives at the repo root, a sibling of backend/ — make it
# importable regardless of the working directory the Flask app is started from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_diff_pipeline import ingestion, validation  # noqa: E402
from repo_diff_pipeline.diff_parser import DiffParseError, parse_diff  # noqa: E402

submission_bp = Blueprint('submission', __name__, url_prefix='/api/submission')
submissions = db['submission']


def _serialize(document):
    document['_id'] = str(document['_id'])
    return document


def _file_summary(files):
    return [
        {
            'path': f.path,
            'change_type': f.change_type.value,
            'additions': f.additions,
            'deletions': f.deletions,
            'is_binary': f.is_binary,
        }
        for f in files
    ]


@submission_bp.post('')
@require_auth
def create():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    submission_type = body.get('submission_type')
    if submission_type not in ('patch', 'diff', 'github_pr'):
        return jsonify({'error': "submission_type must be 'patch', 'diff', or 'github_pr'."}), 400

    record = {
        'user_id': g.user_id,
        'submission_type': submission_type,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    ingest_kwargs: dict = {}

    if submission_type == 'github_pr':
        pr_url = body.get('pr_url')
        if not pr_url:
            return jsonify({'error': "'pr_url' is required for github_pr submissions."}), 400
        connection = github_connections.find_one({'user_id': g.user_id})
        ingest_kwargs['pr_url'] = pr_url
        ingest_kwargs['github_token'] = connection['access_token'] if connection else None
        record.update({'pr_url': pr_url, 'repo': body.get('repo'), 'pr_number': body.get('pr_number')})
    else:
        content = body.get('content')
        if not isinstance(content, str):
            return jsonify({'error': "'content' is required for patch/diff submissions."}), 400
        ingest_kwargs['patch_text'] = content
        record.update({'filename': body.get('filename'), 'size': body.get('size')})

    try:
        with ingestion.ingest_submission(**ingest_kwargs) as ingested:
            block_reason = validation.validate_input(ingested.diff_text)
            parsed = None
            if block_reason is None:
                try:
                    parsed = parse_diff(ingested.diff_text)
                except DiffParseError as e:
                    block_reason = str(e)
                else:
                    block_reason = validation.validate_python_only(parsed.files)

            if block_reason:
                record.update({'status': 'blocked', 'block_reason': block_reason})
            else:
                record.update({
                    'status': 'parsed',
                    'repo_ref': ingested.repo_ref,
                    'base_ref': ingested.base_ref,
                    'head_ref': ingested.head_ref,
                    'files': _file_summary(parsed.files),
                })
    except ingestion.IngestionError as e:
        record.update({'status': 'blocked', 'block_reason': str(e)})

    result = submissions.insert_one(record)
    saved = _serialize(submissions.find_one({'_id': result.inserted_id}))

    if record['status'] == 'blocked':
        return jsonify({'error': record['block_reason'], 'submission': saved}), 400
    return jsonify(saved), 201


@submission_bp.get('')
@require_auth
def list_all():
    documents = [_serialize(doc) for doc in submissions.find({'user_id': g.user_id})]
    return jsonify(documents)


@submission_bp.get('/<doc_id>')
@require_auth
def get_one(doc_id):
    try:
        object_id = ObjectId(doc_id)
    except InvalidId:
        return jsonify({'error': 'Invalid id.'}), 400

    document = submissions.find_one({'_id': object_id, 'user_id': g.user_id})
    if document is None:
        return jsonify({'error': 'Not found.'}), 404
    return jsonify(_serialize(document))
