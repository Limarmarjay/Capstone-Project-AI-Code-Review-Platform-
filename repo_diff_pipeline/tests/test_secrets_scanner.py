from repo_diff_pipeline.diff_parser import parse_diff
from repo_diff_pipeline.secrets_scanner import REDACTION_TEXT, scan_and_redact

DIFF_WITH_SECRET = """\
diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -1,1 +1,3 @@
 BASE = 1
+AWS_KEY = "AKIAABCDEFGHIJKLMNOP"
+password = "supersecretpassword123"
"""

DIFF_WITHOUT_SECRET = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,1 +1,2 @@
 def add(a, b):
+    return a + b
"""


def test_scan_finds_and_redacts_secrets():
    diff = parse_diff(DIFF_WITH_SECRET)
    findings = scan_and_redact(diff)

    assert len(findings) == 2
    rule_ids = {f.rule_id for f in findings}
    assert "aws_access_key_id" in rule_ids
    assert "generic_secret_assignment" in rule_ids

    # The raw secret values must not survive in the diff content after redaction.
    all_content = " ".join(line.content for f in diff.files for h in f.hunks for line in h.lines)
    assert "AKIAABCDEFGHIJKLMNOP" not in all_content
    assert "supersecretpassword123" not in all_content
    assert REDACTION_TEXT in all_content


def test_scan_clean_diff_finds_nothing():
    diff = parse_diff(DIFF_WITHOUT_SECRET)
    findings = scan_and_redact(diff)
    assert findings == []


def test_context_lines_are_not_scanned():
    # A "secret-looking" string in a context (unchanged) line should not be
    # flagged — only newly-added lines are attacker-influenced content.
    diff_text = (
        "diff --git a/config.py b/config.py\n"
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -1,2 +1,3 @@\n"
        ' AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n'
        " BASE = 1\n"
        "+NEW = 2\n"
    )
    diff = parse_diff(diff_text)
    findings = scan_and_redact(diff)
    assert findings == []
