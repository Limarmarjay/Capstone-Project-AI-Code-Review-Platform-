import pytest

from repo_diff_pipeline.checks import apply_security_rules
from repo_diff_pipeline.diff_parser import parse_diff
from repo_diff_pipeline.repository import UnsafeSourceError, _validate_remote_url

RISKY_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,1 +1,4 @@
 import subprocess
+subprocess.run(cmd, shell=True)
+eval(user_input)
+requests.get(url, verify=False)
"""


def test_security_rules_catch_shell_true():
    diff = parse_diff(RISKY_DIFF)
    result = apply_security_rules(diff.files)
    rule_ids = {f.rule_id for f in result.findings}
    assert "shell_true_subprocess" in rule_ids
    assert "eval_or_exec_call" in rule_ids
    assert "disabled_tls_verification" in rule_ids


def test_security_rules_clean_diff_has_no_findings():
    clean_diff = (
        "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
        "@@ -1,1 +1,2 @@\n def add(a, b):\n+    return a + b\n"
    )
    diff = parse_diff(clean_diff)
    result = apply_security_rules(diff.files)
    assert result.findings == []


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "ftp://example.com/repo.git", "notaurl"],
)
def test_unsafe_url_schemes_are_rejected(url):
    with pytest.raises(UnsafeSourceError):
        _validate_remote_url(url)


@pytest.mark.parametrize("url", ["https://github.com/org/repo.git", "ssh://git@github.com/org/repo.git"])
def test_safe_url_schemes_are_accepted(url):
    _validate_remote_url(url)  # should not raise
