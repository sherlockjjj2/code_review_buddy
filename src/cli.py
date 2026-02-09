"""Typer CLI for the code review agent."""

from __future__ import annotations

from typing import Annotated

import typer

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
