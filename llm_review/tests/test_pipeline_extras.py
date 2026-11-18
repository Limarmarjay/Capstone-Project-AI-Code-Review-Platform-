"""Remaining branch coverage for llm_review.pipeline helpers."""
import time

import pytest

from llm_review.pipeline import (
    _accepts_timeout_kwarg, _coerce_category, _combine, _parse_json, _run_llm,
)
from llm_review.types import Category, Hunk, LLMStatus, ReviewFile, Source, StaticFinding


def _file(static=()):
    return ReviewFile('a.py', 'python', [Hunk('h1', 1, 3, '@@ -1 +1 @@\n+x')], list(static))


class _Scripted:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


# --- _accepts_timeout_kwarg ------------------------------------------------------------

def test_uninspectable_client_is_treated_as_no_timeout_support():
    class NoSig:
        @property
        def complete(self):
            raise TypeError('no signature')

    assert _accepts_timeout_kwarg(NoSig()) is False


# --- _run_llm --------------------------------------------------------------------------------

def test_run_llm_deadline_expiring_between_attempts(monkeypatch):
    from llm_review import pipeline

    # remaining: first check passes, loop check fails
    ticks = iter([0.0, 100.0])
    monkeypatch.setattr(pipeline.time, 'monotonic', lambda: next(ticks))
    client = _Scripted('{"findings": []}')
    assert _run_llm(_file(), client, deadline=10.0) == ([], LLMStatus.PARTIAL)
    assert client.calls == 0


def test_run_llm_retries_invalid_then_succeeds():
    client = _Scripted('garbage', '{"findings": [{"line": 1}]}')
    findings, status = _run_llm(_file(), client, deadline=time.monotonic() + 60)
    assert status is LLMStatus.SUCCESS and findings == [{'line': 1}] and client.calls == 2


def test_run_llm_all_invalid_reports_invalid_response():
    client = _Scripted('garbage', 'nope', 'still no')
    assert _run_llm(_file(), client, deadline=float('inf'))[1] is LLMStatus.INVALID_RESPONSE


# --- _parse_json ------------------------------------------------------------------------------

@pytest.mark.parametrize('raw', [
    '', '   ', None, 'no braces at all', '{not: json}', '[1, 2]', '{"other": 1}',
    '{"findings": "nope"}', '{"findings": [1, 2]}', 'prefix {broken',
])
def test_parse_json_rejects_unusable(raw):
    assert _parse_json(raw) is None


def test_parse_json_accepts_fences_and_surrounding_prose():
    assert _parse_json('```json\n{"findings": []}\n```') == {'findings': []}
    assert _parse_json('Here you go: {"findings": []} Hope that helps!') == {'findings': []}


# --- _coerce_category -----------------------------------------------------------------

def test_coerce_category_falls_back_to_code_smell():
    assert _coerce_category('security') is Category.SECURITY
    for bad in ('nonsense', None, 42, ''):
        assert _coerce_category(bad) is Category.CODE_SMELL


def test_combine_matches_static_finding_on_same_line_without_explicit_id():
    # Matching by line + category alone (no relates_to_static_finding_id) is not covered elsewhere.
    static = StaticFinding('sf1', 'bandit', 'B602', 'security', 'shell=True', 'a.py', 3, 'high')
    llm = {'category': 'security', 'line': 3, 'title': 't', 'explanation': 'e',
           'suggested_fix': 'f', 'severity': 'high'}
    (finding,) = _combine(_file([static]), [llm])
    assert finding.source is Source.COMBINED and finding.static_finding_ids == ['sf1']
