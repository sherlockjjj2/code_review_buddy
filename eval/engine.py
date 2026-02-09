"""Scoring engine for eval runs."""

from __future__ import annotations

from src.schema import EvalResult


def score_eval_run() -> EvalResult:
    """Return placeholder metrics for scaffolding stage."""
    return EvalResult(
        recall=0.0,
        precision=0.0,
        f1=0.0,
        avg_confidence_calibration=0.0,
        cost_usd=0.0,
        latency_seconds=0.0,
    )
