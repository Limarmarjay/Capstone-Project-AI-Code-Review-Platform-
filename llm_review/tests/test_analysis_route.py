import sys
from pathlib import Path

from bson import ObjectId


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_PATH = REPO_ROOT / "backend"

for path in (REPO_ROOT, BACKEND_PATH):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app import create_app
from app.tokens import create_token
import app.routes.analysis as analysis_routes


class FakeCollection:
    def __init__(self, document):
        self.document = document
        self.last_query = None

    def find_one(self, query):
        self.last_query = query

        if (
            query.get("_id") == self.document["_id"]
            and query.get("user_id") == self.document["user_id"]
        ):
            return self.document

        return None


class FakeDB:
    def __init__(self, document):
        self.submission = FakeCollection(document)

    def __getitem__(self, name):
        assert name == "submission"
        return self.submission


def test_analyze_route_loads_owned_submission_and_returns_review(monkeypatch):
    user_id = "user-123"
    submission_id = ObjectId()

    submission = {
        "_id": submission_id,
        "user_id": user_id,
        "submission_type": "patch",
        "filename": "../../demo.patch",
        "content": """\
diff --git a/example.py b/example.py
new file mode 100644
--- /dev/null
+++ b/example.py
@@ -0,0 +1 @@
+print("hello")
""",
    }

    fake_db = FakeDB(submission)
    monkeypatch.setattr(analysis_routes, "db", fake_db)

    captured = {}

    def fake_analyze_uploaded_submission(
        content,
        *,
        submission_id,
        submission_type,
        filename,
        client=None,
    ):
        captured["content"] = content
        captured["submission_id"] = submission_id
        captured["submission_type"] = submission_type
        captured["filename"] = filename

        return (
            {
                "submission_id": submission_id,
                "review_status": "completed",
                "llm_status": "success",
                "overall_score": 100,
                "summary": "Test review",
                "findings": [],
            },
            {
                "example.py": (
                    "Static analysis not run: full file not available."
                )
            },
        )

    monkeypatch.setattr(
        analysis_routes,
        "analyze_uploaded_submission",
        fake_analyze_uploaded_submission,
    )

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    token = create_token(user_id)

    response = client.post(
        "/api/analyze",
        json={"submission_id": str(submission_id)},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200

    data = response.get_json()

    assert data["review"]["submission_id"] == str(submission_id)
    assert data["review"]["review_status"] == "completed"
    assert data["review"]["llm_status"] == "success"

    assert data["scan_issues"]["example.py"] == (
        "Static analysis not run: full file not available."
    )

    assert captured["submission_id"] == str(submission_id)
    assert captured["submission_type"] == "patch"

    # "../../demo.patch" must be sanitized before analysis.
    assert captured["filename"] == "demo.patch"

    # Most important authorization check:
    assert fake_db.submission.last_query["_id"] == submission_id
    assert fake_db.submission.last_query["user_id"] == user_id


def test_analyze_route_requires_authentication():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/api/analyze",
        json={"submission_id": str(ObjectId())},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == (
        "Missing or invalid Authorization header."
    )


def test_analyze_route_cannot_access_another_users_submission(monkeypatch):
    owner_id = "owner-123"
    other_user_id = "other-456"
    submission_id = ObjectId()

    submission = {
        "_id": submission_id,
        "user_id": owner_id,
        "submission_type": "patch",
        "filename": "demo.patch",
        "content": """\
diff --git a/example.py b/example.py
--- a/example.py
+++ b/example.py
@@ -1 +1 @@
-print("old")
+print("new")
""",
    }

    fake_db = FakeDB(submission)
    monkeypatch.setattr(analysis_routes, "db", fake_db)

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    token = create_token(other_user_id)

    response = client.post(
        "/api/analyze",
        json={"submission_id": str(submission_id)},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Submission not found."

    assert fake_db.submission.last_query["_id"] == submission_id
    assert fake_db.submission.last_query["user_id"] == other_user_id


def test_analyze_route_response_never_contains_raw_secret(monkeypatch):
    user_id = "owner-123"
    submission_id = ObjectId()

    submission = {
        "_id": submission_id,
        "user_id": user_id,
        "submission_type": "patch",
        "filename": "demo.patch",
        "content": """\
diff --git a/example.py b/example.py
new file mode 100644
--- /dev/null
+++ b/example.py
@@ -0,0 +1 @@
+password = "route_canary_secret"
""",
    }

    monkeypatch.setattr(
        analysis_routes,
        "db",
        FakeDB(submission),
    )

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/api/analyze",
        json={"submission_id": str(submission_id)},
        headers={
            "Authorization": f"Bearer {create_token(user_id)}",
        },
    )

    assert response.status_code == 200
    assert "route_canary_secret" not in response.get_data(as_text=True)


def test_analyze_route_hides_unexpected_internal_error(monkeypatch):
    user_id = "owner-123"
    submission_id = ObjectId()

    submission = {
        "_id": submission_id,
        "user_id": user_id,
        "submission_type": "patch",
        "filename": "demo.patch",
        "content": """\
diff --git a/example.py b/example.py
--- a/example.py
+++ b/example.py
@@ -1 +1 @@
-print("old")
+print("new")
""",
    }

    monkeypatch.setattr(
        analysis_routes,
        "db",
        FakeDB(submission),
    )

    def explode(*args, **kwargs):
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(
        analysis_routes,
        "analyze_uploaded_submission",
        explode,
    )

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/api/analyze",
        json={"submission_id": str(submission_id)},
        headers={
            "Authorization": f"Bearer {create_token(user_id)}",
        },
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == "Analysis failed unexpectedly."
    assert "sensitive internal detail" not in response.get_data(as_text=True)


def test_analyze_route_handles_invalid_stored_filename(monkeypatch):
    user_id = "owner-123"
    submission_id = ObjectId()

    submission = {
        "_id": submission_id,
        "user_id": user_id,
        "submission_type": "patch",
        "filename": None,
        "content": """\
diff --git a/example.py b/example.py
new file mode 100644
--- /dev/null
+++ b/example.py
@@ -0,0 +1 @@
+print("hello")
""",
    }

    monkeypatch.setattr(
        analysis_routes,
        "db",
        FakeDB(submission),
    )

    captured = {}

    def fake_analyze_uploaded_submission(
        content,
        *,
        submission_id,
        submission_type,
        filename,
        client=None,
    ):
        captured["filename"] = filename

        return (
            {
                "submission_id": submission_id,
                "review_status": "completed",
                "llm_status": "success",
                "overall_score": 100,
                "summary": "Test review",
                "findings": [],
            },
            {},
        )

    monkeypatch.setattr(
        analysis_routes,
        "analyze_uploaded_submission",
        fake_analyze_uploaded_submission,
    )

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/api/analyze",
        json={"submission_id": str(submission_id)},
        headers={
            "Authorization": f"Bearer {create_token(user_id)}",
        },
    )

    assert response.status_code == 200
    assert captured["filename"] == "submission.patch"