"""Tests for llm_review/run_review.py (the CLI) and scripts/check_llm.py."""
import json
import os
import sys
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from llm_review import run_review as rr
from scripts import check_llm as cl

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'mock_analysis_input.json')


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    monkeypatch.setattr(rr, 'load_dotenv', lambda *a, **k: None)
    monkeypatch.setattr(cl, 'load_dotenv', lambda *a, **k: None)
    monkeypatch.delenv('OLLAMA_TIMEOUT_SECONDS', raising=False)


# --- run_review ---------------------------------------------------------------------

def test_default_timeout_reads_valid_env_value(monkeypatch):
    monkeypatch.setenv('OLLAMA_TIMEOUT_SECONDS', '12.5')
    assert rr._default_ollama_timeout() == 12.5


def _run(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['run_review', *args])
    return rr.main()


def test_mock_run_prints_review_json(monkeypatch, capsys):
    assert _run(monkeypatch, FIXTURE) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['review_status'] and 'findings' in out


def test_output_flag_writes_file(monkeypatch, tmp_path, capsys):
    target = tmp_path / 'o.json'
    assert _run(monkeypatch, FIXTURE, '-o', str(target)) == 0
    assert f'Wrote {target}' in capsys.readouterr().out
    assert 'findings' in json.loads(target.read_text())


def test_simulated_failure_modes_still_produce_output(monkeypatch, capsys):
    for flag in ('--fail', '--garbage'):
        assert _run(monkeypatch, FIXTURE, flag) == 0
        assert json.loads(capsys.readouterr().out)['llm_status'] != 'success'


def test_real_and_local_are_mutually_exclusive(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, FIXTURE, '--real', '--local')
    assert exc.value.code == 2


def test_client_selection(monkeypatch):
    chosen = {}

    class A:
        def __init__(self):
            chosen['c'] = 'anthropic'

    class O:
        def __init__(self, **kw):
            chosen.update(c='ollama', **kw)

    monkeypatch.setattr(rr, 'AnthropicLLMClient', A)
    monkeypatch.setattr(rr, 'OllamaLLMClient', O)
    monkeypatch.setattr(rr, 'review', lambda data, client: {'ok': True})

    _run(monkeypatch, FIXTURE, '--real')
    assert chosen['c'] == 'anthropic'
    _run(monkeypatch, FIXTURE, '--local', '--model', 'm', '--ollama-host', 'http://h', '--timeout', '9')
    assert chosen == {'c': 'ollama', 'model': 'm', 'host': 'http://h', 'timeout': 9.0}


def test_run_review_entrypoint(monkeypatch):
    import runpy

    monkeypatch.setattr(sys, 'argv', ['run_review', FIXTURE])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(rr.__file__, run_name='__main__')
    assert exc.value.code == 0


# --- scripts/check_llm.py ------------------------------------------------------------------

def test_check_env_vars_reports_and_never_fails(monkeypatch, capsys):
    assert cl.check_env_vars(300.0) is True
    assert 'not set, using default 300.0' in capsys.readouterr().out
    monkeypatch.setenv('OLLAMA_TIMEOUT_SECONDS', '5')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'k')
    assert cl.check_env_vars(5.0) is True
    out = capsys.readouterr().out
    assert "set to '5' -> effective 5.0" in out and 'informational only' in out


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(monkeypatch, outcome):
    def fake(req, timeout=None):
        if isinstance(outcome, Exception):
            raise outcome
        return _Resp(outcome)

    monkeypatch.setattr(urllib.request, 'urlopen', fake)


def test_reachable_returns_models(monkeypatch, capsys):
    _urlopen(monkeypatch, json.dumps({'models': [{'name': 'a'}]}).encode())
    assert cl.check_ollama_reachable('http://h/') == (True, [{'name': 'a'}])
    assert '[PASS]' in capsys.readouterr().out


@pytest.mark.parametrize('outcome,fragment', [
    (urllib.error.URLError('refused'), 'ollama serve'),
    (TimeoutError(), 'timed out'),
    (b'<html>', 'non-JSON'),
])
def test_unreachable_variants(monkeypatch, capsys, outcome, fragment):
    _urlopen(monkeypatch, outcome)
    assert cl.check_ollama_reachable('http://h') == (False, None)
    assert fragment in capsys.readouterr().out


def test_check_model_pulled(capsys):
    models = [{'name': 'a:1', 'model': 'a:1'}, {'name': 'b'}]
    assert cl.check_model_pulled('a:1', models) is True
    assert cl.check_model_pulled('missing', models) is False
    assert 'ollama pull missing' in capsys.readouterr().out


class _FakeOllama:
    result = '{"findings": []}'

    def __init__(self, **kw):
        pass

    def complete(self, system, user):
        if isinstance(_FakeOllama.result, Exception):
            raise _FakeOllama.result
        return _FakeOllama.result


@pytest.mark.parametrize('result,ok,fragment', [
    ('{"findings": []}', True, '0 finding(s)'),
    ('not json', False, 'failed validation'),
    (cl.LLMPermanentError('x'), False, 'permanent error'),
    (cl.LLMTimeout('x'), False, 'timed out'),
    (cl.LLMError('x'), False, 'error: x'),
])
def test_tiny_request(monkeypatch, capsys, result, ok, fragment):
    monkeypatch.setattr(cl, 'OllamaLLMClient', _FakeOllama)
    _FakeOllama.result = result
    assert cl.check_tiny_request('m', 'http://h', 1.0) is ok
    assert fragment in capsys.readouterr().out


def _main(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['check_llm', *args])
    return cl.main()


def test_main_stops_when_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(cl, 'check_ollama_reachable', lambda host: (False, None))
    assert _main(monkeypatch) == 1
    assert capsys.readouterr().out.count('[SKIP]') == 2


def test_main_stops_when_model_missing(monkeypatch, capsys):
    monkeypatch.setattr(cl, 'check_ollama_reachable', lambda host: (True, []))
    assert _main(monkeypatch, '--model', 'nope') == 1
    assert 'model not pulled' in capsys.readouterr().out


@pytest.mark.parametrize('request_ok,code,msg', [(True, 0, 'All checks passed.'), (False, 1, 'One or more checks failed.')])
def test_main_full_run(monkeypatch, capsys, request_ok, code, msg):
    monkeypatch.setattr(cl, 'check_ollama_reachable', lambda host: (True, [{'name': 'qwen2.5-coder:7b'}]))
    monkeypatch.setattr(cl, 'check_tiny_request', lambda *a: request_ok)
    assert _main(monkeypatch) == code
    assert msg in capsys.readouterr().out


def test_check_llm_entrypoint(monkeypatch):
    import runpy

    monkeypatch.setattr(sys, 'argv', ['check_llm', '--host', 'http://127.0.0.1:1'])
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError('no')))
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(cl.__file__, run_name='__main__')
    assert exc.value.code == 1
