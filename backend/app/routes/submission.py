"""
'/api/submission' — receives a patch file or git diff file from the
frontend and runs it through repo_diff_pipeline's ingestion, format
validation, Python-only scope check, and diff parsing, then straight into
static analysis / security checks / LLM review
(Static_checks_and_sec_rules), all in one request — the parsed diff is
built once and reused for both the stored file summary and the analysis,
instead of parsing the same content twice across two separate requests.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, g, jsonify, request
from werkzeug.utils import secure_filename

from app.crypto import decrypt_token
from app.db import db
from app.tokens import require_auth

# repo_diff_pipeline and Static_checks_and_sec_rules/services both live at
# (or under) the repo root, a sibling of backend/ — make them importable
# regardless of the working directory the Flask app is started from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (_REPO_ROOT, _REPO_ROOT / 'Static_checks_and_sec_rules'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from repo_diff_pipeline import github_client, ingestion, validation  # noqa: E402
from repo_diff_pipeline.diff_parser import DiffParseError, parse_diff  # noqa: E402
from services.submission_analysis import (  # noqa: E402
    SubmissionAnalysisError,
    analyze_parsed_submission,
)

log = logging.getLogger(__name__)

submission_bp = Blueprint('submission', __name__, url_prefix='/api/submission')
submissions = db['submission']
reviews = db['review']
findings_collection = db['findings']
github_connections = db['GitHub_connection']

SEVERITY_LEVELS = ('critical', 'high', 'medium', 'low')


def _serialize(document):
    document = dict(document)
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


def _severity_counts(findings):
    counts = {level: 0 for level in SEVERITY_LEVELS}
    for finding in findings:
        severity = finding.get('severity')
        if severity in counts:
            counts[severity] += 1
    return counts


def _infer_tool(finding):
    """e.g. 'bandit:B602:risky.py:3' -> 'bandit'. None for LLM-only findings."""
    static_ids = finding.get('static_finding_ids') or []
    if not static_ids:
        return None
    first = static_ids[0]
    return first.split(':', 1)[0] if ':' in first else first


def _finding_title(finding):
    return finding.get('title') or finding.get('category', 'issue').replace('_', ' ').title()


def _inline_comment_body(finding):
    """'Generate Inline Comments' — one ready-to-display annotation per
    finding, anchored to the file+line it applies to."""
    lines = [f"[{(finding.get('severity') or 'unknown').upper()}] {_finding_title(finding)}"]
    if finding.get('explanation'):
        lines.append(finding['explanation'])
    if finding.get('suggested_fix'):
        lines.append(f"Suggested fix: {finding['suggested_fix']}")
    return '\n'.join(lines)


def _generate_report_text(review_output):
    """'Generate Summary Report' — a human-readable top-of-review report,
    not just the one-line summary already in review_output['summary']."""
    findings = review_output.get('findings', [])
    counts = _severity_counts(findings)

    lines = [
        f"Overall Score: {review_output.get('overall_score')}/100",
        review_output.get('summary') or '',
        '',
    ]

    if not findings:
        lines.append('No issues found.')
        return '\n'.join(lines).strip()

    lines.append(f"Findings ({len(findings)} total):")
    for level in SEVERITY_LEVELS:
        if counts[level]:
            lines.append(f"  - {level}: {counts[level]}")
    lines.append('')
    lines.append('Details:')

    for i, finding in enumerate(findings, start=1):
        location = f"{finding.get('file_path')}:{finding.get('line')}"
        severity = (finding.get('severity') or 'unknown').upper()
        lines.append(f"{i}. [{severity}] {_finding_title(finding)} — {location}")
        if finding.get('explanation'):
            lines.append(f"   {finding['explanation']}")
        if finding.get('suggested_fix'):
            lines.append(f"   Suggested fix: {finding['suggested_fix']}")

    return '\n'.join(lines)


def _start_review(*, submission_id, user_id):
    """'review': one document per submission with the overall review-level
    info for that code review (status/score/summary), not the individual
    issues — those go in 'findings'."""
    started_at = datetime.now(timezone.utc).isoformat()
    review_doc = {
        'user_id': user_id,
        'submission_id': submission_id,
        'status': 'processing',
        'started_at': started_at,
        'completed_at': None,
        'total_findings': None,
        'severity_counts': None,
        'review_score': None,
        'summary': None,
        'report': None,
    }
    return reviews.insert_one(review_doc).inserted_id


def _complete_review(review_id, review_output):
    findings_list = review_output.get('findings', [])
    report_text = _generate_report_text(review_output)
    reviews.update_one(
        {'_id': review_id},
        {'$set': {
            'status': 'completed',
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'total_findings': len(findings_list),
            'severity_counts': _severity_counts(findings_list),
            'review_score': review_output.get('overall_score'),
            'summary': review_output.get('summary'),
            'report': report_text,
        }},
    )

    if findings_list:
        findings_collection.insert_many([
            {
                'review_id': str(review_id),
                'file': finding.get('file_path'),
                'line': finding.get('line'),
                'category': finding.get('category'),
                'severity': finding.get('severity'),
                'source': finding.get('source'),
                'tool': _infer_tool(finding),
                'explanation': finding.get('explanation'),
                'suggestion': finding.get('suggested_fix'),
                'body': _inline_comment_body(finding),
            }
            for finding in findings_list
        ])

    return report_text


def _fail_review(review_id, summary):
    reviews.update_one(
        {'_id': review_id},
        {'$set': {
            'status': 'failed',
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'summary': summary,
        }},
    )


@submission_bp.post('')
@require_auth
def create():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    submission_type = body.get('submission_type')
    if submission_type not in ('patch', 'diff', 'pull_request'):
        return jsonify({'error': "submission_type must be 'patch', 'diff', or 'pull_request'."}), 400

    if submission_type == 'pull_request':
        pr_url = body.get('pr_url')
        if not isinstance(pr_url, str) or not pr_url:
            return jsonify({'error': "'pr_url' is required for pull_request submissions."}), 400

        connection = github_connections.find_one({'user_id': g.user_id})
        if not connection:
            return jsonify({'error': 'GitHub is not connected.'}), 400

        content = None
        raw_filename = pr_url
        ingest_kwargs = {'pr_url': pr_url, 'github_token': decrypt_token(connection['token'])}
    else:
        content = body.get('content')
        if not isinstance(content, str):
            return jsonify({'error': "'content' is required for patch/diff submissions."}), 400

        raw_filename = body.get('filename')
        ingest_kwargs = {'patch_text': content}

    filename = (secure_filename(raw_filename) if isinstance(raw_filename, str) else '') or 'submission.patch'

    object_id = ObjectId()
    submission_id = str(object_id)
    record = {
        '_id': object_id,
        'user_id': g.user_id,
        'submission_type': submission_type,
        'filename': raw_filename,
        'size': body.get('size'),
        'content': content,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }

    parsed = None
    try:
        with ingestion.ingest_submission(**ingest_kwargs) as ingested:
            block_reason = validation.validate_input(ingested.diff_text)
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
                # block_reason is only None here once parse_diff succeeded above.
                assert parsed is not None
                record.update({
                    'status': 'parsed',
                    'repo_ref': ingested.repo_ref,
                    'base_ref': ingested.base_ref,
                    'head_ref': ingested.head_ref,
                    'files': _file_summary(parsed.files),
                })
    except (ingestion.IngestionError, github_client.GitHubError) as e:
        record.update({'status': 'blocked', 'block_reason': str(e)})

    # 'submission' is done once ingestion/parsing is settled — its status
    # reflects intake only, never the review outcome (that lives on 'review').
    submissions.insert_one(record)

    if record['status'] == 'blocked':
        return jsonify({'error': record['block_reason'], 'submission': _serialize(record)}), 400

    # status is only 'parsed' (never 'blocked') once parse_diff succeeded above.
    assert parsed is not None
    review_id = _start_review(submission_id=submission_id, user_id=g.user_id)

    try:
        review_output, scan_issues = analyze_parsed_submission(
            parsed,
            submission_id=submission_id,
            submission_type=submission_type,
            filename=filename,
        )
    except SubmissionAnalysisError as exc:
        log.info('Submission analysis rejected: %s', exc)
        _fail_review(review_id, 'Submission analysis failed.')
        return jsonify({
            'error': 'Submission analysis failed.',
            'submission': _serialize(record),
            'review_id': str(review_id),
        }), 400
    except Exception:
        log.exception('Unexpected submission analysis failure')
        _fail_review(review_id, 'Analysis failed unexpectedly.')
        return jsonify({
            'error': 'Analysis failed unexpectedly.',
            'submission': _serialize(record),
            'review_id': str(review_id),
        }), 500

    report_text = _complete_review(review_id, review_output)

    response_body = _serialize(record)
    response_body['review_id'] = str(review_id)
    response_body['review'] = review_output
    response_body['scan_issues'] = scan_issues
    response_body['report'] = report_text
    response_body['inline_comments'] = [
        {
            'file': finding.get('file_path'),
            'line': finding.get('line'),
            'severity': finding.get('severity'),
            'body': _inline_comment_body(finding),
        }
        for finding in review_output.get('findings', [])
    ]
    return jsonify(response_body), 201


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
