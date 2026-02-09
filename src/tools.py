"""Tool registry and tool contracts."""

from __future__ import annotations

from typing import Protocol


class Tool(Protocol):
    """Protocol for callable tools used by the reviewer."""

    def __call__(self, *args: object, **kwargs: object) -> dict[str, object]:
        """Execute the tool and return a structured response."""
