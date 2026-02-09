# code_review_buddy

Eval-first GitHub PR code review agent.

## Requirements

- Python 3.13 (`.python-version` is set to `3.13`)

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
```
