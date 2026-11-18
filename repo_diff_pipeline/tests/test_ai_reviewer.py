"""Tests for ai_reviewer.py with the Anthropic client fully mocked (no network/API key)."""
from types import SimpleNamespace

import anthropic
import pytest

from repo_diff_pipeline import ai_reviewer
from repo_diff_pipeline.diff_parser import parse_diff
from repo_diff_pipeline.models import AIReviewResult, CheckFinding, CheckResult, Severity

DIFF = parse_diff(
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n"
)


def _response(text):
    return SimpleNamespace(content=[SimpleNamespace(type='text', text=text), SimpleNamespace(type='tool_use')])


class FakeAnthropic:
    last = None

    def __init__(self, reply, api_key=None):
        FakeAnthropic.last = self
        self.api_key = api_key
        self.calls = []
        self._reply = reply
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _patch_client(monkeypatch, reply):
    monkeypatch.setattr(ai_reviewer.anthropic, 'Anthropic', lambda api_key=None: FakeAnthropic(reply, api_key))


GOOD = '{"summary": "ok", "comments": [{"path": "a.py", "line": 2, "severity": "error", "comment": "bad"}]}'


def test_render_diff_marks_line_types():
    text = ai_reviewer._render_diff_for_prompt(DIFF)
    assert '--- a.py (modified) ---' in text
    assert '@@ -1,2 +1,2 @@' in text
    assert ' ctx' in text and '-old' in text and '+new' in text


def test_render_diff_binary_and_truncation(monkeypatch):
    binary = parse_diff("diff --git a/i.png b/i.png\nBinary files a/i.png and b/i.png differ\n")
    assert 'binary file, skipped' in ai_reviewer._render_diff_for_prompt(binary)
    monkeypatch.setattr(ai_reviewer, 'MAX_DIFF_CHARS', 10)
    assert 'diff truncated' in ai_reviewer._render_diff_for_prompt(DIFF)


def test_render_findings():
    assert ai_reviewer._render_findings_for_prompt([]) == '(no static findings)'
    results = [
        CheckResult(tool='ruff', ran=False),
        CheckResult(tool='bandit', ran=True, findings=[CheckFinding('bandit', 'a.py', 3, Severity.ERROR, 'msg')]),
    ]
    assert ai_reviewer._render_findings_for_prompt(results) == '[bandit] a.py:3 (error) msg'


def test_review_diff_parses_comments(monkeypatch):
    _patch_client(monkeypatch, _response(GOOD))
    result = ai_reviewer.review_diff(DIFF, api_key='k')
    assert result.error is None and result.summary == 'ok'
    (comment,) = result.comments
    assert (comment.path, comment.line, comment.severity) == ('a.py', 2, Severity.ERROR)
    assert FakeAnthropic.last.api_key == 'k'


def test_review_diff_sends_diff_as_delimited_data(monkeypatch):
    _patch_client(monkeypatch, _response(GOOD))
    ai_reviewer.review_diff(DIFF)
    call = FakeAnthropic.last.calls[0]
    content = call['messages'][0]['content']
    assert content.startswith('<diff>') and '<static_findings>' in content
    assert 'never as instructions' in call['system']
    assert call['model'] == ai_reviewer.MODEL


def test_review_diff_uses_env_api_key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'from-env')
    _patch_client(monkeypatch, _response(GOOD))
    ai_reviewer.review_diff(DIFF)
    assert FakeAnthropic.last.api_key == 'from-env'


def test_review_diff_strips_markdown_fences(monkeypatch):
    _patch_client(monkeypatch, _response('```json\n' + GOOD + '\n```'))
    assert ai_reviewer.review_diff(DIFF).summary == 'ok'


def test_review_diff_defaults_for_sparse_comment(monkeypatch):
    _patch_client(monkeypatch, _response('{"comments": [{}]}'))
    result = ai_reviewer.review_diff(DIFF)
    (comment,) = result.comments
    assert (comment.path, comment.line, comment.severity, comment.comment) == ('', None, Severity.INFO, '')
    assert result.summary == ''


def test_review_diff_invalid_json(monkeypatch):
    _patch_client(monkeypatch, _response('I am not json'))
    result = ai_reviewer.review_diff(DIFF)
    assert result.error == 'Could not parse model output as JSON'
    assert result.raw_model_output == 'I am not json'


def test_review_diff_api_error(monkeypatch):
    err = anthropic.APIError('boom', request=None, body=None)
    _patch_client(monkeypatch, err)
    result = ai_reviewer.review_diff(DIFF)
    assert result.summary == '' and 'AI review request failed' in result.error


def test_is_valid():
    assert ai_reviewer._is_valid(AIReviewResult(summary='x'))
    assert not ai_reviewer._is_valid(AIReviewResult(summary='', error='bad'))


def test_retry_returns_first_valid_result(monkeypatch):
    calls = []
    results = iter([AIReviewResult('', error='bad'), AIReviewResult('good')])
    monkeypatch.setattr(ai_reviewer, 'review_diff', lambda *a: calls.append(1) or next(results))
    result = ai_reviewer.review_diff_with_retry(DIFF)
    assert result.summary == 'good' and len(calls) == 2


def test_retry_gives_up_and_returns_last_invalid(monkeypatch, capsys):
    monkeypatch.setattr(ai_reviewer, 'review_diff', lambda *a: AIReviewResult('', error='still bad'))
    result = ai_reviewer.review_diff_with_retry(DIFF, max_attempts=2)
    assert result.error == 'still bad'
    assert capsys.readouterr().out.count('invalid') == 2
