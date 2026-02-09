# PLANS.md — Implementation Checklist (Week Plan)

This file is the build plan and checklist for executing V1.

## Quick Start Checklist

- [x] Scaffold repo + deps + ruff + typer + pydantic
- [x] Implement schema contract + JSON roundtrip tests
- [ ] Implement GitHub client (PR metadata, files, diffs, file contents) + caching
- [ ] Build eval suite (10 cases + snapshots) + scoring engine + runner
- [ ] Build baseline single-call reviewer + record baseline metrics
- [ ] Implement tools + registry (read_file, search_code, run_ruff, get_function_context)
- [ ] Add bounded verify loop + optional escalation + redacted observability
- [ ] Add smart context manager + prompt experiments + comparison table
- [ ] Implement robust error handling (LLM/tool/rate-limit/cost protection)
- [ ] Implement outputs (idempotent PR comment + markdown report)
- [ ] Ship CLI (`review`, `eval`) + README + final results

## Day-by-Day Plan (Tasks)

### Day 1 — Scaffolding + GitHub Client + Schema

- T1.1 Project scaffolding
- T1.2 Output schema
- T1.3 GitHub API client: PR metadata + diff + pagination + parse changed line ranges
- T1.4 GitHub API client: file contents for base/head; handle binary/new/deleted
- T1.5 Smoke test + snapshot

### Day 2 — Eval Framework + Test Case Curation

- T2.1 Curate 10 PR cases + ground truth + cache snapshots
- T2.2 Eval scoring engine (matching logic; recall/precision/F1)
- T2.3 Eval runner + run logs
- T2.4 Dummy agent verification

### Day 3 — Baseline Review + Tool Design

- T3.1 Baseline single prompt review + record metrics
- T3.2 System prompt V1
- T3.3–T3.7 Implement tools + registry

### Day 4 — Bounded Verification Loop + Escalation

- T4.1 Bounded verify loop (max LLM/tool budgets)
- T4.2 Confidence-based escalation
- T4.3 Redacted observability
- T4.4 Compare eval vs baseline

### Day 5 — Prompt Engineering + Context Management

- T5.1 Few-shot variant
- T5.2 Rubric variant
- T5.3 “Understand-first” summary variant
- T5.4 Prompt comparison table
- T5.5 Smart context manager
- T5.6 Re-eval and record new best

### Day 6 — Error Handling + Output Delivery

- T6.1 Tool failures
- T6.2 LLM failures (retries, JSON repair)
- T6.3 Cost protection
- T6.4 PR comment writer
- T6.4b Idempotent comments + dedupe
- T6.4c Evidence rendering
- T6.5 Markdown report generator
- T6.6 Full pipeline integration test

### Day 7 — CLI + Polish + Ship

- T7.1 CLI `review`
- T7.2 CLI `eval`
- T7.3 Final eval summary (`runs/final_results.md`)
- T7.4 README
- T7.5 RedNote draft
- T7.6 Cleanup

## Success Criteria (Targets)

- Recall ≥ 80%, Precision ≥ 80%, cost/review < $0.50, latency < 60s
- 10 cached cases (6 Python, 4 JS/TS)
- 3+ prompt variants with metrics
- Idempotent comments + dedupe; 100% evidence coverage
- CLI `review` + `eval` end-to-end

---

## AGENTS.md Starter (Copied Here For Convenience)

See `AGENTS.md` for the authoritative version.
