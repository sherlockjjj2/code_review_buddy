"""Integration smoke test for snapshot artifact capture."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from src.github_client import GitHubResponseCache
from src.snapshot_capture import capture_snapshot_artifact, resolve_smoke_target


def _has_github_token() -> bool:
    """Return whether a GitHub token is configured."""
    return bool(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"))


@pytest.mark.integration
def test_capture_snapshot_artifact_writes_expected_payload(tmp_path: Path) -> None:
    if not _has_github_token():
        pytest.skip("Set GITHUB_TOKEN or GH_TOKEN for integration tests.")

    repo, pr_number = resolve_smoke_target()
    cache = GitHubResponseCache(tmp_path / "github_snapshot_cache.sqlite")
    output_dir = tmp_path / "snapshots"

    output_path = capture_snapshot_artifact(
        repo_full_name=repo,
        pr_number=pr_number,
        output_dir=output_dir,
        cache=cache,
    )

    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "v1"
    assert payload["repository"] == repo
    assert payload["pr_number"] == pr_number
    assert payload["base_sha"]
    assert payload["head_sha"]
    assert payload["raw_diff"]
    assert len(payload["files"]) >= 1
