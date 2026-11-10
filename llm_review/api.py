"""Standalone Flask app exposing the local Ollama model to other teams.

This is deliberately a SEPARATE app/process from backend/app -- it is the
only thing meant to ever be reachable from outside this machine (e.g. over
a Tailscale tunnel). backend/app has its own routes, auth, and database
access; tunneling that whole app would expose all of it, not just model
access. Ollama itself (port 11434) is never exposed either -- only this
app's own port is.

Run it:
    python -m llm_review.api

Requires LLM_API_KEY to be set (see .env.example) -- the app refuses to
start without it, since an unauthenticated port on a tunnel is an open
door to this machine's local model.
"""
from __future__ import annotations

import hmac
import os
import sys
import threading
import time
from collections import deque

from flask import Blueprint, Flask, current_app, jsonify, request

from .client import (
    LLMConnectionError,
    LLMError,
    LLMPermanentError,
    LLMTimeout,
    OllamaLLMClient,
)

DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5100

MAX_INPUT_CHARS = 20_000
RATE_LIMIT_PER_MINUTE = 10
RATE_LIMIT_WINDOW_SECONDS = 60.0

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


class RateLimiter:
    """In-process sliding-window limiter, keyed by API key.

    Module-level state guarded by a lock -- correct for the single Flask
    process this app runs as (dev server or a single gunicorn worker), not
    a substitute for a shared store across multiple processes/workers.
    """

    def __init__(self, *, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """Record a hit for `key` and return None if it's within the
        limit, or the number of seconds to wait before retrying."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self._window_seconds:
                hits.popleft()
            if len(hits) >= self._limit:
                return max(self._window_seconds - (now - hits[0]), 0.0)
            hits.append(now)
            return None


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(
            f"Warning: {name}={raw!r} is not a valid number; using default {default}.",
            file=sys.stderr,
        )
        return default


generate_bp = Blueprint("llm_generate", __name__, url_prefix="/api/llm")


def _check_auth() -> bool:
    expected = current_app.config["LLM_API_KEY"]
    provided = request.headers.get("X-LLM-API-Key", "")
    return hmac.compare_digest(provided, expected)


@generate_bp.post("/generate")
def generate():
    if not _check_auth():
        return jsonify({"error": "Missing or invalid API key."}), 401

    api_key = request.headers.get("X-LLM-API-Key", "")
    limiter: RateLimiter = current_app.config["RATE_LIMITER"]
    retry_after = limiter.check(api_key)
    if retry_after is not None:
        response = jsonify({"error": "Rate limit exceeded."})
        response.headers["Retry-After"] = str(int(retry_after) + 1)
        return response, 429

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "'prompt' is required and must be a non-empty string."}), 400

    system = body.get("system", DEFAULT_SYSTEM_PROMPT)
    if not isinstance(system, str):
        return jsonify({"error": "'system' must be a string if provided."}), 400

    if len(prompt) + len(system) > MAX_INPUT_CHARS:
        return jsonify({"error": f"Input exceeds the {MAX_INPUT_CHARS}-character limit."}), 413

    client: OllamaLLMClient = current_app.config["LLM_CLIENT"]
    start = time.monotonic()
    try:
        text = client.complete(system, prompt, json_schema=None)
    except LLMPermanentError:
        current_app.logger.exception("LLM generate failed: permanent error")
        return jsonify({"error": "The language model is unavailable."}), 502
    except LLMConnectionError:
        # Must be caught before LLMTimeout -- it's a subclass, and this
        # is the "unreachable" case specifically (connection refused),
        # not a request that was sent but didn't come back in time.
        current_app.logger.exception("LLM generate failed: could not reach Ollama")
        return jsonify({"error": "The language model is unavailable."}), 502
    except LLMTimeout:
        current_app.logger.exception("LLM generate failed: timed out")
        return jsonify({"error": "The language model timed out."}), 504
    except LLMError:
        current_app.logger.exception("LLM generate failed: unclassified LLM error")
        return jsonify({"error": "The language model is unavailable."}), 502
    except Exception:
        current_app.logger.exception("Unexpected failure in /api/llm/generate")
        return jsonify({"error": "Unexpected error."}), 500

    latency = time.monotonic() - start
    return jsonify(
        {
            "text": text,
            "model": client.model,
            "latency_seconds": round(latency, 3),
        }
    )


def create_app() -> Flask:
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY is not set. Refusing to start: without it this "
            "app would accept requests from anyone who can reach the "
            "port. Set LLM_API_KEY in the environment (see .env.example) "
            "and retry."
        )

    app = Flask(__name__)
    app.config["LLM_API_KEY"] = api_key
    app.config["LLM_CLIENT"] = OllamaLLMClient(
        model=os.environ.get("LLM_API_MODEL", DEFAULT_MODEL),
        timeout=_float_env("LLM_API_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
    )
    app.config["RATE_LIMITER"] = RateLimiter(
        limit=RATE_LIMIT_PER_MINUTE, window_seconds=RATE_LIMIT_WINDOW_SECONDS
    )

    app.register_blueprint(generate_bp)
    return app


if __name__ == "__main__":
    # Defaults to loopback-only -- reachable from this machine alone.
    # Set LLM_API_HOST to this Mac's Tailscale IP (not 0.0.0.0) when you
    # actually want to share it; see the README for the full tunneling
    # setup. Ollama itself stays bound to localhost regardless
    # (OllamaLLMClient's default host), so only this app is ever exposed.
    host = os.environ.get("LLM_API_HOST", DEFAULT_HOST)
    port = int(os.environ.get("LLM_API_PORT", str(DEFAULT_PORT)))
    create_app().run(host=host, port=port, debug=False)
