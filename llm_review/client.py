"""LLM client abstraction.

Two implementations behind one interface:
- MockLLMClient: deterministic, offline. Lets the whole pipeline run and be
  demoed with zero API key and zero network. Use this for your deliverable.
- AnthropicLLMClient: real calls. Drop in when you have a key; swap the
  provider for OpenAI by changing only this file.

The pipeline depends on the LLMClient protocol, not a concrete provider,
so nothing else changes when you switch.
"""
from __future__ import annotations

import json
import os
from typing import Protocol


class LLMError(Exception):
    """Raised when the LLM call fails or returns unusable output."""


class LLMTimeout(LLMError):
    """Raised specifically on timeout, so the pipeline can distinguish it."""


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Return the raw text response, expected to be a JSON string."""
        ...


class MockLLMClient:
    """Deterministic offline client for development and demos.

    Returns a canned-but-plausible review keyed off what's in the prompt,
    so the pipeline produces real-looking output without a network call.
    """

    def __init__(self, *, fail: bool = False, garbage: bool = False) -> None:
        # fail/garbage let you exercise the fallback path in tests.
        self.fail = fail
        self.garbage = garbage

    def complete(self, system: str, user: str) -> str:
        if self.fail:
            raise LLMTimeout("mock: simulated timeout")
        if self.garbage:
            return "Sure! Here is your review: (not json at all)"

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
            raise LLMError("anthropic SDK not installed") from e

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key, timeout=self.timeout)
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=1500,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:  # network, timeout, rate limit, etc.
            # Normalize everything into our own error types.
            name = type(e).__name__.lower()
            if "timeout" in name:
                raise LLMTimeout(str(e)) from e
            raise LLMError(str(e)) from e

        # Concatenate text blocks. Checking block.type directly (not via
        # getattr) lets the type checker narrow the union to TextBlock.
        text = "".join(
            block.text for block in resp.content if block.type == "text"
        )
        if not text.strip():
            raise LLMError("empty response")
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
