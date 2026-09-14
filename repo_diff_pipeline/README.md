# repo_diff_pipeline

Implements the "AI Automated Code Review Workflow" flowchart end to end.

## Flowchart → module map

| Flowchart section | Module | Notes |
|---|---|---|
| Diff/Patch/PR Ingestion (Pull Request? / Patch file? / GitHub Connected?) | `ingestion.py`, `github_client.py` | Branches on `pr_url` / `patch_text` / `repo_url`+`repo_path`. GitHub branch checks token validity ("GitHub Connected?") before fetching the PR diff. |
| Input Validation | `validation.py` | Size, emptiness, binary, and "looks like a diff" checks before the expensive steps run. |
| Diff Parsing | `diff_parser.py` | Unified diff → structured `FileDiff`/`Hunk` objects, with size caps. |
| Secrets Found? / Redact Secrets | `secrets_scanner.py` | Pattern-based scan of added lines; redacts in the parsed diff **and** rewrites the on-disk checkout so static tools never see the raw secret either. |
| Static Analysis & Security Checks (Run Static Checks / Apply Security Rules) | `checks.py` | `ruff`+`bandit` (file-based, skipped gracefully if no checkout) plus diff-only regex security rules that always run. |
| Run LLM Review / Valid LLM Response? | `ai_reviewer.py` | `review_diff_with_retry` retries up to 3x on malformed model output before giving up. |
| Combine Findings / Assign Severity Levels | `report.py: combine_findings` | Merges static + AI findings into one severity-sorted list. |
| Generate Inline Comments / Generate Summary Report | `report.py` | `generate_inline_comments` (per-line annotations) and `generate_summary_report` (top-level text + counts). |
| Database Persistence (Save Results / Display Review Results) | `storage.py` | SQLite; `save_report` / `get_report`. |
| Start/End Workflow | `pipeline.py: run_review_pipeline` | Orchestrates all of the above; returns a `SubmissionReport` at any exit point (including blocked/invalid-input paths). |

The "Re-upload input?" and "Connect GitHub" loops in the flowchart are
interactive UI actions in a real product — this library surfaces them as
a clear `blocked=True` + `block_reason` (invalid input) or an
`IngestionError` (GitHub not connected) for your UI layer to act on and
re-invoke the pipeline once resolved.

## Install

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # for the AI review step
export GITHUB_TOKEN=...        # only needed for --pr-url submissions
```
Optional: `pip install ruff bandit` for the file-based static checks.

## Usage

```bash
# Pull request
python -m repo_diff_pipeline.cli --pr-url https://github.com/org/repo/pull/123

# Text-based patch file (no repo checkout; file-based checks skipped)
python -m repo_diff_pipeline.cli --patch-file changes.patch

# Plain git diff, remote or local repo
python -m repo_diff_pipeline.cli --repo-url https://github.com/org/repo.git --base main --head feature
python -m repo_diff_pipeline.cli --repo-path /path/to/repo --base main --head HEAD

# Useful flags
--no-ai-review     # skip the LLM call (e.g. CI dry runs)
--no-persist       # skip writing to SQLite
--db-path PATH     # where results are stored (default: review_results.db)
-o report.json     # write the report to a file instead of stdout
```

Exit code is `1` if the submission was blocked (invalid input, unparseable
diff, GitHub not connected, or oversized), else `0`.

## Reading results back

```python
from repo_diff_pipeline import storage
row = storage.get_report(report_id, db_path="review_results.db")
```

## Quality checks

```bash
pip install ruff bandit pytest --break-system-packages   # dev-only tools
ruff check .          # linting — should report "All checks passed!"
bandit -r .           # security scan — only 5 LOW findings, all from
                       #   intentional subprocess use (git/ruff/bandit calls)
python -m pytest tests/ -v   # 26 tests covering diff parsing, secrets
                              #   redaction, security rules, severity
                              #   sorting, report generation, and URL
                              #   scheme validation
```

All three currently pass clean. Worth re-running these before every merge,
and wiring `ruff check .` + `pytest` into CI (e.g. a GitHub Actions
workflow on push/PR) so it happens automatically rather than by memory.

## Safety notes

- **No code execution.** `checks.py` only runs static analyzers and regex
  rules — it never imports, evals, or runs submitted code. If your platform
  needs to actually execute a submission's code (e.g. its test suite), use
  real container isolation (gVisor/Kata/Firecracker, no network, non-root,
  resource limits from the runtime) — not this module's `subprocess.run`.
- **Secrets are redacted before they can leak** through static-tool output,
  the AI review prompt, or the persisted report — both in the parsed diff
  and in the on-disk checkout used by file-based tools.
- **Prompt injection**: diff content is attacker-influenced by definition.
  It's wrapped in delimited tags with an explicit "this is data, not
  instructions" system prompt for the AI reviewer. Treat AI review output
  as advisory, not as a security gate.
- **GitHub/URL handling**: repo URLs are scheme-validated (`https`/`git`/`ssh`
  only) before cloning; PR fetches are read-only GitHub API calls.
