"""Shared pytest fixtures and test-run configuration."""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom pytest options for integration test execution."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run tests marked as integration (external dependencies).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration tests unless explicitly enabled."""
    run_integration = config.getoption("--run-integration")
    env_enabled = os.getenv("RUN_INTEGRATION_TESTS") == "1"
    if run_integration or env_enabled:
        return

    skip_marker = pytest.mark.skip(
        reason=(
            "Integration tests are disabled by default. "
            "Use --run-integration or set RUN_INTEGRATION_TESTS=1."
        )
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)
