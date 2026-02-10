"""Tests for the CLI auth-check command."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from src import cli
from src.github_client import GitHubAuthError, PullRequestFile, PullRequestMeta
from typer.testing import CliRunner

runner = CliRunner()


@dataclass
class _DummyClientContext:
    """Simple context manager to stand in for an HTTP client."""

    def __enter__(self) -> _DummyClientContext:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None


@pytest.mark.unit
def test_auth_check_fails_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_missing_token() -> tuple[str, str]:
        raise GitHubAuthError("Missing token.")

    monkeypatch.setattr(cli, "get_github_token_with_source", _raise_missing_token)
    result = runner.invoke(cli.app, ["auth-check"])

    assert result.exit_code == 1
    assert "GitHub auth check failed" in result.output


@pytest.mark.unit
def test_auth_check_succeeds_with_repo_and_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = PullRequestMeta(
        number=42,
        title="Title",
        body="Body",
        state="open",
        draft=False,
        author_login="octocat",
        html_url="https://github.com/acme/rocket/pull/42",
        base_ref="main",
        base_sha="base-sha",
        head_ref="branch",
        head_sha="head-sha",
    )
    files = (
        PullRequestFile(
            path="src/main.py",
            status="modified",
            additions=1,
            deletions=1,
            changes=2,
            patch="@@ -1,1 +1,1 @@\n-old\n+new",
        ),
    )

    monkeypatch.setattr(cli, "get_github_token_with_source", lambda: ("token", "GITHUB_TOKEN"))
    monkeypatch.setattr(
        cli,
        "build_github_client",
        lambda timeout_seconds=20, trust_env=True: _DummyClientContext(),
    )
    monkeypatch.setattr(cli, "fetch_authenticated_user_login", lambda client: "octocat")
    monkeypatch.setattr(
        cli,
        "fetch_pull_request_metadata",
        lambda client, repo_full_name, pr_number: metadata,
    )
    monkeypatch.setattr(
        cli,
        "fetch_pull_request_files",
        lambda client, repo_full_name, pr_number: files,
    )
    monkeypatch.setattr(
        cli,
        "fetch_pull_request_file_contents",
        lambda client, repo_full_name, metadata, files: ((), ()),
    )

    result = runner.invoke(cli.app, ["auth-check", "--repo", "acme/rocket", "--pr", "42"])

    assert result.exit_code == 0
    assert "Token detected in GITHUB_TOKEN." in result.output
    assert "Authenticated as GitHub user 'octocat'." in result.output
    assert "Repository/PR access check passed for acme/rocket#42." in result.output
    assert "GitHub token setup is valid." in result.output
