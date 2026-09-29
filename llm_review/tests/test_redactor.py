import json

import pytest
from pathlib import Path

from Static_checks_and_sec_rules.services.bandit_scanner import scan_python_file
from Static_checks_and_sec_rules.services.redactor import redact_findings, redact_text


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = REPO_ROOT / "Static_checks_and_sec_rules"
SAMPLES = STATIC_ROOT / "samples"


def test_secret_removed_from_scanner_results():
    results = scan_python_file(str(SAMPLES / "vulnerable.py"))

    redacted_results = redact_findings(results)

    serialized = json.dumps(redacted_results)

    assert "super_secret_password" not in serialized
    assert "<REDACTED_SECRET>" in serialized
    assert "shell=True" in serialized


def test_triple_quoted_string_redaction():
    text = 'password = """super_secret_password"""' 

    redacted = redact_text(text)

    assert "super_secret_password" not in redacted
    assert redacted.count("\n") == text.count("\n")


def test_multiline_triple_quoted_string_redaction():
    text = '''password = """super
    secret
    password"""''' 

    redacted = redact_text(text)

    assert "super" not in redacted
    assert "<REDACTED_SECRET>" in redacted
    assert redacted.count("\n") == text.count("\n")


def test_password_keyword_argument_redaction():
    text = 'db.connect(password="hunter2")'

    redacted = redact_text(text)

    assert "hunter2" not in redacted
    assert '<REDACTED_SECRET>' in redacted


def test_password_dictionary_value_redaction():
    text = '{"password": "hunter2"}'

    redacted = redact_text(text)

    assert "hunter2" not in redacted
    assert '<REDACTED_SECRET>' in redacted


def test_api_key_assignment_redaction():
    text = 'API_KEY = "abc123secret"'

    redacted = redact_text(text)

    assert "abc123secret" not in redacted
    assert '<REDACTED_SECRET>' in redacted


def test_api_key_dictionary_value_redaction():
    text = '{"api_key": "abc123secret"}'

    redacted = redact_text(text)

    assert "abc123secret" not in redacted
    assert '<REDACTED_SECRET>' in redacted


def test_token_assignment_redaction():
    text = 'token = "abc123"'

    redacted = redact_text(text)

    assert "abc123" not in redacted
    assert '<REDACTED_SECRET>' in redacted


def test_secret_dictionary_redaction():
    text = '{"secret": "topsecret"}'

    redacted = redact_text(text)

    assert "topsecret" not in redacted
    assert '<REDACTED_SECRET>' in redacted
    assert redacted == '{"secret": "<REDACTED_SECRET>"}'


def test_redaction_does_not_modify_original_results():
    results = scan_python_file(str(SAMPLES / "vulnerable.py"))

    redacted_results = redact_findings(results)

    original_serialized = json.dumps(results)
    redacted_serialized = json.dumps(redacted_results)

    assert "super_secret_password" in original_serialized
    assert "super_secret_password" not in redacted_serialized   


def test_secret_key_assignment_redaction():
    text = 'SECRET_KEY = "flask-secret-value"'

    redacted = redact_text(text)

    assert "flask-secret-value" not in redacted
    assert "<REDACTED_SECRET>" in redacted


def test_aws_secret_access_key_redaction():
    text = 'AWS_SECRET_ACCESS_KEY = "aws-secret-value"'

    redacted = redact_text(text)

    assert "aws-secret-value" not in redacted
    assert "<REDACTED_SECRET>" in redacted


def test_normal_username_is_not_redacted():
    text = 'username = "jalen"'

    redacted = redact_text(text)

    assert redacted == text


def test_passwd_assignment_redaction():
    text = 'passwd = "hunter2"'

    redacted = redact_text(text)

    assert "hunter2" not in redacted
    assert "<REDACTED_SECRET>" in redacted


def test_pwd_assignment_redaction():
    text = 'pwd = "hunter2"'

    redacted = redact_text(text)

    assert "hunter2" not in redacted
    assert "<REDACTED_SECRET>" in redacted


@pytest.mark.xfail(
    strict=True,
    reason="Typed secret assignments are not supported by regex MVP"
)
def test_typed_password_assignment_known_limitation():
    text = 'password: str = "hunter2"'

    redacted = redact_text(text)

    assert "hunter2" not in redacted


def test_password_comparison_redaction():
    text = 'if password == "hunter2":'

    redacted = redact_text(text)

    assert "hunter2" not in redacted
    assert "<REDACTED_SECRET>" in redacted


def test_f_string_secret_redaction():
    text = 'API_KEY = f"sk-live-123"'

    redacted = redact_text(text)

    assert "sk-live-123" not in redacted
    assert 'f"<REDACTED_SECRET>"' in redacted


def test_bytes_secret_redaction():
    text = 'API_KEY = b"sk-live-123"'

    redacted = redact_text(text)

    assert "sk-live-123" not in redacted
    assert 'b"<REDACTED_SECRET>"' in redacted    


def test_redacts_secret_in_unknown_field():
    results = {
        "findings": [
            {
                "source": "other_tool",
                "snippet": 'token = "abc123"',
            }
        ]
    }

    redacted = redact_findings(results)

    assert "abc123" not in json.dumps(redacted)


@pytest.mark.xfail(
    strict=True,
    reason="Flask config subscript secrets are not supported by the regex MVP"
)
def test_flask_secret_key_subscript_known_limitation():
    text = 'app.config["SECRET_KEY"] = "flask-secret-value"'
    
    redacted = redact_text(text)

    assert "flask-secret-value" not in redacted


@pytest.mark.xfail(
    strict=True,
    reason="Escaped quotes inside secret values are not supported by the regex MVP"
)
def test_escaped_quote_in_secret_known_limitation():
    text = 'password = "ab\\"cd"'

    redacted = redact_text(text)

    assert redacted == 'password = "<REDACTED_SECRET>"'


@pytest.mark.xfail(
    strict=True,
    reason="Truncated triple-quoted secrets are not supported by the regex MVP"
)
def test_truncated_triple_quoted_secret_known_limitation():
    text = 'password = """super_secret_password'

    redacted = redact_text(text)

    assert "super_secret_password" not in redacted 


@pytest.mark.xfail(
    strict=True,
    reason="Secret defaults inside environment lookup calls are not supported by the regex MVP"
)
def test_environment_secret_default_known_limitation():
    text = 'os.environ.get("SECRET_KEY", "dev-secret-value")'

    redacted = redact_text(text)

    assert "dev-secret-value" not in redacted
