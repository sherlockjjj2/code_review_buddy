"""Eval runner orchestration."""

from __future__ import annotations

from src.schema import EvalResult

from eval.engine import score_eval_run


def run_eval_suite() -> EvalResult:
    """Run the eval suite and return aggregate metrics."""
    return score_eval_run()
