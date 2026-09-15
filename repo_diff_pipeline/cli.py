"""Command-line entry point: run the full workflow and print a JSON report."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from enum import Enum

from . import pipeline, storage


def _json_default(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__fspath__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI code review pipeline")
    source = parser.add_argument_group("submission source (choose one)")
    source.add_argument("--pr-url", help="GitHub PR URL, e.g. https://github.com/org/repo/pull/123")
    source.add_argument("--patch-file", help="Path to a text-based patch/diff file")
    source.add_argument("--repo-url", help="Remote git repo URL (used with --base/--head)")
    source.add_argument("--repo-path", help="Local repo path already on disk (used with --base/--head)")

    parser.add_argument("--base", help="Base ref (required for --repo-url/--repo-path)")
    parser.add_argument("--head", help="Head ref (required for --repo-url/--repo-path)")
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN"), help="Defaults to $GITHUB_TOKEN")
    parser.add_argument("--no-ai-review", action="store_true", help="Skip the LLM review step")
    parser.add_argument("--no-persist", action="store_true", help="Skip database persistence")
    parser.add_argument("--db-path", default=storage.DEFAULT_DB_PATH, help="SQLite DB path for results")
    parser.add_argument("-o", "--output", help="Write JSON report to this path instead of stdout")
    args = parser.parse_args()

    patch_text = None
    if args.patch_file:
        with open(args.patch_file, "r") as f:
            patch_text = f.read()

    if not any([args.pr_url, patch_text is not None, args.repo_url, args.repo_path]):
        parser.error("one of --pr-url, --patch-file, --repo-url, or --repo-path is required")

    report = pipeline.run_review_pipeline(
        pr_url=args.pr_url,
        patch_text=patch_text,
        repo_url=args.repo_url,
        repo_path=args.repo_path,
        base_ref=args.base,
        head_ref=args.head,
        github_token=args.github_token,
        run_ai_review=not args.no_ai_review,
        persist=not args.no_persist,
        db_path=args.db_path,
    )

    output = json.dumps(report, default=_json_default, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
    else:
        print(output)

    return 1 if report.blocked else 0


if __name__ == "__main__":
    sys.exit(main())
