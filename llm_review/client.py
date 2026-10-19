"""LLM client abstraction.

Three implementations behind one interface:
- MockLLMClient: deterministic, offline. Lets the whole pipeline run and be
  demoed with zero API key and zero network. Use this for your deliverable.
- AnthropicLLMClient: real calls to Claude. Drop in when you have a key.
- OllamaLLMClient: real calls to a locally-running open-weight model (e.g.
  Qwen2.5-Coder) via Ollama. No API key, no internet.

The pipeline depends on the LLMClient protocol, not a concrete provider,
so nothing else changes when you switch -- including to a new provider
entirely (e.g. OpenAI): add a class here with the same complete() method.
"""
from __future__ import annotations

import json
import os
from typing import Protocol

from .types import Category

# Constrains Ollama's output to this shape via its `format` request field
# (JSON-Schema-based structured output / constrained decoding -- supported
# regardless of which model is loaded, since it's enforced by the decoding
# grammar, not the model's own training). The category enum is derived
# from Category directly so this can't silently drift from types.py.
# Matches the shape SYSTEM_PROMPT already asks for in prose; this just
# makes it a hard constraint instead of a request the model can ignore.
_FINDING_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": [c.value for c in Category]},
        "line": {"type": "integer"},
        "title": {"type": "string"},
        "explanation": {"type": "string"},
        "suggested_fix": {"type": "string"},
        "relates_to_static_finding_id": {"type": ["string", "null"]},
    },
    "required": [
        "category", "line", "title", "explanation",
        "suggested_fix", "relates_to_static_finding_id",
    ],
}

FINDINGS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": _FINDING_JSON_SCHEMA},
    },
    "required": ["findings"],
}


class LLMError(Exception):
    """Raised when the LLM call fails or returns unusable output."""


class LLMTimeout(LLMError):
    """Transient failure worth retrying: timeout, rate limit, connection
    error, or a 5xx from the API."""


class LLMPermanentError(LLMError):
    """Non-retryable failure: bad credentials, permission, a malformed
    request, a missing API key, or the SDK not being installed. Retrying
    these wastes latency without any chance of succeeding."""


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Return the raw text response, expected to be a JSON string."""
        ...


class MockLLMClient:
    """Deterministic offline client for development and demos.

    Returns a canned-but-plausible review keyed off what's in the prompt,
    so the pipeline produces real-looking output without a network call.
    """

    def __init__(
        self,
        *,
        fail: bool = False,
        garbage: bool = False,
        permanent_fail: bool = False,
        bad_shape: bool = False,
    ) -> None:
        # fail/garbage/permanent_fail/bad_shape let you exercise the
        # fallback and retry paths in tests.
        self.fail = fail
        self.garbage = garbage
        self.permanent_fail = permanent_fail
        self.bad_shape = bad_shape
        self.call_count = 0  # lets retry tests assert how many attempts ran

    def complete(self, system: str, user: str) -> str:
        self.call_count += 1
        if self.permanent_fail:
            raise LLMPermanentError("mock: simulated permanent failure (e.g. bad API key)")
        if self.fail:
            raise LLMTimeout("mock: simulated timeout")
        if self.garbage:
            return "Sure! Here is your review: (not json at all)"
        if self.bad_shape:
            # Valid JSON, wrong shape: "findings" isn't a list of dicts.
            return json.dumps({"findings": None})

        findings = []
        # Cheap heuristics so the mock reacts to the prompt content.
        if "SELECT" in user and ("f\"" in user or "format" in user):
            findings.append({
                "category": "security",
                "line": _static_line_for(user, "injection"),
                "title": "Possible SQL injection in query",
                "explanation": "User input appears to be interpolated into the "
                               "SQL string, which allows injection.",
                "suggested_fix": "Use a parameterized query with placeholders "
                                 "instead of string interpolation.",
                "relates_to_static_finding_id": _static_id_for(user, "injection"),
            })
        if "db.connect" in user or "connect(" in user:
            findings.append({
                "category": "bug",
                "line": 44,
                "title": "Missing error handling on connection",
                "explanation": "The connection call can raise; there is no "
                               "try/except, so a failure crashes the request.",
                "suggested_fix": "Wrap the connection in try/except and return a "
                                 "handled error.",
                "relates_to_static_finding_id": None,
            })
        if not findings:
            findings.append({
                "category": "missing_docs",
                "line": 1,
                "title": "Add a docstring to the changed function",
                "explanation": "The new/modified function has no docstring, making "
                               "intent harder to review.",
                "suggested_fix": "Add a short docstring describing parameters and "
                                 "return value.",
                "relates_to_static_finding_id": None,
            })
        return json.dumps({"findings": findings})


class AnthropicLLMClient:
    """Real client. Requires the `anthropic` package and ANTHROPIC_API_KEY.

    Kept import-lazy so the module loads (and the mock works) even when the
    SDK isn't installed.
    """

    def __init__(self, model: str = "claude-sonnet-4-6", timeout: float = 30.0) -> None:
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        try:
            import anthropic  # lazy
        except ImportError as e:  # pragma: no cover
            raise LLMPermanentError("anthropic SDK not installed") from e

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMPermanentError("ANTHROPIC_API_KEY not set")

        try:
            client = anthropic.Anthropic(api_key=api_key, timeout=self.timeout)
            resp = client.messages.create(
                model=self.model,
                max_tokens=1500,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APITimeoutError as e:
            raise LLMTimeout(str(e)) from e
        except anthropic.RateLimitError as e:
            raise LLMTimeout(str(e)) from e
        except anthropic.APIConnectionError as e:
            # Broader network failures; APITimeoutError (a subclass) is
            # caught above so it isn't swallowed here.
            raise LLMTimeout(str(e)) from e
        except anthropic.AuthenticationError as e:
            raise LLMPermanentError(str(e)) from e
        except anthropic.PermissionDeniedError as e:
            raise LLMPermanentError(str(e)) from e
        except anthropic.NotFoundError as e:
            raise LLMPermanentError(str(e)) from e
        except anthropic.BadRequestError as e:
            raise LLMPermanentError(str(e)) from e
        except anthropic.APIStatusError as e:
            # Any other HTTP-status error (e.g. a 5xx not covered above):
            # treat server errors as transient, other status codes as
            # permanent, since retrying a non-5xx status is unlikely to help.
            if e.status_code >= 500:
                raise LLMTimeout(str(e)) from e
            raise LLMPermanentError(str(e)) from e
        except Exception as e:
            # Constructor failures and anything else unclassified: fail
            # fast rather than assume it's safe to retry.
            raise LLMPermanentError(str(e)) from e

        # Concatenate text blocks. Checking block.type directly (not via
        # getattr) lets the type checker narrow the union to TextBlock.
        text = "".join(
            block.text for block in resp.content if block.type == "text"
        )
        if not text.strip():
            raise LLMError("empty response")
        return text


class OllamaLLMClient:
    """Local inference via Ollama's REST API -- no API key, no network
    egress beyond localhost. Requires `ollama serve` to already be running
    with the model pulled (e.g. `ollama pull qwen2.5-coder:7b`); this
    client only calls the already-running server, it doesn't manage it.

    Uses only the standard library (urllib) so llm_review doesn't gain a
    new dependency just to support a second provider.
    """

    def __init__(
        self,
        model: str = "qwen2.5-coder:7b",
        host: str = "http://localhost:11434",
        timeout: float = 300.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        import urllib.error
        import urllib.request

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": FINDINGS_RESPONSE_SCHEMA,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except TimeoutError as e:
            raise LLMTimeout(f"Ollama request timed out after {self.timeout}s") from e
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code == 404:
                # Either the API path is wrong or, far more likely, the
                # model hasn't been pulled -- retrying won't fix either.
                raise LLMPermanentError(
                    f"Ollama returned 404 for model {self.model!r} -- is it "
                    f"pulled? Try: ollama pull {self.model}. Detail: {detail}"
                ) from e
            if e.code >= 500:
                raise LLMTimeout(f"Ollama server error {e.code}: {detail}") from e
            raise LLMPermanentError(f"Ollama rejected the request ({e.code}): {detail}") from e
        except urllib.error.URLError as e:
            # Most commonly: `ollama serve` isn't running at all. Treated
            # as transient for consistency with AnthropicLLMClient's
            # APIConnectionError handling, even though a fully-down local
            # server won't come back up between this attempt and the next.
            raise LLMTimeout(f"Could not reach Ollama at {self.host}: {e.reason}") from e

        try:
            parsed = json.loads(body)
        except ValueError as e:
            raise LLMError(f"Ollama returned non-JSON response: {e}") from e

        text = parsed.get("message", {}).get("content", "")
        if not text.strip():
            raise LLMError("empty response from Ollama")
        return text


def _static_id_for(text: str, keyword: str) -> str | None:
    """Find the [sf_...] id on the static-findings line mentioning keyword,
    so the mock references the SAME finding the real static tool reported."""
    import re
    for line in text.splitlines():
        if keyword.lower() in line.lower():
            m = re.search(r"\[(sf_[0-9a-zA-Z_]+)\]", line)
            if m:
                return m.group(1)
    return None


def _static_line_for(text: str, keyword: str) -> int:
    """Find the 'line N' reported on the matching static-findings line."""
    import re
    for line in text.splitlines():
        if keyword.lower() in line.lower():
            m = re.search(r"line (\d+)", line)
            if m:
                return int(m.group(1))
    return 0
