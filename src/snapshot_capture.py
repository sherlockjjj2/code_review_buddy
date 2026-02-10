"""Smoke snapshot capture helpers for GitHub pull requests."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.github_client import (
    DEFAULT_GITHUB_CACHE_DB_PATH,
    GitHubResponseCache,
    PullRequestSnapshot,
    build_github_client,
    fetch_pull_request_snapshot,
    parse_repo_full_name,
    validate_pr_number,
)

DEFAULT_SMOKE_REPO = "octocat/Hello-World"
DEFAULT_SMOKE_PR_NUMBER = 1
DEFAULT_SNAPSHOT_OUTPUT_DIR = Path("eval/data/snapshots")
GITHUB_SMOKE_REPO_ENV_VAR = "GITHUB_SMOKE_REPO"
GITHUB_SMOKE_PR_ENV_VAR = "GITHUB_SMOKE_PR"


def resolve_smoke_target(
    *,
    repo_full_name: str | None = None,
    pr_number: int | None = None,
) -> tuple[str, int]:
    """Resolve smoke snapshot target with defaults and env overrides."""
    resolved_repo = repo_full_name or os.getenv(GITHUB_SMOKE_REPO_ENV_VAR) or DEFAULT_SMOKE_REPO
    parse_repo_full_name(resolved_repo)

    if pr_number is not None:
        return resolved_repo, validate_pr_number(pr_number)

    pr_value = os.getenv(GITHUB_SMOKE_PR_ENV_VAR)
    if pr_value is not None:
        try:
            parsed_pr_number = int(pr_value)
        except ValueError as error:
            raise ValueError(
                f"{GITHUB_SMOKE_PR_ENV_VAR} must be an integer, got '{pr_value}'."
            ) from error
        return resolved_repo, validate_pr_number(parsed_pr_number)

    return resolved_repo, DEFAULT_SMOKE_PR_NUMBER


def build_snapshot_artifact(snapshot: PullRequestSnapshot) -> dict[str, Any]:
    """Build JSON-serializable artifact payload for a pull request snapshot."""
    metadata = asdict(snapshot.metadata)
    files = [asdict(changed_file) for changed_file in snapshot.files]
    captured_at = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "v1",
        "captured_at": captured_at,
        "repository": snapshot.repository,
        "pr_number": snapshot.metadata.number,
        "base_sha": snapshot.metadata.base_sha,
        "head_sha": snapshot.metadata.head_sha,
        "metadata": metadata,
        "files": files,
        "raw_diff": snapshot.raw_diff,
        "warnings": list(snapshot.warnings),
    }


def capture_snapshot_artifact(
    *,
    repo_full_name: str,
    pr_number: int,
    output_dir: Path,
    cache: GitHubResponseCache | None = None,
    timeout_seconds: int = 20,
) -> Path:
    """Fetch and persist one pull request snapshot artifact to disk."""
    parse_repo_full_name(repo_full_name)
    normalized_pr_number = validate_pr_number(pr_number)

    with build_github_client(timeout_seconds=timeout_seconds) as client:
        snapshot = fetch_pull_request_snapshot(
            client=client,
            repo_full_name=repo_full_name,
            pr_number=normalized_pr_number,
            cache=cache,
        )

    payload = build_snapshot_artifact(snapshot)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _snapshot_filename(
        repo_full_name=repo_full_name,
        pr_number=normalized_pr_number,
        head_sha=snapshot.metadata.head_sha,
    )
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def _snapshot_filename(*, repo_full_name: str, pr_number: int, head_sha: str) -> str:
    """Build deterministic filename for snapshot output."""
    owner, repo = parse_repo_full_name(repo_full_name)
    short_sha = head_sha[:12]
    return f"{owner}__{repo}__pr{pr_number}__{short_sha}.json"


def _build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for snapshot capture utility."""
    parser = argparse.ArgumentParser(description="Capture a GitHub PR snapshot artifact.")
    parser.add_argument(
        "--repo",
        help=(
            "Repository in owner/repo format. "
            f"Defaults to {GITHUB_SMOKE_REPO_ENV_VAR} or {DEFAULT_SMOKE_REPO}."
        ),
    )
    parser.add_argument(
        "--pr",
        type=int,
        help=(
            "Pull request number. "
            f"Defaults to {GITHUB_SMOKE_PR_ENV_VAR} or {DEFAULT_SMOKE_PR_NUMBER}."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_SNAPSHOT_OUTPUT_DIR),
        help=f"Snapshot output directory (default: {DEFAULT_SNAPSHOT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--cache-db",
        default=DEFAULT_GITHUB_CACHE_DB_PATH,
        help=f"SQLite cache path (default: {DEFAULT_GITHUB_CACHE_DB_PATH}).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache for this capture run.",
    )
    return parser


def main() -> None:
    """Capture a smoke snapshot and print the artifact path."""
    parser = _build_parser()
    args = parser.parse_args()
    repo_full_name, pr_number = resolve_smoke_target(repo_full_name=args.repo, pr_number=args.pr)
    cache = None if args.no_cache else GitHubResponseCache(args.cache_db)
    output_path = capture_snapshot_artifact(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        output_dir=Path(args.output_dir),
        cache=cache,
    )
    print(output_path)


if __name__ == "__main__":
    main()
