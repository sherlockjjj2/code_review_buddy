"""Review orchestration entrypoints."""

from __future__ import annotations

from src.schema import ReviewResult


def review_pull_request() -> ReviewResult:
    """Run a pull request review and return the structured review result."""
    raise NotImplementedError("Review orchestration is not implemented yet.")
