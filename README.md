# LLM Review Logic (Issue #3)

Owns the middle stage of the AI Code Review Platform pipeline: it takes the
parsed diff + static-analysis findings from **Static Analysis (#2)**,
enriches them with an LLM review of the changed **Python** code, assigns
severity, and emits combined findings for **UI / Reporting (#4)**.

```
#1 Repo/Diff  →  #2 Static Analysis  →  [ #3 LLM Review (this) ]  →  #4 UI/Reporting
```

## What it does

1. **Run LLM Review** — sends the diff + static findings to the model and
   asks for explanations, additional issues, and suggested fixes as JSON.
2. **Combine Findings** — merges LLM output with static findings. Same
   line / referenced id ⇒ `combined`; LLM-only ⇒ `llm`; untouched static
   findings are still emitted as `static` (nothing gets dropped).
3. **Assign Severity** — rule-based Critical(4)/High(3)/Medium(2)/Low(1),
   escalated one level when both the LLM and static analysis agree.
4. **Summary + score** — 0–100 health score and a one-line summary.

## Fallback (the important part)

The pipeline **never dead-ends on an LLM failure**. `llm_status` reports
what happened, and findings still populate from static analysis alone:

| llm_status         | meaning                                   |
|--------------------|-------------------------------------------|
| `success`          | LLM ran, findings enriched                |
| `timeout`          | LLM didn't respond → static-only fallback |
| `invalid_response` | LLM returned unparseable output → static-only |
| `skipped`          | redaction not confirmed → LLM not called  |

There's also a **redaction gate**: if `redaction_applied` isn't `true` in
the input, the LLM is skipped entirely, so unredacted code is never sent to
an external model.

## Run it

```bash
# Normal run (offline mock LLM — no API key needed)
python run_review.py tests/fixtures/mock_analysis_input.json

# Demo the fallback paths
python run_review.py tests/fixtures/mock_analysis_input.json --fail      # timeout
python run_review.py tests/fixtures/mock_analysis_input.json --garbage   # bad output

# Real LLM (needs: pip install anthropic, and ANTHROPIC_API_KEY set)
python run_review.py tests/fixtures/mock_analysis_input.json --real

# Tests
python tests/test_pipeline.py         # stdlib runner
python -m pytest tests/ -v            # if pytest installed
```

## Contracts (shared with the team)

- Input from #2: `schemas/analysis_input.example.json`
- Output to #4: `schemas/review_output.example.json`

Changing either shape needs a heads-up to #2 and #4. Code against the
fixture, not against teammates' code — swap in their real output at
integration.

## Swapping the LLM provider

Everything depends on the `LLMClient` protocol in `llm_review/client.py`.
`MockLLMClient` is offline/deterministic (used for the demo). To use a real
model, use `AnthropicLLMClient`, or add an OpenAI client with the same
`complete(system, user)` method — no other file changes.

## Layout

```
llm_review/
  types.py       # dataclasses mirroring the schemas
  prompt.py      # system + per-file prompt (asks for strict JSON)
  client.py      # LLMClient protocol, MockLLMClient, AnthropicLLMClient
  severity.py    # category→severity table + scoring
  pipeline.py    # run → parse → combine → severity → summary (+ fallback)
run_review.py    # CLI
tests/           # 9 tests incl. fallback + redaction gate
schemas/         # shared input/output contracts
```
