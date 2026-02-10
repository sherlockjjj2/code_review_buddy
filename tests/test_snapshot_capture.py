"""Unit tests for smoke snapshot capture helpers."""

from __future__ import annotations

from src.github_client import ChangedRange, PullRequestFile, PullRequestMeta, PullRequestSnapshot
from src.snapshot_capture import (
    DEFAULT_SMOKE_PR_NUMBER,
    DEFAULT_SMOKE_REPO,
    GITHUB_SMOKE_PR_ENV_VAR,
    GITHUB_SMOKE_REPO_ENV_VAR,
    build_snapshot_artifact,
    resolve_smoke_target,
)


def make_snapshot() -> PullRequestSnapshot:
    """Build a minimal valid snapshot object for tests."""
    return PullRequestSnapshot(
        repository="acme/rocket",
        metadata=PullRequestMeta(
            number=42,
            title="Fix launch sequence",
            body="Adjust ignition ordering.",
            state="open",
            draft=False,
            author_login="octocat",
            html_url="https://github.com/acme/rocket/pull/42",
            base_ref="main",
            base_sha="base1234567890",
            head_ref="feature/launch-fix",
            head_sha="head1234567890",
        ),
        files=(
            PullRequestFile(
                path="src/launch.py",
                status="modified",
                additions=2,
                deletions=1,
                changes=3,
                patch="@@ -1,1 +1,2 @@\n-a\n+b\n+c",
                changed_ranges=(ChangedRange(line_start=1, line_end=2),),
            ),
        ),
        raw_diff="diff --git a/src/launch.py b/src/launch.py",
        warnings=("1 file(s) missing patch content",),
    )


def test_resolve_smoke_target_defaults(monkeypatch) -> None:
    monkeypatch.delenv(GITHUB_SMOKE_REPO_ENV_VAR, raising=False)
    monkeypatch.delenv(GITHUB_SMOKE_PR_ENV_VAR, raising=False)

    repo, pr_number = resolve_smoke_target()

    assert repo == DEFAULT_SMOKE_REPO
    assert pr_number == DEFAULT_SMOKE_PR_NUMBER


def test_resolve_smoke_target_uses_env(monkeypatch) -> None:
    monkeypatch.setenv(GITHUB_SMOKE_REPO_ENV_VAR, "acme/rocket")
    monkeypatch.setenv(GITHUB_SMOKE_PR_ENV_VAR, "55")

    repo, pr_number = resolve_smoke_target()

    assert repo == "acme/rocket"
    assert pr_number == 55


def test_resolve_smoke_target_uses_explicit_overrides(monkeypatch) -> None:
    monkeypatch.setenv(GITHUB_SMOKE_REPO_ENV_VAR, "ignored/repo")
    monkeypatch.setenv(GITHUB_SMOKE_PR_ENV_VAR, "99")

    repo, pr_number = resolve_smoke_target(repo_full_name="acme/rocket", pr_number=12)

    assert repo == "acme/rocket"
    assert pr_number == 12


def test_build_snapshot_artifact_shape() -> None:
    artifact = build_snapshot_artifact(make_snapshot())

    assert artifact["schema_version"] == "v1"
    assert artifact["repository"] == "acme/rocket"
    assert artifact["pr_number"] == 42
    assert artifact["base_sha"] == "base1234567890"
    assert artifact["head_sha"] == "head1234567890"
    assert artifact["raw_diff"].startswith("diff --git")
    assert isinstance(artifact["files"], list)
    assert artifact["files"][0]["path"] == "src/launch.py"
    assert "base" not in artifact["files"][0]
    assert "head" not in artifact["files"][0]
