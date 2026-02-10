"""Integration tests for GitHub client against live GitHub API."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from src.github_client import (
    GitHubResponseCache,
    build_github_client,
    fetch_pull_request_file_contents,
    fetch_pull_request_files,
    fetch_pull_request_metadata,
    fetch_pull_request_snapshot,
)


def _integration_target() -> tuple[str, int]:
    """Return repo/pr target configured for integration tests."""
    repo = os.getenv("GITHUB_TEST_REPO")
    pr_value = os.getenv("GITHUB_TEST_PR")
    if not repo or not pr_value:
        pytest.skip("Set GITHUB_TEST_REPO and GITHUB_TEST_PR to run GitHub integration tests.")
    try:
        pr_number = int(pr_value)
    except ValueError as error:
        raise pytest.SkipTest("GITHUB_TEST_PR must be an integer.") from error
    return repo, pr_number


def _has_github_token() -> bool:
    """Return whether a GitHub token is configured."""
    return bool(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"))


@pytest.mark.integration
def test_live_fetch_pull_request_snapshot(tmp_path: Path) -> None:
    if not _has_github_token():
        pytest.skip("Set GITHUB_TOKEN or GH_TOKEN for integration tests.")
    repo, pr_number = _integration_target()
    cache = GitHubResponseCache(tmp_path / "github_integration_cache.sqlite")

    with build_github_client(timeout_seconds=20) as client:
        snapshot = fetch_pull_request_snapshot(
            client=client,
            repo_full_name=repo,
            pr_number=pr_number,
            cache=cache,
        )

    assert snapshot.repository == repo
    assert snapshot.metadata.number == pr_number
    assert snapshot.raw_diff
    assert len(snapshot.files) >= 1


@pytest.mark.integration
def test_live_cache_persists_and_reuses_entries(tmp_path: Path) -> None:
    if not _has_github_token():
        pytest.skip("Set GITHUB_TOKEN or GH_TOKEN for integration tests.")
    repo, pr_number = _integration_target()
    cache_db_path = tmp_path / "github_integration_cache.sqlite"
    cache = GitHubResponseCache(cache_db_path)

    with build_github_client(timeout_seconds=20) as client:
        first = fetch_pull_request_metadata(
            client=client,
            repo_full_name=repo,
            pr_number=pr_number,
            cache=cache,
        )
        second = fetch_pull_request_metadata(
            client=client,
            repo_full_name=repo,
            pr_number=pr_number,
            cache=cache,
        )

        files = fetch_pull_request_files(
            client=client,
            repo_full_name=repo,
            pr_number=pr_number,
            cache=cache,
        )
        subset = tuple(files[: min(3, len(files))])
        resolved_files, _warnings = fetch_pull_request_file_contents(
            client=client,
            repo_full_name=repo,
            metadata=first,
            files=subset,
            cache=cache,
        )

    assert first == second
    assert len(resolved_files) == len(subset)

    with sqlite3.connect(cache_db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM github_response_cache").fetchone()
    assert row is not None
    assert int(row[0]) > 0
