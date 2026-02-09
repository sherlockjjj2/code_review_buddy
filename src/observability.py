"""Run telemetry models and helpers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RunTelemetry:
    """Redacted run telemetry summary."""

    run_id: str
    review_id: str
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    warnings: list[str] = field(default_factory=list)
