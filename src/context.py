"""Context payload construction and token-budget helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ContextBudget:
    """Context budget constraints used when constructing LLM inputs."""

    max_llm_calls: int = 2
    max_tool_calls: int = 3
    max_verify_candidates: int = 5
    max_output_issues: int = 15
    max_wall_time_seconds: int = 60
    max_cost_usd: float = 0.50
