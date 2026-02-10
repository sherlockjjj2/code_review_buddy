"""GitHub API wrapper and auth helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from dotenv import load_dotenv

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<base_start>\d+)(?:,(?P<base_count>\d+))? "
    r"\+(?P<head_start>\d+)(?:,(?P<head_count>\d+))? @@"
)
COMMIT_SHA_PATTERN = re.compile(r"^[a-f0-9]{7,40}$")
GITHUB_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
DEFAULT_MUTABLE_TTL_SECONDS = 60.0
DEFAULT_BRANCH_CONTENT_TTL_SECONDS = 120.0
DEFAULT_IMMUTABLE_CONTENT_TTL_SECONDS = 60.0 * 60.0 * 24.0 * 30.0
DEFAULT_GITHUB_CACHE_DB_PATH = ".cache/github_cache.sqlite"
GITHUB_CACHE_DISABLED_ENV_VAR = "GITHUB_CACHE_DISABLED"
GITHUB_CACHE_DB_PATH_ENV_VAR = "GITHUB_CACHE_DB_PATH"


@dataclass(frozen=True, slots=True)
class CachePolicy:
    """Per-endpoint cache policy."""

    ttl_seconds: float | None
    immutable: bool


@dataclass(frozen=True, slots=True)
class CachedHttpResponse:
    """Cached response payload with metadata for revalidation."""

    status_code: int
    body: bytes
    etag: str | None
    last_modified: str | None
    fetched_at: float
    expires_at: float | None
    immutable: bool


class GitHubAuthError(RuntimeError):
    """Raised when required GitHub authentication is missing."""


class GitHubInputError(ValueError):
    """Raised when repository or PR input values are invalid."""


class GitHubApiError(RuntimeError):
    """Raised when a GitHub API request fails."""

    def __init__(self, message: str, *, status_code: int, endpoint: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint


class GitHubRateLimitError(GitHubApiError):
    """Raised when GitHub API rate limiting prevents request completion."""


@dataclass(frozen=True, slots=True)
class ChangedRange:
    """Span of changed line numbers in the PR head revision."""

    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class PullRequestMeta:
    """Normalized PR metadata required by the review pipeline."""

    number: int
    title: str
    body: str
    state: str
    draft: bool
    author_login: str
    html_url: str
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str


@dataclass(frozen=True, slots=True)
class PullRequestFile:
    """Changed file details from GitHub pull request files API."""

    path: str
    status: str
    additions: int
    deletions: int
    changes: int
    patch: str | None
    previous_filename: str | None = None
    changed_ranges: tuple[ChangedRange, ...] = ()
    is_binary: bool = False


@dataclass(frozen=True, slots=True)
class PullRequestSnapshot:
    """Aggregate payload used by downstream review/context builders."""

    repository: str
    metadata: PullRequestMeta
    files: tuple[PullRequestFile, ...]
    raw_diff: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class FileContentAtRef:
    """Normalized file-content lookup result at one git ref."""

    ref: str
    path: str
    exists: bool
    sha: str | None
    size_bytes: int | None
    text: str | None
    is_binary: bool
    encoding: str | None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class PullRequestFileContent:
    """Resolved base/head contents for one changed file."""

    path: str
    status: str
    base: FileContentAtRef
    head: FileContentAtRef
    warnings: tuple[str, ...] = field(default_factory=tuple)


class GitHubResponseCache:
    """SQLite-backed response cache for GitHub API GET requests."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        mutable_ttl_seconds: float = DEFAULT_MUTABLE_TTL_SECONDS,
        branch_content_ttl_seconds: float = DEFAULT_BRANCH_CONTENT_TTL_SECONDS,
        immutable_content_ttl_seconds: float = DEFAULT_IMMUTABLE_CONTENT_TTL_SECONDS,
    ) -> None:
        self._db_path = Path(db_path)
        self._mutable_ttl_seconds = mutable_ttl_seconds
        self._branch_content_ttl_seconds = branch_content_ttl_seconds
        self._immutable_content_ttl_seconds = immutable_content_ttl_seconds
        self._ensure_schema()

    def _open_connection(self) -> sqlite3.Connection:
        """Open a SQLite connection."""
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        """Create cache table and indexes if missing."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS github_response_cache (
                    cache_key TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    accept_header TEXT,
                    status_code INTEGER NOT NULL,
                    body BLOB NOT NULL,
                    etag TEXT,
                    last_modified TEXT,
                    fetched_at REAL NOT NULL,
                    expires_at REAL,
                    immutable INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_github_response_cache_expires_at
                ON github_response_cache (expires_at)
                """
            )

    def get(self, cache_key: str) -> CachedHttpResponse | None:
        """Read cache entry by key."""
        with self._open_connection() as connection:
            row = connection.execute(
                """
                SELECT status_code, body, etag, last_modified, fetched_at, expires_at, immutable
                FROM github_response_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()

        if row is None:
            return None
        return CachedHttpResponse(
            status_code=int(row[0]),
            body=bytes(row[1]),
            etag=row[2],
            last_modified=row[3],
            fetched_at=float(row[4]),
            expires_at=float(row[5]) if row[5] is not None else None,
            immutable=bool(row[6]),
        )

    def upsert(
        self,
        *,
        cache_key: str,
        endpoint: str,
        accept_header: str | None,
        status_code: int,
        body: bytes,
        etag: str | None,
        last_modified: str | None,
        fetched_at: float,
        expires_at: float | None,
        immutable: bool,
    ) -> None:
        """Insert or replace cached response entry."""
        with self._open_connection() as connection:
            connection.execute(
                """
                INSERT INTO github_response_cache (
                    cache_key, endpoint, accept_header, status_code, body, etag, last_modified,
                    fetched_at, expires_at, immutable
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    endpoint=excluded.endpoint,
                    accept_header=excluded.accept_header,
                    status_code=excluded.status_code,
                    body=excluded.body,
                    etag=excluded.etag,
                    last_modified=excluded.last_modified,
                    fetched_at=excluded.fetched_at,
                    expires_at=excluded.expires_at,
                    immutable=excluded.immutable
                """,
                (
                    cache_key,
                    endpoint,
                    accept_header,
                    status_code,
                    sqlite3.Binary(body),
                    etag,
                    last_modified,
                    fetched_at,
                    expires_at,
                    int(immutable),
                ),
            )

    def touch(self, *, cache_key: str, fetched_at: float, expires_at: float | None) -> None:
        """Update freshness timestamps for existing cache entry."""
        with self._open_connection() as connection:
            connection.execute(
                """
                UPDATE github_response_cache
                SET fetched_at = ?, expires_at = ?
                WHERE cache_key = ?
                """,
                (fetched_at, expires_at, cache_key),
            )

    def policy_for_endpoint(self, endpoint: str) -> CachePolicy:
        """Return cache policy based on endpoint path and query."""
        parsed = urlsplit(endpoint)
        if "/contents/" in parsed.path:
            ref_values = parse_qs(parsed.query).get("ref")
            ref = ref_values[0] if ref_values else None
            if ref and COMMIT_SHA_PATTERN.fullmatch(ref):
                return CachePolicy(
                    ttl_seconds=self._immutable_content_ttl_seconds,
                    immutable=True,
                )
            return CachePolicy(
                ttl_seconds=self._branch_content_ttl_seconds,
                immutable=False,
            )
        return CachePolicy(
            ttl_seconds=self._mutable_ttl_seconds,
            immutable=False,
        )

    def delete_expired(self, *, now: float) -> None:
        """Prune expired entries."""
        with self._open_connection() as connection:
            connection.execute(
                """
                DELETE FROM github_response_cache
                WHERE expires_at IS NOT NULL AND expires_at <= ?
                """,
                (now,),
            )


_DEFAULT_GITHUB_RESPONSE_CACHE: GitHubResponseCache | None = None


def _default_cache_path() -> Path:
    """Return default cache path, optionally overridden by environment."""
    configured_path = os.getenv(GITHUB_CACHE_DB_PATH_ENV_VAR)
    if configured_path:
        return Path(configured_path)
    return Path(DEFAULT_GITHUB_CACHE_DB_PATH)


def get_github_response_cache() -> GitHubResponseCache | None:
    """Return default GitHub response cache unless disabled."""
    if os.getenv(GITHUB_CACHE_DISABLED_ENV_VAR) == "1":
        return None
    global _DEFAULT_GITHUB_RESPONSE_CACHE
    if _DEFAULT_GITHUB_RESPONSE_CACHE is None:
        _DEFAULT_GITHUB_RESPONSE_CACHE = GitHubResponseCache(_default_cache_path())
    return _DEFAULT_GITHUB_RESPONSE_CACHE


def _cache_key(endpoint: str, accept_header: str | None) -> str:
    """Build cache key that separates JSON vs diff variants."""
    key_material = f"GET\n{endpoint}\n{accept_header or ''}\n{GITHUB_API_VERSION}"
    return hashlib.sha256(key_material.encode("utf-8")).hexdigest()


def _expires_at_for_policy(*, now: float, policy: CachePolicy) -> float | None:
    """Compute expiration timestamp for cache policy."""
    if policy.immutable or policy.ttl_seconds is None:
        return None
    return now + policy.ttl_seconds


def _is_fresh_cache_entry(entry: CachedHttpResponse, *, now: float) -> bool:
    """Return whether cache entry can be reused without network request."""
    if entry.expires_at is None:
        return True
    return now < entry.expires_at


def _response_from_cache(
    *,
    endpoint: str,
    accept_header: str | None,
    cached: CachedHttpResponse,
) -> httpx.Response:
    """Build a lightweight httpx.Response from cached payload."""
    url = f"{GITHUB_API_BASE_URL}{endpoint}"
    request_headers: dict[str, str] = {}
    if accept_header:
        request_headers["Accept"] = accept_header
    response_headers: dict[str, str] = {}
    if cached.etag:
        response_headers["ETag"] = cached.etag
    if cached.last_modified:
        response_headers["Last-Modified"] = cached.last_modified
    return httpx.Response(
        status_code=cached.status_code,
        content=cached.body,
        request=httpx.Request("GET", url, headers=request_headers),
        headers=response_headers,
    )


def _ensure_mapping(value: object, *, context: str) -> dict[str, Any]:
    """Ensure a response fragment is a JSON object."""
    if not isinstance(value, dict):
        raise GitHubApiError(
            f"Expected JSON object for {context}.",
            status_code=500,
            endpoint=context,
        )
    return value


def _require_str(payload: dict[str, Any], *, key: str, endpoint: str) -> str:
    """Read a required string field from payload."""
    value = payload.get(key)
    if not isinstance(value, str):
        raise GitHubApiError(
            f"Expected string field '{key}' in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )
    return value


def _require_bool(payload: dict[str, Any], *, key: str, endpoint: str) -> bool:
    """Read a required boolean field from payload."""
    value = payload.get(key)
    if not isinstance(value, bool):
        raise GitHubApiError(
            f"Expected boolean field '{key}' in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )
    return value


def _require_int(payload: dict[str, Any], *, key: str, endpoint: str) -> int:
    """Read a required integer field from payload."""
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise GitHubApiError(
            f"Expected integer field '{key}' in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )
    return value


def _require_object(payload: dict[str, Any], *, key: str, endpoint: str) -> dict[str, Any]:
    """Read a required object field from payload."""
    value = payload.get(key)
    if not isinstance(value, dict):
        raise GitHubApiError(
            f"Expected object field '{key}' in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )
    return value


def _request_json(
    client: httpx.Client,
    endpoint: str,
    *,
    cache: GitHubResponseCache | None = None,
) -> dict[str, Any]:
    """Perform a JSON request against GitHub API."""
    response = _request_with_retries(
        client,
        endpoint,
        accept_header="application/vnd.github+json",
        cache=cache,
    )
    return _ensure_mapping(response.json(), context=endpoint)


def _request_json_list(
    client: httpx.Client,
    endpoint: str,
    *,
    cache: GitHubResponseCache | None = None,
) -> list[dict[str, Any]]:
    """Perform a JSON request that returns an array of objects."""
    response = _request_with_retries(
        client,
        endpoint,
        accept_header="application/vnd.github+json",
        cache=cache,
    )
    payload = response.json()
    if not isinstance(payload, list):
        raise GitHubApiError(
            "Expected JSON array in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise GitHubApiError(
                "Expected all array items to be JSON objects in GitHub response.",
                status_code=500,
                endpoint=endpoint,
            )
        rows.append(item)
    return rows


def _request_text(
    client: httpx.Client,
    endpoint: str,
    *,
    accept_header: str,
    cache: GitHubResponseCache | None = None,
) -> str:
    """Perform a text request with explicit Accept header."""
    response = _request_with_retries(
        client,
        endpoint,
        accept_header=accept_header,
        cache=cache,
    )
    return response.text


def parse_head_changed_ranges_from_patch(patch: str) -> tuple[ChangedRange, ...]:
    """Extract head-side changed line spans from a unified diff patch."""
    ranges: list[ChangedRange] = []
    in_hunk = False
    head_line = 0
    range_start: int | None = None
    range_end: int | None = None

    def flush_open_range() -> None:
        nonlocal range_start, range_end
        if range_start is None or range_end is None:
            return
        ranges.append(ChangedRange(line_start=range_start, line_end=range_end))
        range_start = None
        range_end = None

    for line in patch.splitlines():
        header_match = HUNK_HEADER_PATTERN.match(line)
        if header_match is not None:
            flush_open_range()
            in_hunk = True
            head_line = int(header_match.group("head_start"))
            continue

        if not in_hunk:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            if range_start is None:
                range_start = head_line
            range_end = head_line
            head_line += 1
            continue

        if line.startswith(" "):
            flush_open_range()
            head_line += 1
            continue

        if line.startswith("-") and not line.startswith("---"):
            flush_open_range()
            continue

        if line.startswith("\\"):
            continue

        flush_open_range()

    flush_open_range()
    return tuple(ranges)


def _is_retryable_status(status_code: int) -> bool:
    """Return whether a status code is retryable under policy."""
    return status_code == 429 or 500 <= status_code < 600


def _parse_retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse Retry-After header as seconds if present and valid."""
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return None
    try:
        parsed_value = float(retry_after)
    except ValueError:
        return None
    if parsed_value < 0:
        return None
    return parsed_value


def _compute_retry_delay_seconds(response: httpx.Response, *, attempt_number: int) -> float:
    """Compute retry delay from Retry-After header or exponential backoff."""
    retry_after_seconds = _parse_retry_after_seconds(response)
    if retry_after_seconds is not None:
        return retry_after_seconds
    return DEFAULT_RETRY_BACKOFF_SECONDS * (2 ** (attempt_number - 1))


def _sleep_for_retry(seconds: float) -> None:
    """Sleep helper for retry delays (wrapped for deterministic tests)."""
    time.sleep(seconds)


def _raise_http_error(response: httpx.Response, endpoint: str) -> None:
    """Raise a typed error for a non-success GitHub API response."""
    message = f"GitHub API request failed with status {response.status_code} for '{endpoint}'."
    if response.status_code == 429:
        raise GitHubRateLimitError(
            message,
            status_code=response.status_code,
            endpoint=endpoint,
        )
    raise GitHubApiError(
        message,
        status_code=response.status_code,
        endpoint=endpoint,
    )


def _request_with_retries(
    client: httpx.Client,
    endpoint: str,
    *,
    accept_header: str | None = None,
    max_attempts: int = GITHUB_MAX_RETRIES,
    allow_not_found: bool = False,
    cache: GitHubResponseCache | None = None,
) -> httpx.Response:
    """Perform a GET request with retry handling for 429/5xx responses."""
    cache_backend = cache
    cache_entry: CachedHttpResponse | None = None
    cache_key = _cache_key(endpoint, accept_header)
    now = time.time()
    if cache_backend is not None:
        try:
            cache_entry = cache_backend.get(cache_key)
        except sqlite3.Error:
            cache_entry = None

    if (
        cache_entry is not None
        and cache_entry.status_code == 404
        and not allow_not_found
    ):
        cache_entry = None

    if cache_entry is not None and _is_fresh_cache_entry(cache_entry, now=now):
        return _response_from_cache(
            endpoint=endpoint,
            accept_header=accept_header,
            cached=cache_entry,
        )

    headers: dict[str, str] = {}
    if accept_header:
        headers["Accept"] = accept_header
    if cache_entry is not None and not cache_entry.immutable:
        if cache_entry.etag:
            headers["If-None-Match"] = cache_entry.etag
        elif cache_entry.last_modified:
            headers["If-Modified-Since"] = cache_entry.last_modified

    request_headers = headers or None
    for attempt_number in range(1, max_attempts + 1):
        response = client.get(endpoint, headers=request_headers)
        if response.status_code == 304 and cache_entry is not None:
            if cache_backend is not None:
                try:
                    policy = cache_backend.policy_for_endpoint(endpoint)
                    cache_backend.touch(
                        cache_key=cache_key,
                        fetched_at=time.time(),
                        expires_at=_expires_at_for_policy(now=time.time(), policy=policy),
                    )
                except sqlite3.Error:
                    pass
            return _response_from_cache(
                endpoint=endpoint,
                accept_header=accept_header,
                cached=cache_entry,
            )
        if response.status_code < 400:
            if cache_backend is not None:
                try:
                    policy = cache_backend.policy_for_endpoint(endpoint)
                    cache_backend.upsert(
                        cache_key=cache_key,
                        endpoint=endpoint,
                        accept_header=accept_header,
                        status_code=response.status_code,
                        body=response.content,
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                        fetched_at=time.time(),
                        expires_at=_expires_at_for_policy(now=time.time(), policy=policy),
                        immutable=policy.immutable,
                    )
                except sqlite3.Error:
                    pass
            return response
        if allow_not_found and response.status_code == 404:
            if cache_backend is not None:
                try:
                    policy = cache_backend.policy_for_endpoint(endpoint)
                    cache_backend.upsert(
                        cache_key=cache_key,
                        endpoint=endpoint,
                        accept_header=accept_header,
                        status_code=response.status_code,
                        body=response.content,
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                        fetched_at=time.time(),
                        expires_at=_expires_at_for_policy(now=time.time(), policy=policy),
                        immutable=policy.immutable,
                    )
                except sqlite3.Error:
                    pass
            return response

        should_retry = _is_retryable_status(response.status_code) and attempt_number < max_attempts
        if not should_retry:
            _raise_http_error(response, endpoint)

        delay_seconds = _compute_retry_delay_seconds(response, attempt_number=attempt_number)
        _sleep_for_retry(delay_seconds)

    raise RuntimeError("Unexpected retry loop exit without a response.")


def fetch_pull_request_metadata(
    *,
    client: httpx.Client,
    repo_full_name: str,
    pr_number: int,
    cache: GitHubResponseCache | None = None,
) -> PullRequestMeta:
    """Fetch pull request metadata from GitHub."""
    owner, repo = parse_repo_full_name(repo_full_name)
    normalized_pr_number = validate_pr_number(pr_number)
    endpoint = f"/repos/{owner}/{repo}/pulls/{normalized_pr_number}"

    payload = _request_json(client, endpoint, cache=cache)
    user_payload = _require_object(payload, key="user", endpoint=endpoint)
    base_payload = _require_object(payload, key="base", endpoint=endpoint)
    head_payload = _require_object(payload, key="head", endpoint=endpoint)

    body = payload.get("body")
    if body is None:
        body = ""
    elif not isinstance(body, str):
        raise GitHubApiError(
            "Expected 'body' to be a string or null in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )

    return PullRequestMeta(
        number=_require_int(payload, key="number", endpoint=endpoint),
        title=_require_str(payload, key="title", endpoint=endpoint),
        body=body,
        state=_require_str(payload, key="state", endpoint=endpoint),
        draft=_require_bool(payload, key="draft", endpoint=endpoint),
        author_login=_require_str(user_payload, key="login", endpoint=endpoint),
        html_url=_require_str(payload, key="html_url", endpoint=endpoint),
        base_ref=_require_str(base_payload, key="ref", endpoint=endpoint),
        base_sha=_require_str(base_payload, key="sha", endpoint=endpoint),
        head_ref=_require_str(head_payload, key="ref", endpoint=endpoint),
        head_sha=_require_str(head_payload, key="sha", endpoint=endpoint),
    )


def fetch_pull_request_files(
    *,
    client: httpx.Client,
    repo_full_name: str,
    pr_number: int,
    cache: GitHubResponseCache | None = None,
) -> tuple[PullRequestFile, ...]:
    """Fetch all changed files for a pull request with pagination."""
    owner, repo = parse_repo_full_name(repo_full_name)
    normalized_pr_number = validate_pr_number(pr_number)
    base_endpoint = f"/repos/{owner}/{repo}/pulls/{normalized_pr_number}/files"
    per_page = 100

    files: list[PullRequestFile] = []
    page = 1
    while True:
        endpoint = f"{base_endpoint}?per_page={per_page}&page={page}"
        rows = _request_json_list(client, endpoint, cache=cache)
        if not rows:
            break

        for row in rows:
            filename = _require_str(row, key="filename", endpoint=endpoint)
            status = _require_str(row, key="status", endpoint=endpoint)
            patch_value = row.get("patch")
            if patch_value is not None and not isinstance(patch_value, str):
                raise GitHubApiError(
                    "Expected 'patch' to be a string or null in GitHub response.",
                    status_code=500,
                    endpoint=endpoint,
                )

            previous_filename = row.get("previous_filename")
            if previous_filename is not None and not isinstance(previous_filename, str):
                raise GitHubApiError(
                    "Expected 'previous_filename' to be a string or null in GitHub response.",
                    status_code=500,
                    endpoint=endpoint,
                )

            files.append(
                PullRequestFile(
                    path=filename,
                    status=status,
                    additions=_require_int(row, key="additions", endpoint=endpoint),
                    deletions=_require_int(row, key="deletions", endpoint=endpoint),
                    changes=_require_int(row, key="changes", endpoint=endpoint),
                    patch=patch_value,
                    previous_filename=previous_filename,
                    changed_ranges=(
                        parse_head_changed_ranges_from_patch(patch_value)
                        if patch_value is not None
                        else ()
                    ),
                    is_binary=patch_value is None,
                )
            )

        if len(rows) < per_page:
            break
        page += 1

    return tuple(files)


def fetch_pull_request_diff(
    *,
    client: httpx.Client,
    repo_full_name: str,
    pr_number: int,
    cache: GitHubResponseCache | None = None,
) -> str:
    """Fetch full raw diff for a pull request."""
    owner, repo = parse_repo_full_name(repo_full_name)
    normalized_pr_number = validate_pr_number(pr_number)
    endpoint = f"/repos/{owner}/{repo}/pulls/{normalized_pr_number}"
    return _request_text(
        client,
        endpoint,
        accept_header="application/vnd.github.diff",
        cache=cache,
    )


def fetch_pull_request_snapshot(
    *,
    client: httpx.Client,
    repo_full_name: str,
    pr_number: int,
    cache: GitHubResponseCache | None = None,
) -> PullRequestSnapshot:
    """Fetch PR metadata, files, and diff into one normalized snapshot."""
    metadata = fetch_pull_request_metadata(
        client=client,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        cache=cache,
    )
    files = fetch_pull_request_files(
        client=client,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        cache=cache,
    )
    raw_diff = fetch_pull_request_diff(
        client=client,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        cache=cache,
    )

    files_without_patch = [pull_file.path for pull_file in files if pull_file.patch is None]
    warnings: list[str] = []
    if files_without_patch:
        displayed_files = ", ".join(files_without_patch[:5])
        if len(files_without_patch) > 5:
            displayed_files = f"{displayed_files}, ..."
        warnings.append(
            f"{len(files_without_patch)} file(s) missing patch content "
            f"(binary or truncated): {displayed_files}"
        )

    return PullRequestSnapshot(
        repository=repo_full_name,
        metadata=metadata,
        files=files,
        raw_diff=raw_diff,
        warnings=tuple(warnings),
    )


def fetch_file_content_at_ref(
    *,
    client: httpx.Client,
    repo_full_name: str,
    path: str,
    ref: str,
    cache: GitHubResponseCache | None = None,
) -> FileContentAtRef:
    """Fetch one file's content payload at a specific git ref."""
    owner, repo = parse_repo_full_name(repo_full_name)
    normalized_path = path.lstrip("/")
    if not normalized_path:
        raise GitHubInputError("Invalid file path ''. Expected a non-empty repository path.")
    if not ref:
        raise GitHubInputError("Invalid ref ''. Expected a non-empty git ref.")

    endpoint = (
        f"/repos/{owner}/{repo}/contents/{quote(normalized_path, safe='/')}"
        f"?ref={quote(ref, safe='')}"
    )
    response = _request_with_retries(
        client,
        endpoint,
        allow_not_found=True,
        cache=cache,
    )
    if response.status_code == 404:
        return FileContentAtRef(
            ref=ref,
            path=normalized_path,
            exists=False,
            sha=None,
            size_bytes=None,
            text=None,
            is_binary=False,
            encoding=None,
        )

    payload = _ensure_mapping(response.json(), context=endpoint)
    content_type = payload.get("type")
    if content_type is not None and not isinstance(content_type, str):
        raise GitHubApiError(
            "Expected 'type' to be a string or null in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )
    sha = payload.get("sha")
    if sha is not None and not isinstance(sha, str):
        raise GitHubApiError(
            "Expected 'sha' to be a string or null in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )
    size_value = payload.get("size")
    if size_value is not None and (not isinstance(size_value, int) or isinstance(size_value, bool)):
        raise GitHubApiError(
            "Expected 'size' to be an integer or null in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )
    encoding = payload.get("encoding")
    if encoding is not None and not isinstance(encoding, str):
        raise GitHubApiError(
            "Expected 'encoding' to be a string or null in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )
    content = payload.get("content")
    if content is not None and not isinstance(content, str):
        raise GitHubApiError(
            "Expected 'content' to be a string or null in GitHub response.",
            status_code=500,
            endpoint=endpoint,
        )

    if content_type is not None and content_type != "file":
        return FileContentAtRef(
            ref=ref,
            path=normalized_path,
            exists=True,
            sha=sha,
            size_bytes=size_value,
            text=None,
            is_binary=True,
            encoding=encoding,
            warning=f"Unsupported GitHub content type '{content_type}' for '{normalized_path}'.",
        )

    if content is None:
        return FileContentAtRef(
            ref=ref,
            path=normalized_path,
            exists=True,
            sha=sha,
            size_bytes=size_value,
            text=None,
            is_binary=True,
            encoding=encoding,
            warning=f"Missing file content payload for '{normalized_path}' at ref '{ref}'.",
        )

    if encoding == "base64":
        try:
            decoded_bytes = base64.b64decode(content, validate=False)
        except (binascii.Error, ValueError):
            return FileContentAtRef(
                ref=ref,
                path=normalized_path,
                exists=True,
                sha=sha,
                size_bytes=size_value,
                text=None,
                is_binary=True,
                encoding=encoding,
                warning=f"Invalid base64 payload for '{normalized_path}' at ref '{ref}'.",
            )
        try:
            text = decoded_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return FileContentAtRef(
                ref=ref,
                path=normalized_path,
                exists=True,
                sha=sha,
                size_bytes=size_value,
                text=None,
                is_binary=True,
                encoding=encoding,
                warning=f"Non-UTF-8 content for '{normalized_path}' at ref '{ref}'.",
            )
        return FileContentAtRef(
            ref=ref,
            path=normalized_path,
            exists=True,
            sha=sha,
            size_bytes=size_value,
            text=text,
            is_binary=False,
            encoding=encoding,
        )

    if encoding in {"utf-8", "utf8"}:
        return FileContentAtRef(
            ref=ref,
            path=normalized_path,
            exists=True,
            sha=sha,
            size_bytes=size_value,
            text=content,
            is_binary=False,
            encoding=encoding,
        )

    return FileContentAtRef(
        ref=ref,
        path=normalized_path,
        exists=True,
        sha=sha,
        size_bytes=size_value,
        text=None,
        is_binary=True,
        encoding=encoding,
        warning=(
            f"Unsupported content encoding '{encoding}' for '{normalized_path}' at ref '{ref}'."
        ),
    )


def _build_missing_content(*, ref: str, path: str, warning: str) -> FileContentAtRef:
    """Build a placeholder for a file expected to be absent at a ref."""
    return FileContentAtRef(
        ref=ref,
        path=path,
        exists=False,
        sha=None,
        size_bytes=None,
        text=None,
        is_binary=False,
        encoding=None,
        warning=warning,
    )


def resolve_pull_request_file_content(
    *,
    client: httpx.Client,
    repo_full_name: str,
    metadata: PullRequestMeta,
    pull_request_file: PullRequestFile,
    cache: GitHubResponseCache | None = None,
) -> PullRequestFileContent:
    """Resolve base/head contents for one changed file using PR status semantics."""
    status = pull_request_file.status
    path = pull_request_file.path
    warnings: list[str] = []

    if status == "added":
        base_content = _build_missing_content(
            ref=metadata.base_sha,
            path=path,
            warning=f"File '{path}' is added and absent at base ref.",
        )
        head_content = fetch_file_content_at_ref(
            client=client,
            repo_full_name=repo_full_name,
            path=path,
            ref=metadata.head_sha,
            cache=cache,
        )
    elif status == "removed":
        base_content = fetch_file_content_at_ref(
            client=client,
            repo_full_name=repo_full_name,
            path=path,
            ref=metadata.base_sha,
            cache=cache,
        )
        head_content = _build_missing_content(
            ref=metadata.head_sha,
            path=path,
            warning=f"File '{path}' is removed and absent at head ref.",
        )
    elif status == "renamed":
        previous_path = pull_request_file.previous_filename
        if previous_path is None:
            warnings.append(
                f"Renamed file '{path}' missing previous_filename; "
                "using current path for base fetch."
            )
            previous_path = path
        base_content = fetch_file_content_at_ref(
            client=client,
            repo_full_name=repo_full_name,
            path=previous_path,
            ref=metadata.base_sha,
            cache=cache,
        )
        head_content = fetch_file_content_at_ref(
            client=client,
            repo_full_name=repo_full_name,
            path=path,
            ref=metadata.head_sha,
            cache=cache,
        )
    elif status == "modified":
        base_content = fetch_file_content_at_ref(
            client=client,
            repo_full_name=repo_full_name,
            path=path,
            ref=metadata.base_sha,
            cache=cache,
        )
        head_content = fetch_file_content_at_ref(
            client=client,
            repo_full_name=repo_full_name,
            path=path,
            ref=metadata.head_sha,
            cache=cache,
        )
    else:
        warnings.append(
            f"Unknown file status '{status}' for '{path}'; "
            "attempting current path on base/head refs."
        )
        base_content = fetch_file_content_at_ref(
            client=client,
            repo_full_name=repo_full_name,
            path=path,
            ref=metadata.base_sha,
            cache=cache,
        )
        head_content = fetch_file_content_at_ref(
            client=client,
            repo_full_name=repo_full_name,
            path=path,
            ref=metadata.head_sha,
            cache=cache,
        )

    if base_content.warning:
        warnings.append(base_content.warning)
    if head_content.warning:
        warnings.append(head_content.warning)

    return PullRequestFileContent(
        path=path,
        status=status,
        base=base_content,
        head=head_content,
        warnings=tuple(warnings),
    )


def fetch_pull_request_file_contents(
    *,
    client: httpx.Client,
    repo_full_name: str,
    metadata: PullRequestMeta,
    files: tuple[PullRequestFile, ...],
    cache: GitHubResponseCache | None = None,
) -> tuple[tuple[PullRequestFileContent, ...], tuple[str, ...]]:
    """Resolve base/head file contents for all changed files in a pull request."""
    resolved_files: list[PullRequestFileContent] = []
    aggregated_warnings: list[str] = []

    for pull_request_file in files:
        resolved_content = resolve_pull_request_file_content(
            client=client,
            repo_full_name=repo_full_name,
            metadata=metadata,
            pull_request_file=pull_request_file,
            cache=cache,
        )
        resolved_files.append(resolved_content)
        aggregated_warnings.extend(resolved_content.warnings)

    return tuple(resolved_files), tuple(aggregated_warnings)


def parse_repo_full_name(repo_full_name: str) -> tuple[str, str]:
    """Parse and validate repository input in owner/repo format."""
    owner, separator, repo = repo_full_name.strip().partition("/")
    if not separator or not owner or not repo or "/" in repo:
        raise GitHubInputError(
            f"Invalid repo '{repo_full_name}'. Expected format is owner/repo."
        )
    return owner, repo


def validate_pr_number(pr_number: int) -> int:
    """Validate and normalize pull request number input."""
    if pr_number <= 0:
        raise GitHubInputError(f"Invalid PR number '{pr_number}'. Expected a positive integer.")
    return pr_number


def get_github_token() -> str:
    """Read GitHub token from environment and fail fast if missing."""
    token, _source = get_github_token_with_source()
    return token


def get_github_token_with_source() -> tuple[str, str]:
    """Read GitHub token and return token value with environment source key."""
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)

    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        return github_token, "GITHUB_TOKEN"

    gh_token = os.getenv("GH_TOKEN")
    if gh_token:
        return gh_token, "GH_TOKEN"

    message = "Missing GitHub token. Set GITHUB_TOKEN (preferred) or GH_TOKEN."
    raise GitHubAuthError(message)


def fetch_authenticated_user_login(*, client: httpx.Client) -> str:
    """Fetch authenticated GitHub user login for token validation."""
    endpoint = "/user"
    payload = _request_json(client, endpoint)
    return _require_str(payload, key="login", endpoint=endpoint)


def build_github_client(
    timeout_seconds: int = 20,
    *,
    trust_env: bool = True,
) -> httpx.Client:
    """Build an authenticated GitHub HTTP client."""
    token = get_github_token()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    return httpx.Client(
        base_url=GITHUB_API_BASE_URL,
        headers=headers,
        timeout=timeout_seconds,
        trust_env=trust_env,
    )
