# Eval fixtures for llm_review

Held-out test cases for measuring any LLM client against `llm_review`'s review
task (currently used to evaluate Qwen2.5-Coder 7B fine-tuning). Each fixture
is a pair of files the fine-tuned model is scored against; none of this data
may ever reach the model during training.

## Hold-out policy (read this before touching anything under `eval/`)

**Nothing under `eval/fixtures/` may be used as fine-tuning training data.**
Reusing an eval example for training invalidates every number this eval set
produces -- the whole point is measuring the model on cases it has never
seen.

**Nothing under `eval/fixtures/` may be used as a few-shot example in
`SYSTEM_PROMPT` either**, or in any other prompt-engineering artifact. Tuning
the prompt against the eval set is the same contamination problem as
training on it: the eval stops measuring generalization and starts measuring
memorization of its own test cases.

If you need an example to demonstrate prompt behavior, write a new one that
isn't checked in here.

Guardrails:
- `eval/` is a standalone top-level directory, never nested inside wherever
  training data ends up living, so the two can't accidentally merge.
- `eval/fixtures/MANIFEST.json` records a content hash of each case's diff
  text. Whatever script eventually builds the training set should check
  incoming examples against this manifest and skip matches -- a check that
  doesn't rely on anyone remembering this file exists.

## Layout

```
eval/
  README.md                       # this file
  fixtures/
    MANIFEST.json                 # case id -> content hash, for training-data dedup
    <case-id>/
      analysis_input.json         # fed to the model verbatim
      expected_findings.json      # grading rubric for this case
```

One directory per case, named descriptively in kebab-case (e.g.
`sql-injection-basic`). Not flat `<name>.analysis_input.json` pairs --
a subdirectory per case stays readable as the set grows into dozens of
cases.

## `analysis_input.json`

Exactly the existing `analysis_input` contract
(`schemas/analysis_input.example.json`) -- nothing eval-specific. This file's
entire job is to be indistinguishable from what the pipeline feeds
`build_user_prompt` for a real submission. `redaction_applied` must always be
`true`.

## `expected_findings.json`

A grading rubric, not a byte-for-byte expected output. Free-text fields
(`reference_title`/`reference_explanation`/`reference_suggested_fix`) are
reference text for a human or LLM-judge pass -- phrasing is never
exact-matched. The structured fields (`category`, `line`,
`relates_to_static_finding_id`) are exact-matched (line within tolerance,
see below), since those are what actually drive pipeline behavior downstream
(`category` feeds `assign_severity`; `relates_to_static_finding_id` feeds
`_combine`'s matching).

```json
{
  "case_id": "<matches the directory name>",
  "must_catch": [
    {
      "id": "mc1",
      "category": "<a llm_review.types.Category value>",
      "line": 45,
      "relates_to_static_finding_id": "sf_002",
      "reference_title": "...",
      "reference_explanation": "...",
      "reference_suggested_fix": "..."
    }
  ],
  "may_catch": [ /* same shape as must_catch */ ],
  "must_not_flag": [
    {"line": 46, "note": "why a finding here would be wrong"}
  ]
}
```

- **`must_catch`** -- required findings. Missing one is a failure. This is
  where the deliberately-injected bug goes.
- **`may_catch`** -- findings a good model might independently notice that
  don't count against the model if missed, but count in its favor if found.
  Keeps the eval from being unfairly strict about genuine judgment calls.
- **`must_not_flag`** -- false-positive control. A model finding at one of
  these lines is a specific, deliberately-provoked wrong answer (commenting
  on unchanged/unrelated code), scored separately from generic over-eagerness
  (see **unexpected**, below).
- No `severity` field anywhere: severity isn't LLM output -- `assign_severity`
  derives it downstream from `category`. Grading the model on it directly
  would be grading the wrong layer. A harness can still compute it post-hoc
  from the matched `category` for reporting.
- A case with empty `must_catch`/`may_catch` and `must_not_flag` covering
  every line is a true-negative "clean diff" case -- just as important as
  the positive cases, since it's the only way to catch a model that
  over-flags.
- Every `category` value must be a real member of `llm_review.types.Category`
  -- validate against the enum when adding a fixture, not just eyeballed.

## Line-number matching: ±1 tolerance

Checked directly against what the model is actually shown
(`build_user_prompt` / `prompt.py`): the diff hunk's header gives only a
*starting* line number (`@@ -42,2 +42,5 @@`); every individual line's number
has to be inferred by the model counting through context/added/removed lines
from that header. There is no per-line number printed anywhere in the prompt.
Since the model has no explicit, unambiguous line number to read off, exact
equality is the wrong bar -- **matching allows ±1 line** on every line
comparison in this document (`must_catch`, `may_catch`, and `must_not_flag`
alike).

## Scoring

For each model-reported finding, attempt to match it against the fixture's
expectations **in this order**, consuming the model finding on the first
match (one model finding can satisfy at most one expectation):

1. **`must_catch`** -- category exact match AND `|model.line - expected.line| <= 1`.
2. **`may_catch`** -- same matching rule.
3. **`must_not_flag`** -- `|model.line - expected.line| <= 1` (category irrelevant: any finding this close to a forbidden line counts as a hit).
4. Anything left over is **`unexpected`**.

This order matters where expected lines sit close together (e.g. a
`must_catch` at line 45 next to a `must_not_flag` at line 46, whose ±1
windows overlap) -- a finding that could satisfy either is credited to the
deliberately-placed expectation, not punished as a trap hit.

Report two metrics **separately** per matched `must_catch`/`may_catch` entry,
so a correct finding with a wrong static-link doesn't lose all its credit:

- **(a) Content match**: did a model finding satisfy this entry's category +
  line (within tolerance)? Pass/fail per entry; this is the entry's recall
  signal.
- **(b) Static-link correctness**: *only evaluated when (a) passed* -- does
  the matched finding's `relates_to_static_finding_id` equal the entry's
  (including both being `null`)? Reported as its own pass/fail, not folded
  into (a) -- a model that finds the right bug but fails to (or wrongly
  does) link it to a static finding should show up as "content: pass,
  link: fail", not as a single conflated failure.

Separately report, not folded into the above:

- **`must_not_flag` hits** -- count of forbidden-line matches (step 3).
- **`unexpected`** -- count of findings matching nothing (step 4). This is a
  different signal from a `must_not_flag` hit: it's generic hallucination
  anywhere in the file, not a hit on a specifically provoked trap.

## Adding a fixture

1. Create `eval/fixtures/<case-id>/`.
2. Write `analysis_input.json` (real or synthetic diff + static findings,
   `redaction_applied: true`).
3. Write `expected_findings.json` (validate every `category` against
   `llm_review.types.Category` first).
4. Add an entry to `MANIFEST.json`: `sha256` of the concatenated
   `files[].hunks[].diff` text (in file/hunk order), plus the date added.
