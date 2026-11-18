"""Extra branch coverage for llm_review.api: rate-limiter, env parsing, error mapping, __main__."""
import runpy
import sys

import pytest

from llm_review import api
from llm_review.client import LLMError

KEY = 'k'
H = {'X-LLM-API-Key': KEY}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', KEY)
    return api.create_app()


class _Raises:
    model = 'm'

    def __init__(self, exc):
        self.exc = exc

    def complete(self, *a, **k):
        raise self.exc


def test_limiter_blocks_then_recovers(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
    limiter = api.RateLimiter(limit=2, window_seconds=10)
    assert limiter.check('a') is None and limiter.check('a') is None
    wait = limiter.check('a')
    assert wait == pytest.approx(10.0)
    assert limiter.check('b') is None  # keys are independent
    now[0] += 11
    assert limiter.check('a') is None  # window slid past the old hits


def test_limiter_retry_after_shrinks_over_time(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
    limiter = api.RateLimiter(limit=1, window_seconds=10)
    limiter.check('a')
    now[0] = 4
    assert limiter.check('a') == pytest.approx(6.0)


@pytest.mark.parametrize('raw,expected', [(None, 5.0), ('', 5.0), ('2.5', 2.5)])
def test_float_env(monkeypatch, raw, expected):
    monkeypatch.delenv('X_TEST', raising=False)
    if raw is not None:
        monkeypatch.setenv('X_TEST', raw)
    assert api._float_env('X_TEST', 5.0) == expected


def test_float_env_invalid_warns(monkeypatch, capsys):
    monkeypatch.setenv('X_TEST', 'abc')
    assert api._float_env('X_TEST', 5.0) == 5.0
    assert 'not a valid number' in capsys.readouterr().err


def test_create_app_reads_model_and_timeout_env(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', KEY)
    monkeypatch.setenv('LLM_API_MODEL', 'custom:1b')
    monkeypatch.setenv('LLM_API_TIMEOUT_SECONDS', '7')
    app = api.create_app()
    assert app.config['LLM_CLIENT'].model == 'custom:1b' and app.config['LLM_CLIENT'].timeout == 7.0


@pytest.mark.parametrize('body', ['text', ['list'], None])
def test_non_object_body_rejected(app, body):
    response = app.test_client().post('/api/llm/generate', json=body, headers=H)
    assert response.status_code == 400


def test_generic_llm_error_maps_to_502(app):
    app.config['LLM_CLIENT'] = _Raises(LLMError('weird'))
    response = app.test_client().post('/api/llm/generate', json={'prompt': 'x'}, headers=H)
    assert response.status_code == 502


def test_unexpected_exception_maps_to_500_without_leaking(app):
    app.config['LLM_CLIENT'] = _Raises(RuntimeError('secret internals'))
    response = app.test_client().post('/api/llm/generate', json={'prompt': 'x'}, headers=H)
    assert response.status_code == 500
    assert 'secret internals' not in response.get_data(as_text=True)


def test_main_block_uses_env_host_and_port(monkeypatch):
    from flask import Flask

    calls = {}
    monkeypatch.setenv('LLM_API_KEY', KEY)
    monkeypatch.setenv('LLM_API_HOST', '100.64.0.1')
    monkeypatch.setenv('LLM_API_PORT', '5555')
    monkeypatch.setattr(Flask, 'run', lambda self, **kw: calls.update(kw))
    runpy.run_module('llm_review.api', run_name='__main__')
    assert calls == {'host': '100.64.0.1', 'port': 5555, 'debug': False}


def test_non_string_system_prompt_rejected(app):
    response = app.test_client().post('/api/llm/generate', json={'prompt': 'x', 'system': 5}, headers=H)
    assert response.status_code == 400
