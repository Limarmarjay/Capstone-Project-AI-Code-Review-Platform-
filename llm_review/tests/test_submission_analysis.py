import json

from Static_checks_and_sec_rules.services.submission_analysis import (
    SanitizingClient,
    analyze_uploaded_submission,
)


class RecordingClient:
    def __init__(self):
        self.system = None
        self.user = None

    def complete(self, system: str, user: str) -> str:
        self.system = system
        self.user = user
        return '{"findings": []}'


def test_sanitizing_client_redacts_before_delegate():
    recorder = RecordingClient()
    client = SanitizingClient(recorder)

    client.complete(
        "trusted system prompt",
        'password = "CANARY_7f3a9e"',
    )

    assert recorder.system == "trusted system prompt"
    assert recorder.user is not None
    assert "CANARY_7f3a9e" not in recorder.user
    assert "<REDACTED_SECRET>" in recorder.user


def test_uploaded_patch_reaches_canonical_review_pipeline():
    patch = """\
diff --git a/example.py b/example.py
new file mode 100644
--- /dev/null
+++ b/example.py
@@ -0,0 +1,2 @@
+password = "demo_secret"
+print("hello")
"""

    review_output, scan_issues = analyze_uploaded_submission(
        patch,
        submission_id="sub_demo",
        submission_type="patch",
        filename="demo.patch",
    )

    assert review_output["submission_id"] == "sub_demo"
    assert review_output["review_status"] == "completed"
    assert review_output["llm_status"] == "success"

    assert scan_issues == {}

    assert any(
        "bandit:B105:example.py:1"
        in finding["static_finding_ids"]
        for finding in review_output["findings"]
    )

    assert len(review_output["findings"]) >= 1

    serialized = json.dumps(review_output)
    assert "demo_secret" not in serialized


def test_uploaded_patch_uses_provided_client():
    patch = """\
diff --git a/example.py b/example.py
new file mode 100644
--- /dev/null
+++ b/example.py
@@ -0,0 +1,2 @@
+password = "demo_secret"
+print("hello")
"""

    recorder = RecordingClient()

    review_output, _ = analyze_uploaded_submission(
        patch,
        submission_id="sub_recording",
        submission_type="patch",
        filename="demo.patch",
        client=recorder,
    )

    assert recorder.system is not None
    assert recorder.user is not None
    assert review_output["llm_status"] == "success"

    assert "demo_secret" not in recorder.user


def test_bandit_info_finding_does_not_become_critical_in_upload_review():
    patch = """\
diff --git a/example.py b/example.py
new file mode 100644
--- /dev/null
+++ b/example.py
@@ -0,0 +1,3 @@
+import subprocess
+def run(cmd):
+    subprocess.run(cmd, shell=True)
"""

    review_output, scan_issues = analyze_uploaded_submission(
        patch,
        submission_id="sub_severity",
        submission_type="patch",
        filename="severity.patch",
    )

    assert scan_issues == {}

    b404 = next(
        finding
        for finding in review_output["findings"]
        if any(
            "B404" in static_id
            for static_id in finding["static_finding_ids"]
        )
    )

    assert b404["severity"] != "critical"