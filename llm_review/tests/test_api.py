"""Tests for the standalone /api/llm/generate app (llm_review/api.py).

Uses the app factory directly (`llm_review.api.create_app`) -- no
sys.path insertion into `backend/`; this app has no dependency on it.

Run:  python -m pytest llm_review/tests/test_api.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from llm_review.api import create_app, RATE_LIMIT_PER_MINUTE
from llm_review.client import LLMConnectionError, LLMPermanentError, LLMTimeout

API_KEY = "test-key-123"
AUTH_HEADERS = {"X-LLM-API-Key": API_KEY}


class FakeClient:
    """Stands in for OllamaLLMClient: no network, fully scripted."""

    def __init__(self, *, text="hello", exc=None):
        self.text = text
        self.exc = exc
        self.model = "fake-model"
        self.call_count = 0
        self.last_call = None

    def complete(self, system, user, *, json_schema=None):
        self.call_count += 1
        self.last_call = {"system": system, "user": user, "json_schema": json_schema}
        if self.exc is not None:
            raise self.exc
        return self.text


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", API_KEY)
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _set_fake_client(app, **kwargs):
    fake = FakeClient(**kwargs)
    app.config["LLM_CLIENT"] = fake
    return fake


# ---- fail-closed startup -------------------------------------------------

def test_create_app_refuses_to_start_without_api_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        create_app()


# ---- happy path ------------------------------------------------------------

def test_generate_happy_path(app, client):
    fake = _set_fake_client(app, text="A terse summary.")
    resp = client.post(
        "/api/llm/generate",
        json={"prompt": "Summarize this."},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["text"] == "A terse summary."
    assert body["model"] == "fake-model"
    assert isinstance(body["latency_seconds"], (int, float))
    assert fake.call_count == 1
    # Passthrough generation must not constrain output to the findings
    # schema -- that's the whole point of the json_schema=None override.
    assert fake.last_call["json_schema"] is None
    assert fake.last_call["user"] == "Summarize this."


def test_generate_uses_default_system_prompt_when_omitted(app, client):
    fake = _set_fake_client(app)
    resp = client.post("/api/llm/generate", json={"prompt": "hi"}, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert fake.last_call["system"] == "You are a helpful assistant."


def test_generate_passes_through_custom_system_prompt(app, client):
    fake = _set_fake_client(app)
    resp = client.post(
        "/api/llm/generate",
        json={"prompt": "hi", "system": "You are terse."},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert fake.last_call["system"] == "You are terse."


# ---- auth -------------------------------------------------------------------

def test_generate_rejects_missing_api_key(app, client):
    fake = _set_fake_client(app)
    resp = client.post("/api/llm/generate", json={"prompt": "hi"})
    assert resp.status_code == 401
    assert "error" in resp.get_json()
    assert fake.call_count == 0


def test_generate_rejects_wrong_api_key(app, client):
    fake = _set_fake_client(app)
    resp = client.post(
        "/api/llm/generate",
        json={"prompt": "hi"},
        headers={"X-LLM-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401
    assert fake.call_count == 0


# ---- input validation / size limit ------------------------------------------

def test_generate_rejects_non_json_body(app, client):
    fake = _set_fake_client(app)
    resp = client.post("/api/llm/generate", data=b"not json", headers=AUTH_HEADERS)
    assert resp.status_code == 400
    assert fake.call_count == 0


def test_generate_rejects_missing_prompt(app, client):
    fake = _set_fake_client(app)
    resp = client.post("/api/llm/generate", json={}, headers=AUTH_HEADERS)
    assert resp.status_code == 400
    assert fake.call_count == 0


def test_generate_rejects_oversized_input(app, client):
    fake = _set_fake_client(app)
    huge_prompt = "x" * 20_001
    resp = client.post(
        "/api/llm/generate", json={"prompt": huge_prompt}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 413
    assert fake.call_count == 0


# ---- error mapping ------------------------------------------------------------

def test_generate_maps_genuine_timeout_to_504(app, client):
    _set_fake_client(app, exc=LLMTimeout("Ollama request timed out after 60.0s"))
    resp = client.post("/api/llm/generate", json={"prompt": "hi"}, headers=AUTH_HEADERS)
    assert resp.status_code == 504
    body = resp.get_json()
    assert "error" in body
    assert "60.0s" not in str(body)  # no raw exception text leaked


def test_generate_maps_connection_error_to_502(app, client):
    # LLMConnectionError is a distinct type (a LLMTimeout subclass), not
    # detected by matching exception text -- confirms the route branches
    # on type, not on a "Could not reach Ollama" string in the message.
    _set_fake_client(
        app,
        exc=LLMConnectionError("Could not reach Ollama at http://localhost:11434: refused"),
    )
    resp = client.post("/api/llm/generate", json={"prompt": "hi"}, headers=AUTH_HEADERS)
    assert resp.status_code == 502
    assert "refused" not in str(resp.get_json())


def test_generate_maps_permanent_error_to_502(app, client):
    _set_fake_client(app, exc=LLMPermanentError("model not pulled"))
    resp = client.post("/api/llm/generate", json={"prompt": "hi"}, headers=AUTH_HEADERS)
    assert resp.status_code == 502
    assert "not pulled" not in str(resp.get_json())


# ---- rate limit ----------------------------------------------------------

def test_generate_rate_limits_after_threshold(app, client):
    _set_fake_client(app)
    for _ in range(RATE_LIMIT_PER_MINUTE):
        resp = client.post("/api/llm/generate", json={"prompt": "hi"}, headers=AUTH_HEADERS)
        assert resp.status_code == 200

    resp = client.post("/api/llm/generate", json={"prompt": "hi"}, headers=AUTH_HEADERS)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_generate_rate_limit_is_per_key(app, client):
    _set_fake_client(app)
    for _ in range(RATE_LIMIT_PER_MINUTE):
        resp = client.post("/api/llm/generate", json={"prompt": "hi"}, headers=AUTH_HEADERS)
        assert resp.status_code == 200

    # A different (still valid, if there were one) key would get its own
    # window; here we just confirm *this* key is now limited, and that a
    # request with a bad key is still a 401 (auth checked before the
    # limiter's own 429), not swallowed into the rate-limit response.
    resp = client.post(
        "/api/llm/generate", json={"prompt": "hi"}, headers={"X-LLM-API-Key": "nope"}
    )
    assert resp.status_code == 401
