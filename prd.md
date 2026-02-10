# Code Review Agent — PRD (V1)

## Product Goal

Build an AI agent that reviews GitHub Pull Requests (PRs), finds bugs and risks, suggests improvements, and posts results as:

1. a GitHub PR comment, and
2. a local Markdown report.

The build is **eval-first** (quantitative scoring on real PRs) and uses **raw LLM APIs** (no agent frameworks) for maximum control and debuggability.

## Target Users

- Solo developers and indie builders who want faster, higher-signal PR review.
- OSS maintainers who want a consistent, repeatable review baseline.
- Learners building an agent portfolio project with real evaluation.

## Primary Use Cases

- Review a PR given `owner/repo` + `pr_number`.
- Produce a readable PR comment (GitHub-flavored Markdown).
- Generate a richer local report artifact.
- Run an eval suite against curated test PRs and track metrics over time.

## Scope (V1 / MVP)

### Inputs

- GitHub PR via API (real-world diff + minimal surrounding context; fetch more only on-demand).
- Auth: Personal Access Token (PAT) via env var.
  - Read `GITHUB_TOKEN` (fallback `GH_TOKEN`).
  - Local development uses `.env` loaded via `python-dotenv` (`load_dotenv`).
  - Fail fast when token is missing.
  - Required GitHub permissions: Pull requests (read), Contents (read), Issues (write).

### Languages

- Python + JavaScript/TypeScript.

### Outputs

- GitHub PR comment + local Markdown report file.
- Logged run artifact (redacted by default).
- Standard artifact layout: `runs/{timestamp}_{review_id}/review.md`, `review.json`, `telemetry.json`.

### Non‑negotiable MVP Guardrails

- Idempotency + dedupe (update an existing bot comment; avoid duplicates).
- Evidence-first: every issue must include an `evidence_snippet`.
- Hard budgets (default profile):
  - Max LLM calls: `2` (`1` review + optional `1` verify).
  - Max tool calls: `3` total.
  - Max verify candidates: `5` highest-risk issues.
  - Max output issues: `15`.
  - Max wall time: `60s`.
  - Max cost per review run: `$0.50`.
  - On budget exceed, stop immediately and return partial output with `status="truncated"`.
- Rate limits + retries + caching of PR snapshots.
- Redacted observability by default (no secrets; full logs only behind explicit debug flag).
- TS signal: run `tsc --noEmit` and/or `eslint` when repo configs exist; warn-only fallback if checks cannot run (for example missing deps or runtime errors).

## Out of Scope (V1)

- GitHub App auth / org installation.
- Inline PR review comments (line-level) beyond the single PR comment.
- Advanced multi-language support beyond Python + JS/TS.
- Open-ended multi-step “agent brains” or unbounded loops.

## Success Metrics / Acceptance Criteria

- **Recall ≥ 80%**, **Precision ≥ 80%** on the test suite.
- Avg **cost per review < $0.50** and default hard cap `$0.50` per `review` run.
- Avg **latency < 60s** (target) measured on full end-to-end runtime; also track LLM-only latency as a secondary metric.
- 10 test cases (6 Python, 4 JS/TS) with cached snapshots.
- 3+ prompt variants tested with quantitative comparison.
- No duplicate bot comments; re-runs update the same comment.
- 100% of posted issues include evidence snippets.
- CLI provides `review` and `eval` commands working end-to-end.

## Day 2 Milestone (Eval Framework + Case Curation)

- Curate 10 public PR cases with ground truth annotations (`6` Python, `4` JS/TS).
- Persist reusable PR snapshots under `eval/data/snapshots/` and map them in `eval/data/cases.json`.
- Implement eval scoring (`recall`, `precision`, `f1`, confidence calibration) in `eval/engine.py`.
- Implement eval orchestration in `eval/runner.py` with case filtering (`all|python|js|ts`).
- Validate eval path with dummy-agent fixtures (expected high score vs low score).

## Evaluation Matching Rules (V1)

- Match a predicted issue to ground truth when all are true:
  - same `file`
  - same `category`
  - line overlap with tolerance `±3`
- Severity is tracked as a scoring bonus/penalty signal, not a hard match requirement.
- Initial 10-case suite is curated from public PRs (6 Python, 4 JS/TS) with cached snapshots.

## Key Product Decisions (V1)

- Input: GitHub PR via API
- Auth: PAT (fast for V1)
- Review scope: diff + minimal surrounding context; fetch more only on-demand
  - On-demand context triggers: low confidence (`<0.65`), insufficient evidence, or cross-file dependency suspicion.
  - Expansion caps: at most `2` extra files and `200` lines per file.
- Output: PR comment + markdown report artifact
- Linting: ruff for Python; `tsc`/`eslint` when present; otherwise LLM-only for JS/TS
- Idempotent comment marker: `<!-- code-review-agent:review_id={id} -->`
- `review_id` formula: `sha256(repo, pr_number, head_sha, prompt_version, model, budget_profile)[:16]`
- Models: OpenAI-first default `gpt-4.1-mini`; optional escalation to `gpt-4.1` for high/critical issues with confidence `<0.6`
