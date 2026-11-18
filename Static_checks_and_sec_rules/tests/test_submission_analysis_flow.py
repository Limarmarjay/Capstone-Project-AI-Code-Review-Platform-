"""Tests for submission_analysis orchestration (LLM replaced by a mock/recording client)."""
import pytest

from Static_checks_and_sec_rules.services import submission_analysis as sa
from llm_review.client import MockLLMClient
from repo_diff_pipeline.diff_parser import parse_diff

DIFF = (
    "diff --git a/risky.py b/risky.py\nnew file mode 100644\n--- /dev/null\n+++ b/risky.py\n"
    "@@ -0,0 +1,3 @@\n+import subprocess\n+def run(cmd):\n+    subprocess.call(cmd, shell=True)\n"
)


def _kwargs(**over):
    base = dict(submission_id='s1', submission_type='patch', filename='risky.py')
    base.update(over)
    return base


def test_source_types_cover_all_submission_kinds():
    assert set(sa.SOURCE_TYPES) == {'patch', 'diff', 'pull_request'}


@pytest.mark.parametrize('fn_arg', [parse_diff(DIFF), DIFF])
def test_unsupported_type_rejected(fn_arg):
    fn = sa.analyze_parsed_submission if not isinstance(fn_arg, str) else sa.analyze_uploaded_submission
    with pytest.raises(sa.SubmissionAnalysisError, match='Unsupported'):
        fn(fn_arg, **_kwargs(submission_type='zip'))


def test_over_limit_rejected(monkeypatch):
    monkeypatch.setattr(sa, 'MAX_CHANGED_LINES', 1)
    with pytest.raises(sa.SubmissionAnalysisError, match='5000-line'):
        sa.analyze_parsed_submission(parse_diff(DIFF), **_kwargs())


def test_parsed_submission_returns_review_and_scan_issues():
    review, scan_issues = sa.analyze_parsed_submission(parse_diff(DIFF), **_kwargs())
    assert review['submission_id'] == 's1'
    assert scan_issues == {}
    assert any(f.get('static_finding_ids') for f in review['findings'])  # bandit finding surfaced


def test_default_client_is_mock_and_wrapped_in_sanitizer(monkeypatch):
    seen = {}
    real_review = sa.review

    def spy(analysis_input, client):
        seen['client'] = client
        return real_review(analysis_input, client=client)

    monkeypatch.setattr(sa, 'review', spy)
    sa.analyze_parsed_submission(parse_diff(DIFF), **_kwargs())
    assert isinstance(seen['client'], sa.SanitizingClient)
    assert isinstance(seen['client']._client, MockLLMClient)


def test_custom_client_receives_redacted_prompt():
    class Recorder:
        def __init__(self):
            self.user = None

        def complete(self, system, user):
            self.user = user
            return '{"findings": []}'

    secret_diff = (
        "diff --git a/s.py b/s.py\nnew file mode 100644\n--- /dev/null\n+++ b/s.py\n"
        "@@ -0,0 +1 @@\n+password = 'CANARY_7f3a9e_value'\n"
    )
    recorder = Recorder()
    sa.analyze_parsed_submission(parse_diff(secret_diff), **_kwargs(client=recorder))
    assert 'CANARY_7f3a9e_value' not in recorder.user


# --- analyze_uploaded_submission -----------------------------------------------------

def test_uploaded_invalid_input():
    with pytest.raises(sa.SubmissionAnalysisError, match='Invalid input'):
        sa.analyze_uploaded_submission('plain prose', **_kwargs())


def test_uploaded_parse_failure(monkeypatch):
    def boom(_):
        raise sa.DiffParseError('bad')

    monkeypatch.setattr(sa, 'parse_diff', boom)
    with pytest.raises(sa.SubmissionAnalysisError, match='bad'):
        sa.analyze_uploaded_submission(DIFF, **_kwargs())


def test_uploaded_happy_path():
    review, _ = sa.analyze_uploaded_submission(DIFF, **_kwargs())
    assert review['submission_id'] == 's1'
