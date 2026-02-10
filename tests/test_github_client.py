"""Unit tests for GitHub client behavior."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from src.github_client import (
    GitHubApiError,
    GitHubAuthError,
    GitHubInputError,
    GitHubRateLimitError,
    GitHubResponseCache,
    PullRequestFile,
    PullRequestMeta,
    fetch_authenticated_user_login,
    fetch_file_content_at_ref,
    fetch_pull_request_diff,
    fetch_pull_request_file_contents,
    fetch_pull_request_files,
    fetch_pull_request_metadata,
    fetch_pull_request_snapshot,
    get_github_token,
    get_github_token_with_source,
    parse_head_changed_ranges_from_patch,
    parse_repo_full_name,
    resolve_pull_request_file_content,
    validate_pr_number,
)


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Create an HTTP client backed by mock transport."""
    transport = httpx.MockTransport(handler)
    return httpx.Client(base_url="https://api.github.com", transport=transport)


def make_pr_payload() -> dict[str, object]:
    """Build a minimal valid pull request API payload."""
    return {
        "number": 42,
        "title": "Fix race condition",
        "body": "Details",
        "state": "open",
        "draft": False,
        "html_url": "https://github.com/acme/rocket/pull/42",
        "user": {"login": "octocat"},
        "base": {"ref": "main", "sha": "base-sha"},
        "head": {"ref": "feature/race-fix", "sha": "head-sha"},
    }


def make_file_row(
    *,
    filename: str,
    patch: str | None = "@@ -1,1 +1,1 @@\n-old\n+new",
) -> dict[str, object]:
    """Build a minimal valid pull request file payload."""
    return {
        "filename": filename,
        "status": "modified",
        "additions": 1,
        "deletions": 1,
        "changes": 2,
        "patch": patch,
        "previous_filename": None,
    }


def make_contents_payload(
    *,
    content_bytes: bytes,
    path: str,
    sha: str,
    content_type: str = "file",
) -> dict[str, object]:
    """Build a minimal valid repository contents API payload."""
    encoded_content = base64.b64encode(content_bytes).decode("ascii")
    return {
        "type": content_type,
        "encoding": "base64",
        "content": encoded_content,
        "sha": sha,
        "size": len(content_bytes),
        "path": path,
    }


def make_metadata() -> PullRequestMeta:
    """Build a metadata object for file-content resolution tests."""
    return PullRequestMeta(
        number=42,
        title="Fix race condition",
        body="Details",
        state="open",
        draft=False,
        author_login="octocat",
        html_url="https://github.com/acme/rocket/pull/42",
        base_ref="main",
        base_sha="base-sha",
        head_ref="feature/race-fix",
        head_sha="head-sha",
    )


@pytest.mark.unit
def test_parse_repo_full_name_accepts_owner_repo() -> None:
    owner, repo = parse_repo_full_name("acme/rocket")
    assert owner == "acme"
    assert repo == "rocket"


@pytest.mark.unit
def test_parse_repo_full_name_rejects_invalid_format() -> None:
    with pytest.raises(GitHubInputError):
        parse_repo_full_name("acme")


@pytest.mark.unit
def test_validate_pr_number_rejects_non_positive() -> None:
    with pytest.raises(GitHubInputError):
        validate_pr_number(0)


@pytest.mark.unit
def test_parse_head_changed_ranges_from_patch_returns_spans() -> None:
    patch = "\n".join(
        [
            "@@ -10,2 +10,3 @@",
            " line1",
            "-line2",
            "+line2a",
            "+line2b",
            " line3",
            "@@ -30,1 +31,2 @@",
            "-old",
            "+new1",
            "+new2",
        ]
    )
    spans = parse_head_changed_ranges_from_patch(patch)
    assert [(span.line_start, span.line_end) for span in spans] == [(11, 12), (31, 32)]


@pytest.mark.unit
def test_parse_head_changed_ranges_from_patch_handles_deletion_only_hunk() -> None:
    patch = "\n".join(["@@ -8,2 +8,0 @@", "-old1", "-old2"])
    spans = parse_head_changed_ranges_from_patch(patch)
    assert spans == ()


@pytest.mark.unit
def test_fetch_pull_request_metadata_parses_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/rocket/pulls/42"
        return httpx.Response(status_code=200, json=make_pr_payload())

    with make_client(handler) as client:
        metadata = fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )

    assert metadata.number == 42
    assert metadata.title == "Fix race condition"
    assert metadata.author_login == "octocat"
    assert metadata.base_sha == "base-sha"
    assert metadata.head_sha == "head-sha"


@pytest.mark.unit
def test_fetch_pull_request_metadata_rejects_invalid_shape() -> None:
    payload = make_pr_payload()
    payload["title"] = 100

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json=payload)

    with make_client(handler) as client, pytest.raises(GitHubApiError):
        fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )


@pytest.mark.unit
def test_fetch_pull_request_files_paginates_until_last_page() -> None:
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/repos/acme/rocket/pulls/42/files":
            raise AssertionError("Unexpected endpoint")

        page = request.url.params.get("page")
        requested_pages.append(page or "")
        if page == "1":
            rows = [make_file_row(filename=f"src/file_{i}.py") for i in range(100)]
            return httpx.Response(status_code=200, json=rows)
        if page == "2":
            return httpx.Response(status_code=200, json=[make_file_row(filename="src/final.py")])
        raise AssertionError("Unexpected page")

    with make_client(handler) as client:
        files = fetch_pull_request_files(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )

    assert requested_pages == ["1", "2"]
    assert len(files) == 101
    assert files[-1].path == "src/final.py"


@pytest.mark.unit
def test_fetch_pull_request_files_sets_changed_ranges_from_patch() -> None:
    patch = "\n".join(["@@ -5,1 +5,2 @@", "-old", "+new_a", "+new_b"])

    def handler(request: httpx.Request) -> httpx.Response:
        rows = [make_file_row(filename="src/a.py", patch=patch)]
        return httpx.Response(status_code=200, json=rows)

    with make_client(handler) as client:
        files = fetch_pull_request_files(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )

    assert len(files) == 1
    assert [(span.line_start, span.line_end) for span in files[0].changed_ranges] == [(5, 6)]


@pytest.mark.unit
def test_fetch_pull_request_diff_uses_diff_accept_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "application/vnd.github.diff"
        return httpx.Response(status_code=200, text="diff --git a/a.py b/a.py")

    with make_client(handler) as client:
        raw_diff = fetch_pull_request_diff(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )

    assert raw_diff.startswith("diff --git")


@pytest.mark.unit
def test_retry_honors_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_durations: list[float] = []
    monkeypatch.setattr("src.github_client._sleep_for_retry", sleep_durations.append)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(status_code=429, headers={"Retry-After": "3"})
        return httpx.Response(status_code=200, json=make_pr_payload())

    with make_client(handler) as client:
        metadata = fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )

    assert metadata.number == 42
    assert attempts["count"] == 2
    assert sleep_durations == [3.0]


@pytest.mark.unit
def test_retry_uses_exponential_backoff_for_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_durations: list[float] = []
    monkeypatch.setattr("src.github_client._sleep_for_retry", sleep_durations.append)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] in {1, 2}:
            return httpx.Response(status_code=502)
        return httpx.Response(status_code=200, json=make_pr_payload())

    with make_client(handler) as client:
        metadata = fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )

    assert metadata.number == 42
    assert attempts["count"] == 3
    assert sleep_durations == [0.5, 1.0]


@pytest.mark.unit
def test_non_retryable_404_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_durations: list[float] = []
    monkeypatch.setattr("src.github_client._sleep_for_retry", sleep_durations.append)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(status_code=404)

    with make_client(handler) as client, pytest.raises(GitHubApiError) as exc_info:
        fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )

    assert exc_info.value.status_code == 404
    assert attempts["count"] == 1
    assert sleep_durations == []


@pytest.mark.unit
def test_rate_limit_error_after_retry_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_durations: list[float] = []
    monkeypatch.setattr("src.github_client._sleep_for_retry", sleep_durations.append)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(status_code=429)

    with make_client(handler) as client, pytest.raises(GitHubRateLimitError):
        fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )

    assert attempts["count"] == 3
    assert sleep_durations == [0.5, 1.0]


@pytest.mark.unit
def test_fetch_pull_request_snapshot_aggregates_and_warns_for_missing_patch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/rocket/pulls/42/files":
            rows = [
                make_file_row(filename="src/has_patch.py", patch="@@ -1 +1 @@\n-old\n+new"),
                make_file_row(filename="assets/logo.png", patch=None),
            ]
            return httpx.Response(status_code=200, json=rows)

        if request.url.path == "/repos/acme/rocket/pulls/42":
            if request.headers["Accept"] == "application/vnd.github.diff":
                return httpx.Response(status_code=200, text="diff --git a/a.py b/a.py")
            return httpx.Response(status_code=200, json=make_pr_payload())

        raise AssertionError("Unexpected endpoint")

    with make_client(handler) as client:
        snapshot = fetch_pull_request_snapshot(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
        )

    assert snapshot.repository == "acme/rocket"
    assert snapshot.metadata.number == 42
    assert len(snapshot.files) == 2
    assert snapshot.raw_diff.startswith("diff --git")
    assert len(snapshot.warnings) == 1
    assert "assets/logo.png" in snapshot.warnings[0]


@pytest.mark.unit
def test_get_github_token_with_source_prefers_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")

    token, source = get_github_token_with_source()

    assert token == "github-token"
    assert source == "GITHUB_TOKEN"


@pytest.mark.unit
def test_get_github_token_loads_from_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    (tmp_path / ".env").write_text("GITHUB_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert get_github_token() == "dotenv-token"


@pytest.mark.unit
def test_get_github_token_with_source_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(GitHubAuthError):
        get_github_token_with_source()


@pytest.mark.unit
def test_fetch_authenticated_user_login_returns_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user"
        return httpx.Response(status_code=200, json={"login": "octocat"})

    with make_client(handler) as client:
        login = fetch_authenticated_user_login(client=client)

    assert login == "octocat"


@pytest.mark.unit
def test_fetch_file_content_at_ref_decodes_utf8_text_payload() -> None:
    expected_text = "print('hello')\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/rocket/contents/src/main.py"
        assert request.url.params["ref"] == "head-sha"
        payload = make_contents_payload(
            content_bytes=expected_text.encode("utf-8"),
            path="src/main.py",
            sha="sha-head",
        )
        return httpx.Response(status_code=200, json=payload)

    with make_client(handler) as client:
        content = fetch_file_content_at_ref(
            client=client,
            repo_full_name="acme/rocket",
            path="src/main.py",
            ref="head-sha",
        )

    assert content.exists is True
    assert content.is_binary is False
    assert content.text == expected_text
    assert content.warning is None


@pytest.mark.unit
def test_fetch_file_content_at_ref_returns_exists_false_for_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=404)

    with make_client(handler) as client:
        content = fetch_file_content_at_ref(
            client=client,
            repo_full_name="acme/rocket",
            path="src/missing.py",
            ref="base-sha",
        )

    assert content.exists is False
    assert content.text is None
    assert content.warning is None


@pytest.mark.unit
def test_fetch_file_content_at_ref_marks_non_utf8_as_binary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = make_contents_payload(
            content_bytes=b"\xff\xfe\xfd",
            path="assets/logo.bin",
            sha="sha-bin",
        )
        return httpx.Response(status_code=200, json=payload)

    with make_client(handler) as client:
        content = fetch_file_content_at_ref(
            client=client,
            repo_full_name="acme/rocket",
            path="assets/logo.bin",
            ref="head-sha",
        )

    assert content.exists is True
    assert content.is_binary is True
    assert content.text is None
    assert content.warning is not None
    assert "Non-UTF-8 content" in content.warning


@pytest.mark.unit
def test_resolve_pull_request_file_content_added_fetches_head_only() -> None:
    requested_refs: list[str] = []
    pull_request_file = PullRequestFile(
        path="src/new.py",
        status="added",
        additions=3,
        deletions=0,
        changes=3,
        patch="@@ -0,0 +1,3 @@\n+one\n+two\n+three",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requested_refs.append(request.url.params["ref"])
        payload = make_contents_payload(
            content_bytes=b"print('new')\n",
            path="src/new.py",
            sha="sha-new",
        )
        return httpx.Response(status_code=200, json=payload)

    with make_client(handler) as client:
        resolved = resolve_pull_request_file_content(
            client=client,
            repo_full_name="acme/rocket",
            metadata=make_metadata(),
            pull_request_file=pull_request_file,
        )

    assert requested_refs == ["head-sha"]
    assert resolved.base.exists is False
    assert resolved.head.exists is True
    assert resolved.head.text == "print('new')\n"
    assert any("added and absent at base" in warning for warning in resolved.warnings)


@pytest.mark.unit
def test_resolve_pull_request_file_content_renamed_uses_previous_filename_for_base() -> None:
    requested_paths: list[tuple[str, str]] = []
    pull_request_file = PullRequestFile(
        path="src/new_name.py",
        status="renamed",
        additions=1,
        deletions=1,
        changes=2,
        patch="@@ -1,1 +1,1 @@\n-old\n+new",
        previous_filename="src/old_name.py",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append((request.url.path, request.url.params["ref"]))
        if request.url.path.endswith("/src/old_name.py"):
            payload = make_contents_payload(
                content_bytes=b"old_value = 1\n",
                path="src/old_name.py",
                sha="sha-old",
            )
            return httpx.Response(status_code=200, json=payload)
        if request.url.path.endswith("/src/new_name.py"):
            payload = make_contents_payload(
                content_bytes=b"new_value = 1\n",
                path="src/new_name.py",
                sha="sha-new",
            )
            return httpx.Response(status_code=200, json=payload)
        raise AssertionError("Unexpected path")

    with make_client(handler) as client:
        resolved = resolve_pull_request_file_content(
            client=client,
            repo_full_name="acme/rocket",
            metadata=make_metadata(),
            pull_request_file=pull_request_file,
        )

    assert requested_paths == [
        ("/repos/acme/rocket/contents/src/old_name.py", "base-sha"),
        ("/repos/acme/rocket/contents/src/new_name.py", "head-sha"),
    ]
    assert resolved.base.path == "src/old_name.py"
    assert resolved.head.path == "src/new_name.py"
    assert resolved.warnings == ()


@pytest.mark.unit
def test_resolve_pull_request_file_content_unknown_status_falls_back_to_current_path() -> None:
    requested_refs: list[str] = []
    pull_request_file = PullRequestFile(
        path="src/copied.py",
        status="copied",
        additions=1,
        deletions=0,
        changes=1,
        patch="@@ -0,0 +1,1 @@\n+copied = True",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requested_refs.append(request.url.params["ref"])
        payload = make_contents_payload(
            content_bytes=b"copied = True\n",
            path="src/copied.py",
            sha="sha-copy",
        )
        return httpx.Response(status_code=200, json=payload)

    with make_client(handler) as client:
        resolved = resolve_pull_request_file_content(
            client=client,
            repo_full_name="acme/rocket",
            metadata=make_metadata(),
            pull_request_file=pull_request_file,
        )

    assert requested_refs == ["base-sha", "head-sha"]
    assert resolved.base.exists is True
    assert resolved.head.exists is True
    assert any("Unknown file status 'copied'" in warning for warning in resolved.warnings)


@pytest.mark.unit
def test_fetch_pull_request_file_contents_aggregates_warnings() -> None:
    files = (
        PullRequestFile(
            path="src/new.py",
            status="added",
            additions=2,
            deletions=0,
            changes=2,
            patch="@@ -0,0 +1,2 @@\n+one\n+two",
        ),
        PullRequestFile(
            path="src/normal.py",
            status="modified",
            additions=1,
            deletions=1,
            changes=2,
            patch="@@ -1,1 +1,1 @@\n-old\n+new",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = make_contents_payload(
            content_bytes=b"value = 1\n",
            path=request.url.path.split("/contents/", maxsplit=1)[-1],
            sha="sha-generic",
        )
        return httpx.Response(status_code=200, json=payload)

    with make_client(handler) as client:
        resolved_files, warnings = fetch_pull_request_file_contents(
            client=client,
            repo_full_name="acme/rocket",
            metadata=make_metadata(),
            files=files,
        )

    assert len(resolved_files) == 2
    assert len(warnings) >= 1
    assert any("added and absent at base" in warning for warning in warnings)


@pytest.mark.unit
def test_metadata_fetch_uses_cache_on_second_call(tmp_path: Path) -> None:
    cache = GitHubResponseCache(tmp_path / "github_cache.sqlite")
    call_count = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        return httpx.Response(status_code=200, json=make_pr_payload(), headers={"ETag": "meta-v1"})

    with make_client(handler) as client:
        first = fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
            cache=cache,
        )
        second = fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
            cache=cache,
        )

    assert call_count["count"] == 1
    assert first == second


@pytest.mark.unit
def test_metadata_fetch_revalidates_with_etag_when_stale(tmp_path: Path) -> None:
    cache = GitHubResponseCache(tmp_path / "github_cache.sqlite", mutable_ttl_seconds=0.0)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(
                status_code=200,
                json=make_pr_payload(),
                headers={"ETag": "meta-v1"},
            )
        assert request.headers.get("If-None-Match") == "meta-v1"
        return httpx.Response(status_code=304)

    with make_client(handler) as client:
        first = fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
            cache=cache,
        )
        second = fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
            cache=cache,
        )

    assert attempts["count"] == 2
    assert first == second


@pytest.mark.unit
def test_cache_key_separates_json_and_diff_variants(tmp_path: Path) -> None:
    cache = GitHubResponseCache(tmp_path / "github_cache.sqlite")
    calls_by_accept: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        accept = request.headers.get("Accept", "")
        calls_by_accept[accept] = calls_by_accept.get(accept, 0) + 1
        if accept == "application/vnd.github.diff":
            return httpx.Response(status_code=200, text="diff --git a/a.py b/a.py")
        return httpx.Response(status_code=200, json=make_pr_payload(), headers={"ETag": "meta-v1"})

    with make_client(handler) as client:
        fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
            cache=cache,
        )
        fetch_pull_request_diff(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
            cache=cache,
        )
        fetch_pull_request_metadata(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
            cache=cache,
        )
        fetch_pull_request_diff(
            client=client,
            repo_full_name="acme/rocket",
            pr_number=42,
            cache=cache,
        )

    assert calls_by_accept["application/vnd.github+json"] == 1
    assert calls_by_accept["application/vnd.github.diff"] == 1


@pytest.mark.unit
def test_file_content_404_is_cached_for_commit_ref(tmp_path: Path) -> None:
    cache = GitHubResponseCache(tmp_path / "github_cache.sqlite")
    call_count = {"count": 0}
    commit_sha = "a" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        assert request.url.params["ref"] == commit_sha
        return httpx.Response(status_code=404)

    with make_client(handler) as client:
        first = fetch_file_content_at_ref(
            client=client,
            repo_full_name="acme/rocket",
            path="src/missing.py",
            ref=commit_sha,
            cache=cache,
        )
        second = fetch_file_content_at_ref(
            client=client,
            repo_full_name="acme/rocket",
            path="src/missing.py",
            ref=commit_sha,
            cache=cache,
        )

    assert call_count["count"] == 1
    assert first.exists is False
    assert second.exists is False
