"""Typer CLI for the code review agent."""

from __future__ import annotations

from typing import Annotated

import httpx
import typer

from src.github_client import (
    GitHubApiError,
    GitHubAuthError,
    build_github_client,
    fetch_authenticated_user_login,
    fetch_pull_request_file_contents,
    fetch_pull_request_files,
    fetch_pull_request_metadata,
    get_github_token_with_source,
)

app = typer.Typer(help="Eval-first GitHub pull request code review agent.")


@app.command("review")
def review_command(
    repo: Annotated[str, typer.Option(help="Repository in owner/repo format.")],
    pr: Annotated[int, typer.Option(help="Pull request number.")],
    dry_run: Annotated[bool, typer.Option(help="Skip writing GitHub PR comment.")] = False,
    budget: Annotated[float, typer.Option(help="Maximum budget in USD for this run.")] = 0.50,
    max_issues: Annotated[int, typer.Option(help="Maximum number of output issues.")] = 15,
    model: Annotated[str | None, typer.Option(help="Override reviewer model.")] = None,
    output_format: Annotated[
        str, typer.Option(help="Local artifact format: md|json|both.")
    ] = "both",
    verbose: Annotated[bool, typer.Option(help="Print progress and warnings.")] = False,
    debug: Annotated[bool, typer.Option(help="Enable raw prompt/response logging.")] = False,
) -> None:
    """Run a PR review (placeholder command)."""
    typer.echo(
        "Scaffold complete: review command wiring exists but runtime flow is not implemented yet."
    )
    typer.echo(
        f"repo={repo} pr={pr} dry_run={dry_run} budget={budget} max_issues={max_issues} "
        f"model={model} output_format={output_format} verbose={verbose} debug={debug}"
    )


@app.command("eval")
def eval_command(
    cases: Annotated[str, typer.Option(help="Case selector: all|python|js|ts.")] = "all",
    prompt: Annotated[str, typer.Option(help="Prompt version: best|<prompt_version>.")] = "best",
    model: Annotated[str | None, typer.Option(help="Override evaluator model.")] = None,
    budget: Annotated[float, typer.Option(help="Maximum budget in USD for eval run.")] = 5.00,
    verbose: Annotated[bool, typer.Option(help="Print per-case metrics and failures.")] = False,
) -> None:
    """Run eval suite (placeholder command)."""
    typer.echo(
        "Scaffold complete: eval command wiring exists but eval runner is not implemented yet."
    )
    typer.echo(f"cases={cases} prompt={prompt} model={model} budget={budget} verbose={verbose}")


@app.command("auth-check")
def auth_check_command(
    repo: Annotated[
        str | None,
        typer.Option(help="Optional repository in owner/repo format for permission check."),
    ] = None,
    pr: Annotated[
        int | None,
        typer.Option(help="Optional pull request number used with --repo for permission check."),
    ] = None,
    timeout_seconds: Annotated[
        int, typer.Option(help="GitHub API timeout in seconds for the validation call.")
    ] = 20,
    trust_env: Annotated[
        bool,
        typer.Option(
            "--trust-env/--no-trust-env",
            help="Use proxy/SSL environment variables from the current shell.",
        ),
    ] = True,
) -> None:
    """Validate GitHub token setup and optional PR read access."""
    if (repo is None) != (pr is None):
        raise typer.BadParameter("Provide both --repo and --pr together, or neither.")

    try:
        _token, token_source = get_github_token_with_source()
    except GitHubAuthError as error:
        typer.echo(f"GitHub auth check failed: {error}")
        raise typer.Exit(code=1) from error

    typer.echo(f"Token detected in {token_source}.")

    try:
        with build_github_client(timeout_seconds=timeout_seconds, trust_env=trust_env) as client:
            login = fetch_authenticated_user_login(client=client)
            typer.echo(f"Authenticated as GitHub user '{login}'.")

            if repo is not None and pr is not None:
                metadata = fetch_pull_request_metadata(
                    client=client,
                    repo_full_name=repo,
                    pr_number=pr,
                )
                files = fetch_pull_request_files(
                    client=client,
                    repo_full_name=repo,
                    pr_number=pr,
                )
                if files:
                    fetch_pull_request_file_contents(
                        client=client,
                        repo_full_name=repo,
                        metadata=metadata,
                        files=(files[0],),
                    )
                typer.echo(f"Repository/PR access check passed for {repo}#{pr}.")
    except GitHubApiError as error:
        typer.echo(
            "GitHub auth check failed: "
            f"status={error.status_code} endpoint={error.endpoint}."
        )
        raise typer.Exit(code=1) from error
    except httpx.HTTPError as error:
        typer.echo(f"GitHub auth check failed: network error ({error}).")
        raise typer.Exit(code=1) from error
    except ImportError as error:
        typer.echo(
            "GitHub auth check failed: proxy transport dependency is missing. "
            "Try `code-review-agent auth-check --no-trust-env`, or install `httpx[socks]`."
        )
        raise typer.Exit(code=1) from error

    typer.echo("GitHub token setup is valid.")
