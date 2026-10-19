#!/usr/bin/env python3
"""CLI runner for the LLM review section.

Usage:
    python run_review.py tests/fixtures/mock_analysis_input.json
    python run_review.py <input.json> --real        # use Anthropic (needs key)
    python run_review.py <input.json> --local        # use a local Ollama model
    python run_review.py <input.json> --local --model qwen2.5-coder:7b
    python run_review.py <input.json> --fail         # simulate LLM failure
    python run_review.py <input.json> --garbage      # simulate bad LLM output

Reads an analysis-input JSON (the contract from #2), runs the review, and
prints the review-output JSON (the contract for #4).
"""
import argparse
import json
import os
import sys

# Running this as `python llm_review/run_review.py` (as documented above
# and in the README) puts llm_review/ itself on sys.path, not its parent --
# so `import llm_review` can't find the package without this. Only needed
# when run directly; `python -m llm_review.run_review` wouldn't need it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from llm_review import review
from llm_review.client import AnthropicLLMClient, MockLLMClient, OllamaLLMClient

DEFAULT_OLLAMA_TIMEOUT = 300.0


def _default_ollama_timeout() -> float:
    """Read $OLLAMA_TIMEOUT_SECONDS, falling back to DEFAULT_OLLAMA_TIMEOUT
    if it's unset, blank, or not a valid number -- a typo in .env should
    never crash the CLI, just fall back with a warning."""
    raw = os.environ.get("OLLAMA_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_OLLAMA_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        print(
            f"Warning: OLLAMA_TIMEOUT_SECONDS={raw!r} is not a valid number; "
            f"using {DEFAULT_OLLAMA_TIMEOUT}",
            file=sys.stderr,
        )
        return DEFAULT_OLLAMA_TIMEOUT


def main() -> int:
    # Loads ANTHROPIC_API_KEY (and anything else in .env) from a .env file
    # in the current or a parent directory, if one exists. No-op if there
    # isn't one -- real deployments can still set the env var directly.
    load_dotenv()

    p = argparse.ArgumentParser(description="Run LLM code review on a diff+static input.")
    p.add_argument("input", help="Path to analysis-input JSON")
    p.add_argument("--real", action="store_true", help="Use real Anthropic client")
    p.add_argument("--local", action="store_true", help="Use a local Ollama model instead of Anthropic")
    p.add_argument("--model", default="qwen2.5-coder:7b", help="Ollama model tag (with --local)")
    p.add_argument(
        "--ollama-host", default="http://localhost:11434", help="Ollama server URL (with --local)"
    )
    p.add_argument(
        "--timeout", type=float, default=_default_ollama_timeout(),
        help=f"Ollama request timeout in seconds (with --local). Default "
             f"{DEFAULT_OLLAMA_TIMEOUT}, or $OLLAMA_TIMEOUT_SECONDS.",
    )
    p.add_argument("--fail", action="store_true", help="Simulate LLM timeout")
    p.add_argument("--garbage", action="store_true", help="Simulate invalid LLM output")
    p.add_argument("-o", "--output", help="Write output JSON to this path")
    args = p.parse_args()

    if args.real and args.local:
        p.error("--real and --local are mutually exclusive")

    with open(args.input) as f:
        data = json.load(f)

    if args.real:
        client = AnthropicLLMClient()
    elif args.local:
        client = OllamaLLMClient(model=args.model, host=args.ollama_host, timeout=args.timeout)
    else:
        client = MockLLMClient(fail=args.fail, garbage=args.garbage)

    result = review(data, client=client)
    text = json.dumps(result, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        print(f"Wrote {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
