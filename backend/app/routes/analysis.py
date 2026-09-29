from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.utils import secure_filename

from app.db import db
from app.tokens import require_auth
from services.submission_analysis import (
    SubmissionAnalysisError,
    analyze_uploaded_submission,
)


analysis_bp = Blueprint("analysis", __name__, url_prefix="/api/analyze")


@analysis_bp.post("")
@require_auth
def analyze():
    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    submission_id = body.get("submission_id")
    if not isinstance(submission_id, str) or not submission_id:
        return jsonify({"error": "submission_id is required."}), 400

    try:
        object_id = ObjectId(submission_id)
    except InvalidId:
        return jsonify({"error": "Invalid submission id."}), 400

    submission = db["submission"].find_one(
        {
            "_id": object_id,
            "user_id": g.user_id,
        }
    )

    if submission is None:
        return jsonify({"error": "Submission not found."}), 404

    content = submission.get("content")
    submission_type = submission.get("submission_type")
    raw_filename = submission.get("filename")
    filename = (
        secure_filename(raw_filename)
        if isinstance(raw_filename, str)
        else ""
    ) or "submission.patch"

    if not isinstance(content, str):
        return jsonify({"error": "Submission content is invalid."}), 400

    try:
        review_output, scan_issues = analyze_uploaded_submission(
            content,
            submission_id=str(submission["_id"]),
            submission_type=submission_type,
            filename=filename,
        )
    except SubmissionAnalysisError as exc:
        current_app.logger.info("Submission analysis rejected: %s", exc)
        return jsonify({"error": "Submission analysis failed."}), 400
    except Exception:
        current_app.logger.exception("Unexpected submission analysis failure")
        return jsonify({"error": "Analysis failed unexpectedly."}), 500

    return jsonify(
        {
            "review": review_output,
            "scan_issues": scan_issues,
        }
    )