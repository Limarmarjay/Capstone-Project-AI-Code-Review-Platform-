#!/usr/bin/env python3
"""Eval runner for the fixtures under eval/fixtures/.

Runs the real llm_review.review() pipeline (redaction -> prompt -> combine)
against an OllamaLLMClient for every fixture, scores the result against
eval/README.md's documented Scoring algorithm exactly, and writes a report
to eval/results/<timestamp>.json (gitignored) and, optionally, also to
eval/baselines/<name>.json (committed).

Hold-out policy (see eval/README.md): expected_findings.json is read ONLY
after the model's response comes back, by the grading code in this file --
never by build_user_prompt/SYSTEM_PROMPT, which only ever sees
analysis_input.json. This script never writes fixture content anywhere a
training pipeline might later ingest it as training data.

Usage:
    python eval/run_eval.py
    python eval/run_eval.py --fixture sql-injection-basic --fixture java-sql-injection
    python eval/run_eval.py --save-baseline qwen2.5-coder-7b
"""
import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

# Running this as `python eval/run_eval.py` puts eval/ itself on sys.path,
# not its parent -- same reasoning as llm_review/run_review.py.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from llm_review import review
from llm_review.client import LLMTimeout, OllamaLLMClient
from llm_review.prompt import SYSTEM_PROMPT
from llm_review.run_review import _default_ollama_timeout

FIXTURES_DIR = os.path.join(REPO_ROOT, "eval", "fixtures")
RESULTS_DIR = os.path.join(REPO_ROOT, "eval", "results")
BASELINES_DIR = os.path.join(REPO_ROOT, "eval", "baselines")
CHECK_LLM_SCRIPT = os.path.join(REPO_ROOT, "scripts", "check_llm.py")
CLEAN_CASE_IDS = {"python-clean-add-logging", "python-clean-rename-variable", "java-clean-add-getter"}


class _ConnectionAwareOllamaClient:
    """Wraps OllamaLLMClient to record whether a raised LLMTimeout was
    actually a connection failure (Ollama unreachable) rather than a
    genuine request timeout -- review()'s own retry/status logic collapses
    both into LLMStatus.TIMEOUT with no way to tell them apart from the
    outside (OllamaLLMClient.complete() raises the same LLMTimeout class
    for both urllib.error.URLError and TimeoutError; see client.py). The
    only distinguishing signal left by the time it reaches here is the
    exception's own message text, which this class inspects before
    re-raising unchanged.
    """

    def __init__(self, inner: OllamaLLMClient) -> None:
        self._inner = inner
        self.connection_failure_seen = False
        self.connection_failure_detail = ""

    def complete(self, system: str, user: str) -> str:
        try:
            return self._inner.complete(system, user)
        except LLMTimeout as e:
            if "Could not reach Ollama" in str(e):
                self.connection_failure_seen = True
                self.connection_failure_detail = str(e)
            raise


def run_setup_check(model: str, host: str, timeout: float) -> bool:
    """Item 2: run scripts/check_llm.py's real checks before starting, not
    a reimplementation of them. Returns True iff it exited 0."""
    print("=== Running scripts/check_llm.py before starting ===", flush=True)
    result = subprocess.run(
        [sys.executable, CHECK_LLM_SCRIPT, "--model", model, "--host", host, "--timeout", str(timeout)],
        cwd=REPO_ROOT,
    )
    print(flush=True)
    return result.returncode == 0


def load_fixtures(only: list[str] | None) -> list[tuple[str, dict, dict]]:
    case_ids = sorted(
        d for d in os.listdir(FIXTURES_DIR)
        if os.path.isdir(os.path.join(FIXTURES_DIR, d))
    )
    if only:
        missing = set(only) - set(case_ids)
        if missing:
            raise SystemExit(f"Unknown fixture(s): {sorted(missing)}")
        case_ids = [c for c in case_ids if c in only]

    fixtures = []
    for case_id in case_ids:
        case_dir = os.path.join(FIXTURES_DIR, case_id)
        with open(os.path.join(case_dir, "analysis_input.json")) as f:
            analysis_input = json.load(f)
        with open(os.path.join(case_dir, "expected_findings.json")) as f:
            expected = json.load(f)
        fixtures.append((case_id, analysis_input, expected))
    return fixtures


def extract_model_findings(review_output: dict) -> list[dict]:
    """Only findings attributable to the model's own output (source "llm"
    or "combined") -- pure Source.STATIC findings are static-tool-only
    entries the LLM never touched, not something to grade the model on."""
    out = []
    for f in review_output["findings"]:
        if f["source"] not in ("llm", "combined"):
            continue
        static_ids = f.get("static_finding_ids") or []
        out.append({
            **f,
            "relates_to_static_finding_id": static_ids[0] if static_ids else None,
        })
    return out


def match_fixture(model_findings: list[dict], expected: dict) -> dict:
    """Mirrors eval/README.md's Scoring section exactly: for each
    model-reported finding (in order), attempt must_catch, then may_catch,
    then must_not_flag, consuming both the finding and the expectation on
    the first match. Whatever's left over is unexpected."""
    must_catch = list(expected.get("must_catch", []))
    may_catch = list(expected.get("may_catch", []))
    must_not_flag = list(expected.get("must_not_flag", []))

    matched_must_catch, matched_may_catch, must_not_flag_hits, unexpected = [], [], [], []

    for mf in model_findings:
        hit = next(
            (e for e in must_catch if e["category"] == mf["category"] and abs(mf["line"] - e["line"]) <= 1),
            None,
        )
        if hit is not None:
            must_catch.remove(hit)
            link_correct = mf["relates_to_static_finding_id"] == hit["relates_to_static_finding_id"]
            matched_must_catch.append({"id": hit["id"], "line": mf["line"], "category": mf["category"], "link_correct": link_correct})
            continue

        hit = next(
            (e for e in may_catch if e["category"] == mf["category"] and abs(mf["line"] - e["line"]) <= 1),
            None,
        )
        if hit is not None:
            may_catch.remove(hit)
            link_correct = mf["relates_to_static_finding_id"] == hit["relates_to_static_finding_id"]
            matched_may_catch.append({"id": hit["id"], "line": mf["line"], "category": mf["category"], "link_correct": link_correct})
            continue

        hit = next((e for e in must_not_flag if abs(mf["line"] - e["line"]) <= 1), None)
        if hit is not None:
            must_not_flag.remove(hit)
            must_not_flag_hits.append({"line": mf["line"], "category": mf["category"], "title": mf.get("title", "")})
            continue

        unexpected.append({"line": mf["line"], "category": mf["category"], "title": mf.get("title", "")})

    return {
        "must_catch_total": len(expected.get("must_catch", [])),
        "may_catch_total": len(expected.get("may_catch", [])),
        "matched_must_catch": matched_must_catch,
        "missed_must_catch": [e["id"] for e in must_catch],
        "matched_may_catch": matched_may_catch,
        "missed_may_catch": [e["id"] for e in may_catch],
        "must_not_flag_hits": must_not_flag_hits,
        "unexpected": unexpected,
    }


def run_one_fixture(case_id: str, analysis_input: dict, expected: dict, client_factory) -> tuple[dict, bool]:
    """Returns (result_dict, connection_failure_seen)."""
    client = client_factory()
    start = time.monotonic()
    try:
        review_output = review(analysis_input, client=client)
    finally:
        elapsed = time.monotonic() - start

    if client.connection_failure_seen:
        return {"case_id": case_id, "connection_failure": client.connection_failure_detail}, True

    model_findings = extract_model_findings(review_output)
    match = match_fixture(model_findings, expected)

    result = {
        "case_id": case_id,
        "llm_status": review_output["llm_status"],
        "latency_seconds": round(elapsed, 3),
        "overall_score": review_output["overall_score"],
        **match,
    }
    return result, False


def aggregate(results: list[dict]) -> dict:
    total_matched_must_catch = sum(len(r["matched_must_catch"]) for r in results)
    total_must_catch = sum(r["must_catch_total"] for r in results)
    total_matched_may_catch = sum(len(r["matched_may_catch"]) for r in results)
    total_may_catch = sum(r["may_catch_total"] for r in results)
    total_must_not_flag_hits = sum(len(r["must_not_flag_hits"]) for r in results)
    total_unexpected = sum(len(r["unexpected"]) for r in results)

    link_pairs = [
        e["link_correct"]
        for r in results
        for e in (r["matched_must_catch"] + r["matched_may_catch"])
    ]
    static_link_correct = sum(1 for ok in link_pairs if ok)
    static_link_incorrect = sum(1 for ok in link_pairs if not ok)

    precision_num = total_matched_must_catch + total_matched_may_catch
    precision_den = precision_num + total_must_not_flag_hits + total_unexpected
    precision = (precision_num / precision_den) if precision_den > 0 else None  # item 7

    recall = (total_matched_must_catch / total_must_catch) if total_must_catch > 0 else None

    clean_false_positives = sum(
        len(r["must_not_flag_hits"]) + len(r["unexpected"])
        for r in results
        if r["case_id"] in CLEAN_CASE_IDS
    )

    all_latencies = [r["latency_seconds"] for r in results]
    success_latencies = [r["latency_seconds"] for r in results if r["llm_status"] == "success"]

    status_counts: dict[str, int] = {}
    for r in results:
        status_counts[r["llm_status"]] = status_counts.get(r["llm_status"], 0) + 1

    return {
        "precision": precision,
        "recall": recall,
        "false_positives_on_clean_cases": clean_false_positives,
        "latency_seconds": {
            "mean": statistics.mean(all_latencies) if all_latencies else None,
            "median": statistics.median(all_latencies) if all_latencies else None,
            "mean_excluding_failed": statistics.mean(success_latencies) if success_latencies else None,
        },
        "llm_status_counts": status_counts,
        "invalid_response_count": status_counts.get("invalid_response", 0),
        "timeout_count": status_counts.get("timeout", 0),
        "totals": {
            "must_catch": total_must_catch,
            "matched_must_catch": total_matched_must_catch,
            "may_catch": total_may_catch,
            "matched_may_catch": total_matched_may_catch,
            "must_not_flag_hits": total_must_not_flag_hits,
            "unexpected": total_unexpected,
            "static_link_correct": static_link_correct,
            "static_link_incorrect": static_link_incorrect,
        },
    }


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main() -> int:
    p = argparse.ArgumentParser(description="Run the eval fixtures against a local Ollama model.")
    p.add_argument("--model", default="qwen2.5-coder:7b")
    p.add_argument("--host", default="http://localhost:11434")
    p.add_argument("--timeout", type=float, default=_default_ollama_timeout())
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--fixture", action="append", default=None, help="Run only this fixture (repeatable). Default: all.")
    p.add_argument("--save-baseline", default=None, help="Also write eval/baselines/<name>.json")
    p.add_argument("--skip-setup-check", action="store_true", help="Debugging escape hatch; not for normal use")
    args = p.parse_args()

    if not args.skip_setup_check:
        if not run_setup_check(args.model, args.host, args.timeout):
            print("Setup check failed -- aborting eval run. See output above.")
            return 1

    fixtures = load_fixtures(args.fixture)
    print(f"Running {len(fixtures)} fixture(s) against model={args.model!r} host={args.host!r}")
    print(f"temperature={args.temperature} seed={args.seed}\n")

    def client_factory():
        return _ConnectionAwareOllamaClient(
            OllamaLLMClient(
                model=args.model, host=args.host, timeout=args.timeout,
                temperature=args.temperature, seed=args.seed,
            )
        )

    results = []
    for case_id, analysis_input, expected in fixtures:
        result, connection_failed = run_one_fixture(case_id, analysis_input, expected, client_factory)
        if connection_failed:
            print(f"[ABORT] {case_id}: Ollama became unreachable mid-run -- {result['connection_failure']}")
            print("Aborting without saving any results (connection failure, not a model timeout).")
            return 1
        status_bits = (
            f"must_catch {len(result['matched_must_catch'])}/{result['must_catch_total']}, "
            f"may_catch {len(result['matched_may_catch'])}/{result['may_catch_total']}, "
            f"must_not_flag_hits {len(result['must_not_flag_hits'])}, "
            f"unexpected {len(result['unexpected'])}"
        )
        print(f"[{result['llm_status']:>16}] {case_id} ({result['latency_seconds']}s) -- {status_bits}")
        results.append(result)

    summary = aggregate(results)

    report = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "host": args.host,
            "timeout_seconds": args.timeout,
            "temperature": args.temperature,
            "seed": args.seed,
            "git_commit": git_commit(),
            "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
            "fixture_count": len(results),
        },
        "fixtures": results,
        "summary": summary,
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_name = report["metadata"]["timestamp"].replace(":", "").replace("+00:00", "Z") + ".json"
    out_path = os.path.join(RESULTS_DIR, out_name)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nWrote {out_path}")

    if args.save_baseline:
        os.makedirs(BASELINES_DIR, exist_ok=True)
        baseline_path = os.path.join(BASELINES_DIR, f"{args.save_baseline}.json")
        with open(baseline_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Wrote {baseline_path}")

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
