# Code Review Agent — spec.md (V1)

## System Overview

An eval-driven GitHub PR review agent built without agent frameworks. It fetches PR metadata + diffs, selectively loads context, optionally runs lightweight tooling (ruff/tsc/eslint when available), and produces a structured `ReviewResult` that is rendered into:

- GitHub PR comment (GFM)

- Local Markdown report

Run artifacts are written to `runs/{timestamp}_{review_id}/` with `review.md`, `review.json`, and `telemetry.json`.

## Repository Architecture (Proposed)

```
code-review-agent/
├── src/
│ ├── agent.py # single-pass reviewer + bounded verify loop + orchestration
│ ├── tools.py # tool registry + implementations
│ ├── github_client.py # GitHub API wrapper
│ ├── prompts.py # system prompts + few-shot examples
│ ├── schema.py # Pydantic models (contract)
│ ├── context.py # smart file loading + token management
│ ├── output.py # PR comment writer + markdown report generator
│ ├── observability.py # run logging, cost tracking, metrics
│ └── cli.py # Typer CLI
├── eval/
│ ├── engine.py # scoring logic
│ ├── runner.py # run agent against test suite
│ └── data/
│ ├── cases.json # PR metadata + ground truth annotations
│ └── snapshots/ # cached PR diffs + file contents
├── runs/ # logged runs (redacted JSON by default)
├── tests/
└── pyproject.toml
```

## Contracts (Output Schema)

**This is the shared contract** between agent, eval engine, and output formatters.

### Issue

- `file`, `line_start`, `line_end?`
- `severity`: critical/high/medium/low
- `category`: security/bug/error_handling/performance/style/logic
- `description`, `suggestion`
- **Guardrails**
  - `evidence_snippet` (required; tied to diff hunk or fetched context)
  - `dedupe_key` (stable across reruns)
- `confidence` (0.0–1.0)
- `language`: python/javascript/typescript

### ReviewResult

- `review_id` (stable per PR+head_sha+config; idempotency)
- `status`: ok/truncated/error
- `model_used`, `warnings`
- `issues[]`, `summary`, `files_reviewed[]`
- `stats`: tokens_used, cost_usd, latency_seconds_e2e, latency_seconds_llm, llm_calls

### EvalResult

- recall, precision, f1
- avg_confidence_calibration
- cost_usd, latency_seconds

## LLM Flow

### Baseline

- Single LLM call: (system prompt + PR description + diff + selected context) → `ReviewResult` JSON
- Parsing guard: JSON parse → on failure retry with “return valid JSON” (max 2 attempts)

### Bounded Verification Loop (Optional, Recommended)

- `max_llm_calls = 2`
- `max_tool_calls = 3`
- `max_verify_candidates = 5`
- `max_output_issues = 15`
- `max_wall_time_seconds = 60`
- `max_cost_usd = 0.50`

1. Draft review from baseline call
2. If needed, request specific extra context via tools
3. Final `ReviewResult`

### Escalation (Optional)

If any issue is (critical|high) **and** confidence < 0.6, optionally escalate analysis of that file/diff to a stronger model. Record model used.

Default model policy: OpenAI-first with `gpt-4.1-mini` as reviewer default and `gpt-4.1` as optional escalation model.

## Tools (APIs)

### `read_file`

Input: `{repo, path, ref}` → Output: `{content, language, line_count}` (+ structured error)

### `search_code`

Input: `{repo, query, language_filter?}` → Output: `{matches:[{file, line, snippet}]}`  
Rate limit aware; fallback: local clone + grep.

### `run_ruff`

Input: `{file_content, filename}` → Output: `{findings:[{rule, line, message, severity}]}`  
Skips non-Python.

### `get_function_context`

Input: `{file_content, function_name, language}` → Output: `{definition, imports, start_line, end_line}`  
Python via `ast`; JS/TS heuristic.

## Context Management

- Always include: full diff.
- For each changed file: include changed lines ±20 lines, function-boundary aware.
- Prefer symbol-level extraction where possible.
- On-demand context expansion triggers:
  - issue confidence below `0.65`
  - evidence insufficient to support claim
  - suspected cross-file dependency impact
- Expansion caps: up to `2` extra files and `200` lines per extra file.
- Enforce token budgets (rough estimate acceptable; better if provider token counter exists).
- If over budget: prioritize files with most changes; truncate safely; set `status="truncated"` and add warnings.

## Invariants (Must Hold)

- Evidence-first: do not output issues without `evidence_snippet`.
- Idempotency: compute `review_id`; update existing bot comment if marker exists.
- Hard budgets: never exceed configured limits (LLM calls, tool calls, tokens/cost).
- No secrets in logs; redacted observability by default.

Idempotency details:

- Comment marker first line: `<!-- code-review-agent:review_id={id} -->`.
- `review_id = sha256(repo, pr_number, head_sha, prompt_version, model, budget_profile)[:16]`.

Auth contract:

- Read token from `GITHUB_TOKEN` (fallback `GH_TOKEN`), fail fast if missing.
- Required permissions: Pull requests (read), Contents (read), Issues (write).

Eval matching rule:

- True-positive matching requires same `file`, same `category`, and line overlap with `±3` tolerance.
- Severity affects scoring calibration but is not a strict match precondition.

## Edge Cases & Failure Handling

- GitHub rate limits: honor `Retry-After`, exponential backoff, ETag/If-None-Match caching.
- PRs with 100+ files: pagination; prioritize.
- Binary/deleted/new files: skip gracefully and warn.
- LLM invalid JSON: retry with error message; fallback extraction only if needed.
- Tool failures: continue with partial info; note limitations in output.
- Repo lacks TS configs: skip tsc/eslint and add warning.
- TS checks present but not executable (for example dependency/runtime failure): warn and continue.

## Test Plan

- Schema JSON roundtrip tests.
- GitHub client smoke test on a public PR.
- Dummy agent sanity checks for eval engine (perfect match vs random).
- Unit tests for each tool.
- Integration test: fetch PR → review → log → report → (optional) comment to test repo.
