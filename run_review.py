#!/usr/bin/env python3
"""CLI runner for the LLM review section.

Usage:
    python run_review.py tests/fixtures/mock_analysis_input.json
    python run_review.py <input.json> --real       # use Anthropic (needs key)
    python run_review.py <input.json> --fail        # simulate LLM failure
    python run_review.py <input.json> --garbage     # simulate bad LLM output

Reads an analysis-input JSON (the contract from #2), runs the review, and
prints the review-output JSON (the contract for #4).
"""
import argparse
import json
import sys

from llm_review import review
from llm_review.client import AnthropicLLMClient, MockLLMClient


def main() -> int:
    p = argparse.ArgumentParser(description="Run LLM code review on a diff+static input.")
    p.add_argument("input", help="Path to analysis-input JSON")
    p.add_argument("--real", action="store_true", help="Use real Anthropic client")
    p.add_argument("--fail", action="store_true", help="Simulate LLM timeout")
    p.add_argument("--garbage", action="store_true", help="Simulate invalid LLM output")
    p.add_argument("-o", "--output", help="Write output JSON to this path")
    args = p.parse_args()

    with open(args.input) as f:
        data = json.load(f)

    if args.real:
        client = AnthropicLLMClient()
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
