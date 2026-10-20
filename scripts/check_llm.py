#!/usr/bin/env python3
"""Verify the local LLM setup for llm_review (Ollama + Qwen2.5-Coder).

Four checks, run in order (each later check is skipped, not failed, if an
earlier one didn't pass -- there's nothing meaningful to check without it):
  1. Ollama reachable at the configured host.
  2. The configured model is pulled.
  3. Env vars from .env.example are set (scoped to the ones that matter for
     --local usage; ANTHROPIC_API_KEY is reported but never required here --
     it's for the separate --real/Anthropic path).
  4. One tiny request round-trips to schema-valid JSON, using the exact
     SYSTEM_PROMPT/build_user_prompt/_parse_json the real pipeline uses --
     this is not a reimplemented check, it's the real one.

Usage:
    python scripts/check_llm.py
    python scripts/check_llm.py --model qwen2.5-coder:7b --host http://localhost:11434
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Running this as `python scripts/check_llm.py` puts scripts/ itself on
# sys.path, not its parent -- same reasoning as llm_review/run_review.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from llm_review.client import LLMError, LLMPermanentError, LLMTimeout, OllamaLLMClient
from llm_review.pipeline import _parse_json
from llm_review.prompt import SYSTEM_PROMPT, build_user_prompt
from llm_review.run_review import _default_ollama_timeout
from llm_review.types import Hunk, ReviewFile

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def _print_result(status: str, label: str, detail: str = "") -> None:
    line = f"[{status}] {label}"
    if detail:
        line += f" -- {detail}"
    print(line)


def check_env_vars(effective_timeout: float) -> bool:
    """Check 3: env vars from .env.example that matter for --local usage.

    ANTHROPIC_API_KEY is reported for visibility but never makes this check
    fail -- it belongs to the separate --real/Anthropic path, not local
    Ollama usage, and plenty of local-only setups will never set it.

    effective_timeout is passed in (computed once in main() via
    _default_ollama_timeout(), which prints its own stderr warning on an
    invalid value) rather than recomputed here, so that warning isn't
    printed twice for the same value.
    """
    all_ok = True

    raw_timeout = os.environ.get("OLLAMA_TIMEOUT_SECONDS", "")
    if not raw_timeout.strip():
        _print_result(PASS, "OLLAMA_TIMEOUT_SECONDS", f"not set, using default {effective_timeout}")
    else:
        _print_result(PASS, "OLLAMA_TIMEOUT_SECONDS", f"set to {raw_timeout!r} -> effective {effective_timeout}")

    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        _print_result(PASS, "ANTHROPIC_API_KEY", "set (not required for --local, informational only)")
    else:
        _print_result(PASS, "ANTHROPIC_API_KEY", "not set (fine -- only needed for --real, not --local)")

    return all_ok


def check_ollama_reachable(host: str) -> tuple[bool, list[dict] | None]:
    """Check 1: can we reach Ollama's API at all."""
    req = urllib.request.Request(f"{host.rstrip('/')}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            body = resp.read()
    except urllib.error.URLError as e:
        _print_result(FAIL, f"Ollama reachable at {host}", f"{e.reason} -- is `ollama serve` running?")
        return False, None
    except TimeoutError:
        _print_result(FAIL, f"Ollama reachable at {host}", "timed out after 5s")
        return False, None

    try:
        models = json.loads(body).get("models", [])
    except ValueError as e:
        _print_result(FAIL, f"Ollama reachable at {host}", f"reachable but returned non-JSON: {e}")
        return False, None

    _print_result(PASS, f"Ollama reachable at {host}")
    return True, models


def check_model_pulled(model: str, models: list[dict]) -> bool:
    """Check 2: the configured model tag shows up in `ollama list`."""
    tags = {m.get("name") for m in models} | {m.get("model") for m in models}
    if model in tags:
        _print_result(PASS, f"Model '{model}' is pulled")
        return True
    _print_result(FAIL, f"Model '{model}' is pulled", f"not found. Try: ollama pull {model}")
    return False


def check_tiny_request(model: str, host: str, timeout: float) -> bool:
    """Check 4: one real, minimal request through OllamaLLMClient, graded by
    the pipeline's own JSON validation (_parse_json) -- not a separate,
    reimplemented schema check."""
    tiny_file = ReviewFile(
        file_path="scripts/check_llm.py",
        language="python",
        hunks=[
            Hunk(
                hunk_id="h1",
                start_line=1,
                end_line=1,
                diff="@@ -0,0 +1,1 @@\n+x = 1",
                added_lines=[1],
                removed_lines=[],
                context_lines=[],
            )
        ],
        static_findings=[],
    )
    user = build_user_prompt(tiny_file)
    client = OllamaLLMClient(model=model, host=host, timeout=timeout)

    try:
        raw = client.complete(SYSTEM_PROMPT, user)
    except LLMPermanentError as e:
        _print_result(FAIL, "Tiny request returns schema-valid JSON", f"permanent error: {e}")
        return False
    except LLMTimeout as e:
        _print_result(FAIL, "Tiny request returns schema-valid JSON", f"timed out: {e}")
        return False
    except LLMError as e:
        _print_result(FAIL, "Tiny request returns schema-valid JSON", f"error: {e}")
        return False

    parsed = _parse_json(raw)
    if parsed is None:
        _print_result(FAIL, "Tiny request returns schema-valid JSON", f"response failed validation: {raw[:200]!r}")
        return False

    _print_result(PASS, "Tiny request returns schema-valid JSON", f"{len(parsed['findings'])} finding(s)")
    return True


def main() -> int:
    load_dotenv()

    # Computed once, here, independent of any --timeout override below, so
    # check_env_vars reports what $OLLAMA_TIMEOUT_SECONDS alone resolves to
    # (and its invalid-value warning, if any, prints exactly once) rather
    # than whatever the user passed on the command line.
    env_derived_timeout = _default_ollama_timeout()

    p = argparse.ArgumentParser(description="Verify the local LLM setup (Ollama + Qwen2.5-Coder).")
    p.add_argument("--model", default="qwen2.5-coder:7b", help="Ollama model tag to check")
    p.add_argument("--host", default="http://localhost:11434", help="Ollama server URL to check")
    p.add_argument("--timeout", type=float, default=env_derived_timeout, help="Request timeout in seconds")
    args = p.parse_args()

    print(f"Checking local LLM setup (model={args.model!r}, host={args.host!r})\n")

    env_ok = check_env_vars(env_derived_timeout)
    print()

    reachable, models = check_ollama_reachable(args.host)
    if not reachable:
        _print_result(SKIP, f"Model '{args.model}' is pulled", "Ollama not reachable")
        _print_result(SKIP, "Tiny request returns schema-valid JSON", "Ollama not reachable")
        return 1

    model_ok = check_model_pulled(args.model, models)
    if not model_ok:
        _print_result(SKIP, "Tiny request returns schema-valid JSON", "model not pulled")
        return 1

    request_ok = check_tiny_request(args.model, args.host, args.timeout)

    print()
    all_ok = env_ok and reachable and model_ok and request_ok
    print("All checks passed." if all_ok else "One or more checks failed.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
