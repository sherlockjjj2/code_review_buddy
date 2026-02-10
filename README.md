# code_review_buddy

Eval-first GitHub PR code review agent.

## Requirements

- Python 3.13 (`.python-version` is set to `3.13`)
- GitHub token in environment:
  - `GITHUB_TOKEN` (preferred) or `GH_TOKEN`
  - local development can use `.env`

## Quick Start

```bash
python3.13 -m venv .venv313
source .venv313/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## Run

```bash
code-review-agent review --repo owner/repo --pr 123 --dry-run
code-review-agent eval --cases all
code-review-agent auth-check
```

Optional PR-level access check:

```bash
code-review-agent auth-check --repo owner/repo --pr 123
```

If your shell exports proxy variables and you do not want to use them:

```bash
code-review-agent auth-check --no-trust-env
```

## Day 1 Smoke Snapshot

Capture a smoke snapshot using a stable public fixture by default:
- Default repo: `octocat/Hello-World`
- Default PR: `1`
- Optional overrides: `GITHUB_SMOKE_REPO`, `GITHUB_SMOKE_PR`

Required auth:
- `GITHUB_TOKEN` (preferred) or `GH_TOKEN`
- `.env` is supported automatically

Command:

```bash
python -m src.snapshot_capture --output-dir eval/data/snapshots
```

The capture writes a JSON artifact to `eval/data/snapshots/` containing PR metadata, changed files,
raw diff, and warnings.

## Integration Tests

Integration tests are opt-in and require GitHub auth:

```bash
uv run pytest -m integration --run-integration
```

You can also enable with environment variable:

```bash
RUN_INTEGRATION_TESTS=1 uv run pytest -m integration
```
