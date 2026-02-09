"""GitHub API wrapper and auth helpers."""

from __future__ import annotations

import os

import httpx


class GitHubAuthError(RuntimeError):
    """Raised when required GitHub authentication is missing."""


def get_github_token() -> str:
    """Read GitHub token from environment and fail fast if missing."""
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        message = "Missing GitHub token. Set GITHUB_TOKEN (preferred) or GH_TOKEN."
        raise GitHubAuthError(message)
    return token


def build_github_client(timeout_seconds: int = 20) -> httpx.Client:
    """Build an authenticated GitHub HTTP client."""
    token = get_github_token()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return httpx.Client(base_url="https://api.github.com", headers=headers, timeout=timeout_seconds)
