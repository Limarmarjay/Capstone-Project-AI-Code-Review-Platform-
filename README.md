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
python llm_review/run_review.py llm_review/tests/fixtures/mock_analysis_input.json

# Demo the fallback paths
python llm_review/run_review.py llm_review/tests/fixtures/mock_analysis_input.json --fail      # timeout
python llm_review/run_review.py llm_review/tests/fixtures/mock_analysis_input.json --garbage   # bad output

# Real LLM (needs ANTHROPIC_API_KEY — see Environment setup below)
python llm_review/run_review.py llm_review/tests/fixtures/mock_analysis_input.json --real

# Tests
python llm_review/tests/test_pipeline.py   # stdlib runner
python -m pytest llm_review/tests/ -v      # if pytest installed
```

### Environment setup

`--real` needs `ANTHROPIC_API_KEY` set. Easiest way locally:

```bash
cp .env.example .env    # then fill in ANTHROPIC_API_KEY
pip install -r requirements.txt
```

`run_review.py` calls `load_dotenv()` on startup, so a `.env` file in the
repo root (or any parent of your working directory) is picked up
automatically — no need to `export` it yourself. `.env` is gitignored;
`.env.example` lists every variable the project currently reads (not just
the LLM one) and has no real values, so it's safe to commit.

In CI or a real deployment, just set `ANTHROPIC_API_KEY` as an actual
environment variable / secret — `load_dotenv()` is a no-op if there's no
`.env` file to find, so nothing here depends on one existing.

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

## External access to the local model

`llm_review/api.py` is a **standalone Flask app**, separate from
`backend/app` entirely, exposing the locally-running Ollama model
(`qwen2.5-coder:7b` by default) to other projects as a plain prompt-in,
text-out API. It does not run the review pipeline or send any review
prompt -- it's a thin passthrough for free-text generation.

```bash
cp .env.example .env     # fill in LLM_API_KEY (required -- the app
                          # refuses to start without it)
python -m llm_review.api # runs on port 5100 by default (LLM_API_PORT)
```

```bash
curl -X POST http://localhost:5100/api/llm/generate \
  -H "X-LLM-API-Key: <your LLM_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Say hello in three words.", "system": "You are terse."}'
# {"text": "Hello there, friend.", "model": "qwen2.5-coder:7b", "latency_seconds": 1.2}
```

By default the app binds to `127.0.0.1` (loopback-only, `LLM_API_HOST`)
with Flask's debug mode off -- reachable from this machine alone, even
though the port is technically open. **For another team/project reaching
this from off-machine:** set `LLM_API_HOST` to this Mac's Tailscale IP
(not `0.0.0.0`, which would bind every interface, including any public
one) before running `python -m llm_review.api`. This app's port (5100 by
default) is the *only* thing that should ever be exposed via a tunnel
(Tailscale recommended; ngrok as a fallback) -- never Ollama's own port
(11434) directly. And since this is a personal machine, not a server:
**it only works while this Mac is awake, Ollama is running, and this app
is running** -- there's no uptime guarantee.

Request limits: prompts over ~20,000 characters are rejected (413); each
API key is capped at 10 requests/minute (429 with `Retry-After` past
that); requests that don't complete within `LLM_API_TIMEOUT_SECONDS`
(default 60s) return 504, and an unreachable/misconfigured Ollama
returns 502 -- never a raw stack trace or exception string.

## Layout

```
llm_review/
  types.py       # dataclasses mirroring the schemas
  prompt.py      # system + per-file prompt (asks for strict JSON)
  client.py      # LLMClient protocol, MockLLMClient, AnthropicLLMClient
  severity.py    # category→severity table + scoring
  pipeline.py    # run → parse → combine → severity → summary (+ fallback)
  api.py         # standalone app exposing /api/llm/generate externally
run_review.py    # CLI
tests/           # unit tests incl. fallback + redaction gate + api.py
schemas/         # shared input/output contracts
```
