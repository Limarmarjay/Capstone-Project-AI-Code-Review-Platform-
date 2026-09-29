"""Tests for the LLM review pipeline.

Run:  python -m pytest tests/ -v
(or with the stdlib: python tests/test_pipeline.py)
"""
import contextlib
import json
import logging
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from llm_review import review
from llm_review.client import AnthropicLLMClient, LLMPermanentError, LLMTimeout, MockLLMClient
from llm_review.severity import assign_severity, score
from llm_review.types import (
    Category,
    LLMStatus,
    ReviewFile,
    Severity,
    StaticFinding,
)
from llm_review.pipeline import (
    _combine,
    _parse_json,
    _run_llm,
    _static_baseline,
    _static_to_finding,
)


# ---- Test infrastructure for client-level tests -------------------------

def _fake_anthropic_module(*, init_exc=None, raise_error_name=None, status_code=None):
    """Build a minimal fake 'anthropic' module with a real-shaped exception
    hierarchy, so client.py's typed except clauses can be exercised without
    a network call or the real SDK raising anything."""
    mod = types.ModuleType("anthropic")

    class APIStatusError(Exception):
        def __init__(self, msg="err", status_code=500):
            super().__init__(msg)
            self.status_code = status_code

    class BadRequestError(APIStatusError):
        def __init__(self, msg="bad request"):
            super().__init__(msg, status_code=400)

    class AuthenticationError(APIStatusError):
        def __init__(self, msg="auth"):
            super().__init__(msg, status_code=401)

    class PermissionDeniedError(APIStatusError):
        def __init__(self, msg="forbidden"):
            super().__init__(msg, status_code=403)

    class NotFoundError(APIStatusError):
        def __init__(self, msg="not found"):
            super().__init__(msg, status_code=404)

    class RateLimitError(APIStatusError):
        def __init__(self, msg="rate limited"):
            super().__init__(msg, status_code=429)

    class APIConnectionError(Exception):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    error_classes = {
        "APIStatusError": APIStatusError,
        "BadRequestError": BadRequestError,
        "AuthenticationError": AuthenticationError,
        "PermissionDeniedError": PermissionDeniedError,
        "NotFoundError": NotFoundError,
        "RateLimitError": RateLimitError,
        "APIConnectionError": APIConnectionError,
        "APITimeoutError": APITimeoutError,
    }
    for name, cls in error_classes.items():
        setattr(mod, name, cls)

    create_exc = None
    if raise_error_name is not None:
        cls = error_classes[raise_error_name]
        if raise_error_name == "APIStatusError" and status_code is not None:
            create_exc = cls(status_code=status_code)
        else:
            create_exc = cls()

    class _Resp:
        content = []

    class _FakeMessages:
        def create(self, **kwargs):
            if create_exc is not None:
                raise create_exc
            return _Resp()

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            if init_exc is not None:
                raise init_exc
            self.messages = _FakeMessages()

    mod.Anthropic = _FakeAnthropic
    return mod


@contextlib.contextmanager
def _installed_fake_anthropic(fake_mod):
    """Temporarily substitute `fake_mod` for `import anthropic`, and set a
    dummy API key, restoring both afterward regardless of test outcome."""
    prev_mod = sys.modules.get("anthropic")
    prev_key = os.environ.get("ANTHROPIC_API_KEY")
    sys.modules["anthropic"] = fake_mod
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    try:
        yield
    finally:
        if prev_mod is not None:
            sys.modules["anthropic"] = prev_mod
        else:
            sys.modules.pop("anthropic", None)
        if prev_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = prev_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)


@contextlib.contextmanager
def _capture_logs(logger_name="llm_review.pipeline"):
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Handler()
    logger = logging.getLogger(logger_name)
    prev_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


@contextlib.contextmanager
def _blocked_import(module_name):
    """Force `import module_name` (or `from module_name import ...`) to
    raise ImportError for the duration of the block, restoring whatever
    was in sys.modules afterward."""
    prev = sys.modules.get(module_name)
    sys.modules[module_name] = None
    try:
        yield
    finally:
        if prev is not None:
            sys.modules[module_name] = prev
        else:
            sys.modules.pop(module_name, None)


def _load_input():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "mock_analysis_input.json")
    with open(path) as f:
        return json.load(f)


def test_happy_path_produces_findings():
    out = review(_load_input(), client=MockLLMClient())
    assert out["review_status"] == "completed"
    assert out["llm_status"] == "success"
    assert len(out["findings"]) >= 1
    assert 0 <= out["overall_score"] <= 100


def test_combine_links_static_and_llm():
    out = review(_load_input(), client=MockLLMClient())
    # The SQL injection is flagged by both static (sf_002) and the LLM,
    # so at least one finding should be source=combined and reference sf_002.
    combined = [f for f in out["findings"] if f["source"] == "combined"]
    assert any("sf_002" in f["static_finding_ids"] for f in combined)


def test_different_categories_on_same_line_do_not_combine():
    static_finding = StaticFinding(
        finding_id="bandit:B404:example.py:1",
        tool="bandit",
        rule_id="B404",
        category="security",
        message="Import of subprocess module.",
        file_path="example.py",
        line=1,
        severity="low",
    )

    review_file = ReviewFile(
        file_path="example.py",
        language="python",
        hunks=[],
        static_findings=[static_finding],
    )

    llm_findings = [
        {
            "category": "missing_docs",
            "line": 1,
            "title": "Add a docstring to the changed function",
            "explanation": "The changed function has no docstring.",
            "suggested_fix": "Add a short docstring.",
            "relates_to_static_finding_id": None,
        }
    ]

    findings = _combine(
        review_file,
        llm_findings,
    )

    assert len(findings) == 2

    static_result = next(
        finding
        for finding in findings
        if static_finding.finding_id in finding.static_finding_ids
    )

    assert static_result.severity == Severity.LOW


def test_no_static_finding_is_dropped():
    out = review(_load_input(), client=MockLLMClient())
    referenced = {sid for f in out["findings"] for sid in f["static_finding_ids"]}
    # sf_001 (unused import) wasn't flagged by the mock LLM, but must still
    # appear as a static-source finding.
    assert "sf_001" in referenced


def test_fallback_on_llm_timeout():
    out = review(_load_input(), client=MockLLMClient(fail=True))
    assert out["llm_status"] == "timeout"
    # Pipeline still completes and still returns the static findings.
    assert out["review_status"] == "completed"
    assert all(f["source"] == "static" for f in out["findings"])
    assert any("sf_002" in f["static_finding_ids"] for f in out["findings"])


def test_fallback_on_garbage_response():
    out = review(_load_input(), client=MockLLMClient(garbage=True))
    assert out["llm_status"] == "invalid_response"
    assert out["review_status"] == "completed"
    assert len(out["findings"]) >= 1  # static findings survive


def test_redaction_gate_skips_llm():
    data = _load_input()
    data["redaction_applied"] = False
    out = review(data, client=MockLLMClient())
    assert out["llm_status"] == "skipped"
    assert all(f["source"] == "static" for f in out["findings"])


def test_severity_escalates_when_confirmed_by_both():
    base = assign_severity(Category.SECURITY)
    escalated = assign_severity(Category.SECURITY, confirmed_by_both=True)
    assert base == Severity.CRITICAL
    assert escalated == Severity.CRITICAL  # already max, stays

    base_bug = assign_severity(Category.BUG)
    esc_bug = assign_severity(Category.BUG, confirmed_by_both=True)
    assert base_bug == Severity.HIGH
    assert esc_bug == Severity.CRITICAL


def test_severity_levels_map_to_numbers():
    assert Severity.CRITICAL.level == 4
    assert Severity.HIGH.level == 3
    assert Severity.MEDIUM.level == 2
    assert Severity.LOW.level == 1


def test_score_clamped():
    class F:
        def __init__(self, sev): self.severity = sev
    assert score([F(Severity.CRITICAL)] * 5) == 0     # floored
    assert score([]) == 100                            # perfect


def test_low_base_severity_is_respected():
    assert (
        assign_severity(
            Category.SECURITY,
            base_severity=Severity.LOW,
        )
        == Severity.LOW
    )


def test_static_finding_uses_tool_baseline_severity():
    static_finding = StaticFinding(
        finding_id="bandit:B602:example.py:3",
        tool="bandit",
        rule_id="B602",
        category="security",
        message="Subprocess call with shell=True.",
        file_path="example.py",
        line=3,
        severity="high",
    )

    review_file = ReviewFile(
        file_path="example.py",
        language="python",
        hunks=[],
        static_findings=[static_finding],
    )

    finding = _static_to_finding(
        review_file,
        static_finding,
    )

    assert finding.severity == Severity.HIGH


def test_confirmed_static_finding_escalates_from_tool_baseline():
    static_finding = StaticFinding(
        finding_id="bandit:B602:example.py:3",
        tool="bandit",
        rule_id="B602",
        category="security",
        message="Subprocess call with shell=True.",
        file_path="example.py",
        line=3,
        severity="high",
    )

    review_file = ReviewFile(
        file_path="example.py",
        language="python",
        hunks=[],
        static_findings=[static_finding],
    )

    llm_findings = [
        {
            "category": "security",
            "line": 3,
            "title": "Shell command execution",
            "explanation": "The shell call may allow unsafe command execution.",
            "suggested_fix": "Avoid shell=True.",
            "relates_to_static_finding_id": "bandit:B602:example.py:3",
        }
    ]

    findings = _combine(
        review_file,
        llm_findings,
    )

    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL


# ---- C3: constructor moved inside try/except -----------------------------

def test_missing_api_key_raises_permanent_error():
    prev = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        client = AnthropicLLMClient()
        try:
            client.complete("sys", "user")
        except LLMPermanentError:
            pass
        else:
            raise AssertionError("expected LLMPermanentError when ANTHROPIC_API_KEY is unset")
    finally:
        if prev is not None:
            os.environ["ANTHROPIC_API_KEY"] = prev


def test_sdk_not_installed_raises_permanent_error():
    prev_mod = sys.modules.get("anthropic")
    sys.modules["anthropic"] = None  # forces `import anthropic` to raise ImportError
    try:
        client = AnthropicLLMClient()
        try:
            client.complete("sys", "user")
        except LLMPermanentError:
            pass
        else:
            raise AssertionError("expected LLMPermanentError when the anthropic SDK is unavailable")
    finally:
        if prev_mod is not None:
            sys.modules["anthropic"] = prev_mod
        else:
            sys.modules.pop("anthropic", None)


def test_constructor_exception_is_caught_as_permanent_not_propagated():
    fake_mod = _fake_anthropic_module(init_exc=RuntimeError("boom"))
    with _installed_fake_anthropic(fake_mod):
        client = AnthropicLLMClient()
        try:
            client.complete("sys", "user")
        except LLMPermanentError:
            pass
        except RuntimeError:
            raise AssertionError(
                "constructor exception propagated uncaught -- the "
                "Anthropic(...) call is not inside the try/except"
            )
        else:
            raise AssertionError("expected LLMPermanentError")


# ---- C4: classify by typed SDK exception, not name substring -------------

def test_rate_limit_error_is_treated_as_transient():
    fake_mod = _fake_anthropic_module(raise_error_name="RateLimitError")
    with _installed_fake_anthropic(fake_mod):
        client = AnthropicLLMClient()
        try:
            client.complete("sys", "user")
        except LLMTimeout:
            pass
        else:
            raise AssertionError("expected LLMTimeout for a rate limit error")


def test_authentication_error_is_treated_as_permanent():
    fake_mod = _fake_anthropic_module(raise_error_name="AuthenticationError")
    with _installed_fake_anthropic(fake_mod):
        client = AnthropicLLMClient()
        try:
            client.complete("sys", "user")
        except LLMPermanentError:
            pass
        else:
            raise AssertionError("expected LLMPermanentError for an authentication error")


def test_bad_request_error_is_treated_as_permanent():
    fake_mod = _fake_anthropic_module(raise_error_name="BadRequestError")
    with _installed_fake_anthropic(fake_mod):
        client = AnthropicLLMClient()
        try:
            client.complete("sys", "user")
        except LLMPermanentError:
            pass
        else:
            raise AssertionError("expected LLMPermanentError for a bad request error")


def test_server_5xx_status_error_is_treated_as_transient():
    fake_mod = _fake_anthropic_module(raise_error_name="APIStatusError", status_code=503)
    with _installed_fake_anthropic(fake_mod):
        client = AnthropicLLMClient()
        try:
            client.complete("sys", "user")
        except LLMTimeout:
            pass
        else:
            raise AssertionError("expected LLMTimeout for a 5xx status error")


def test_unlisted_4xx_status_error_is_treated_as_permanent():
    fake_mod = _fake_anthropic_module(raise_error_name="APIStatusError", status_code=418)
    with _installed_fake_anthropic(fake_mod):
        client = AnthropicLLMClient()
        try:
            client.complete("sys", "user")
        except LLMPermanentError:
            pass
        else:
            raise AssertionError("expected LLMPermanentError for a non-5xx status error")


# ---- Retry: transient/unparseable retries, permanent never retries -------

class _FlakyThenOK:
    """Raises LLMTimeout on the first call, then returns valid JSON."""

    def __init__(self, findings=None):
        self.call_count = 0
        self._findings = findings if findings is not None else []

    def complete(self, system, user):
        self.call_count += 1
        if self.call_count == 1:
            raise LLMTimeout("first attempt: simulated transient failure")
        return json.dumps({"findings": self._findings})


class _AlwaysPermanent:
    def __init__(self):
        self.call_count = 0

    def complete(self, system, user):
        self.call_count += 1
        raise LLMPermanentError("simulated bad API key")


class _FlakyJSON:
    """Returns unparseable text on the first call, then valid JSON."""

    def __init__(self):
        self.call_count = 0

    def complete(self, system, user):
        self.call_count += 1
        if self.call_count == 1:
            return "not json at all"
        return json.dumps({"findings": []})


def _empty_review_file():
    return ReviewFile(file_path="a.py", language="python", hunks=[], static_findings=[])


def test_run_llm_retries_transient_failure_then_succeeds():
    client = _FlakyThenOK()
    findings, status = _run_llm(_empty_review_file(), client)
    assert status == LLMStatus.SUCCESS
    assert client.call_count == 2


def test_run_llm_does_not_retry_permanent_failure():
    client = _AlwaysPermanent()
    findings, status = _run_llm(_empty_review_file(), client)
    assert status == LLMStatus.INVALID_RESPONSE
    assert client.call_count == 1  # never retried


def test_run_llm_exhausts_retries_on_persistent_timeout():
    client = MockLLMClient(fail=True)
    findings, status = _run_llm(_empty_review_file(), client)
    assert status == LLMStatus.TIMEOUT
    assert client.call_count == 2  # both attempts used


def test_run_llm_retries_on_unparseable_json_then_succeeds():
    client = _FlakyJSON()
    findings, status = _run_llm(_empty_review_file(), client)
    assert status == LLMStatus.SUCCESS
    assert client.call_count == 2


# ---- J1: malformed-but-JSON-valid findings no longer crash ---------------

def test_parse_json_rejects_non_list_findings():
    assert _parse_json(json.dumps({"findings": None})) is None
    assert _parse_json(json.dumps({"findings": {"a": 1}})) is None
    assert _parse_json(json.dumps({"findings": ["not a dict"]})) is None
    assert _parse_json(json.dumps({"findings": [{"category": "bug"}]})) is not None


def test_fallback_on_bad_shape_response():
    out = review(_load_input(), client=MockLLMClient(bad_shape=True))
    assert out["llm_status"] == "invalid_response"
    assert out["review_status"] == "completed"
    assert len(out["findings"]) >= 1  # static findings survive, no crash


# ---- Status mapping: permanent errors -> INVALID_RESPONSE ----------------

def test_review_reports_invalid_response_on_permanent_llm_failure():
    out = review(_load_input(), client=MockLLMClient(permanent_fail=True))
    assert out["llm_status"] == "invalid_response"
    assert out["review_status"] == "completed"
    assert all(f["source"] == "static" for f in out["findings"])


# ---- C2: review() reports the FIRST failing file's status ----------------

class _SequencedClient:
    """Test double: each call pulls the next canned response/exception from
    a fixed sequence, regardless of which file triggered it."""

    def __init__(self, sequence):
        self._sequence = list(sequence)
        self.call_count = 0

    def complete(self, system, user):
        self.call_count += 1
        item = self._sequence.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def test_review_reports_first_failing_file_status_not_last():
    data = {
        "submission_id": "sub_multi",
        "source_type": "pull_request",
        "repository": "org/repo",
        "redaction_applied": True,
        "files": [
            {"file_path": "a.py", "language": "python", "hunks": [], "static_findings": []},
            {"file_path": "b.py", "language": "python", "hunks": [], "static_findings": []},
        ],
    }
    client = _SequencedClient([
        LLMTimeout("boom"), LLMTimeout("boom"),       # a.py: exhausts retries -> TIMEOUT
        "not json at all", "still not json either",   # b.py: exhausts retries -> INVALID_RESPONSE
    ])
    out = review(data, client=client)
    assert out["llm_status"] == "timeout"  # first failure (a.py), not last (b.py)
    assert client.call_count == 4


# ---- _static_baseline: repo_diff_pipeline severity scheme + warning ------

def test_static_baseline_accepts_repo_diff_pipeline_severity_scheme_case_insensitive():
    cases = [
        ("info", Severity.LOW),
        ("Warning", Severity.MEDIUM),
        ("ERROR", Severity.HIGH),
        ("Critical", Severity.CRITICAL),
    ]
    for raw, expected in cases:
        sf = StaticFinding(
            finding_id="sf_x", tool="t", rule_id="r", category="bug",
            message="m", file_path="f.py", line=1, severity=raw,
        )
        assert _static_baseline(sf) == expected, f"{raw!r} -> expected {expected}"


def test_unparseable_static_severity_logs_warning_and_falls_back():
    sf = StaticFinding(
        finding_id="sf_y", tool="t", rule_id="r", category="bug",
        message="m", file_path="f.py", line=1, severity="not-a-real-severity",
    )
    with _capture_logs() as records:
        result = _static_baseline(sf)
    assert result is None
    assert any("not-a-real-severity" in r.getMessage() for r in records), (
        "expected a warning naming the unparseable severity value"
    )


# ---- Redaction gate: static finding messages + diff text before the LLM --

class _PromptCapturingClient:
    """Records every prompt it's asked to complete, and answers with an
    empty findings list so the pipeline treats the call as a success."""

    def __init__(self):
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        return json.dumps({"findings": []})


def test_bandit_style_static_finding_message_is_redacted_in_llm_prompt():
    data = {
        "submission_id": "sub_redact_message",
        "source_type": "pull_request",
        "repository": "org/repo",
        "redaction_applied": True,
        "files": [{
            "file_path": "app.py",
            "language": "python",
            "hunks": [],
            "static_findings": [{
                "finding_id": "sf_secret",
                "tool": "bandit",
                "rule_id": "B105",
                "category": "security",
                "message": "Possible hardcoded password: 'hunter2_fake_password'",
                "file_path": "app.py",
                "line": 3,
            }],
        }],
    }
    client = _PromptCapturingClient()
    out = review(data, client=client)

    assert out["llm_status"] == "success"
    assert len(client.prompts) == 1
    assert "hunter2_fake_password" not in client.prompts[0]
    assert "<REDACTED_SECRET>" in client.prompts[0]


def test_assignment_style_secret_in_diff_is_redacted_in_llm_prompt():
    data = {
        "submission_id": "sub_redact_diff",
        "source_type": "pull_request",
        "repository": "org/repo",
        "redaction_applied": True,
        "files": [{
            "file_path": "app.py",
            "language": "python",
            "hunks": [{
                "hunk_id": "h1",
                "start_line": 1,
                "end_line": 1,
                "diff": '@@ -1,0 +1,1 @@\n+api_key = "sk-fake-1234567890abcdef"\n',
                "added_lines": [1],
                "removed_lines": [],
                "context_lines": [],
            }],
            "static_findings": [],
        }],
    }
    client = _PromptCapturingClient()
    out = review(data, client=client)

    assert out["llm_status"] == "success"
    assert len(client.prompts) == 1
    assert "sk-fake-1234567890abcdef" not in client.prompts[0]
    assert "<REDACTED_SECRET>" in client.prompts[0]


def test_review_falls_back_to_skipped_when_redactor_import_fails():
    with _blocked_import("Static_checks_and_sec_rules.services.redactor"):
        out = review(_load_input(), client=MockLLMClient())

    assert out["llm_status"] == "skipped"
    assert out["review_status"] == "completed"
    assert all(f["source"] == "static" for f in out["findings"])
    assert any("sf_002" in f["static_finding_ids"] for f in out["findings"])


if __name__ == "__main__":
    # Minimal stdlib runner so it works without pytest installed.
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
