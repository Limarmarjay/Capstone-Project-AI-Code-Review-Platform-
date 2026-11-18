"""Error-mapping and edge-case tests for llm_review.client (Anthropic SDK and urllib fully mocked)."""
import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import anthropic
import pytest

from llm_review import client as c


def _exc(cls, message='boom', status=None):
    e = cls.__new__(cls)
    Exception.__init__(e, message)
    e.message = message
    if status is not None:
        e.status_code = status
    return e


def _install_fake_sdk(monkeypatch, *, raises=None, blocks=None):
    class Messages:
        def create(self, **kwargs):
            Messages.kwargs = kwargs
            if raises is not None:
                raise raises
            return SimpleNamespace(content=blocks if blocks is not None else [SimpleNamespace(type='text', text='hi')])

    class Fake:
        def __init__(self, api_key=None, timeout=None):
            Fake.init = (api_key, timeout)
            self.messages = Messages()

    monkeypatch.setattr(anthropic, 'Anthropic', Fake)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'k')
    return Fake, Messages


# --- AnthropicLLMClient -------------------------------------------------------------

def test_anthropic_success_joins_text_blocks_and_clamps_timeout(monkeypatch):
    blocks = [SimpleNamespace(type='text', text='a'), SimpleNamespace(type='tool_use', text='x'),
              SimpleNamespace(type='text', text='b')]
    Fake, Messages = _install_fake_sdk(monkeypatch, blocks=blocks)
    assert c.AnthropicLLMClient(timeout=30).complete('sys', 'usr', timeout=5) == 'ab'
    assert Fake.init == ('k', 5)  # min(client timeout, per-call timeout)
    assert Messages.kwargs['system'] == 'sys'


def test_anthropic_empty_response(monkeypatch):
    _install_fake_sdk(monkeypatch, blocks=[SimpleNamespace(type='text', text='   ')])
    with pytest.raises(c.LLMError, match='empty'):
        c.AnthropicLLMClient().complete('s', 'u')


@pytest.mark.parametrize('sdk_exc,expected', [
    (_exc(anthropic.APITimeoutError), c.LLMTimeout),
    (_exc(anthropic.APIConnectionError), c.LLMTimeout),
    (_exc(anthropic.PermissionDeniedError, status=403), c.LLMPermanentError),
    (_exc(anthropic.NotFoundError, status=404), c.LLMPermanentError),
    (_exc(anthropic.InternalServerError, status=500), c.LLMTimeout),
])
def test_anthropic_error_classification(monkeypatch, sdk_exc, expected):
    _install_fake_sdk(monkeypatch, raises=sdk_exc)
    with pytest.raises(expected) as info:
        c.AnthropicLLMClient().complete('s', 'u')
    assert type(info.value) is expected or isinstance(info.value, expected)


def test_connection_error_is_a_timeout_subclass_but_distinct():
    assert issubclass(c.LLMConnectionError, c.LLMTimeout)
    assert issubclass(c.LLMTimeout, c.LLMError) and issubclass(c.LLMPermanentError, c.LLMError)


# --- OllamaLLMClient ----------------------------------------------------------------

class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ollama(monkeypatch, outcome):
    def urlopen(req, timeout=None):
        urlopen.req, urlopen.timeout = req, timeout
        if isinstance(outcome, Exception):
            raise outcome
        return _Resp(outcome)

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    return urlopen


def _http_error(code, body=b'detail'):
    return urllib.error.HTTPError('http://x', code, 'm', {}, io.BytesIO(body))


def test_ollama_other_4xx_is_permanent(monkeypatch):
    _ollama(monkeypatch, _http_error(400))
    with pytest.raises(c.LLMPermanentError, match='400'):
        c.OllamaLLMClient().complete('s', 'u')


def test_ollama_non_json_body(monkeypatch):
    _ollama(monkeypatch, b'<html>')
    with pytest.raises(c.LLMError, match='non-JSON'):
        c.OllamaLLMClient().complete('s', 'u')


# --- mock helpers ------------------------------------------------------------------------

def test_static_helpers_find_ids_and_lines():
    text = 'noise\n[sf_bandit_1] subprocess shell=True at line 12\nother'
    assert c._static_id_for(text, 'subprocess') == 'sf_bandit_1'
    assert c._static_line_for(text, 'subprocess') == 12


def test_static_helpers_return_defaults_when_missing():
    assert c._static_id_for('nothing here', 'subprocess') is None
    assert c._static_line_for('nothing here', 'subprocess') == 0
    assert c._static_id_for('subprocess but no id', 'subprocess') is None
    assert c._static_line_for('subprocess but no number', 'subprocess') == 0
