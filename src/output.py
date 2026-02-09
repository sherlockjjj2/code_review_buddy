"""GitHub comment and local report rendering."""

from __future__ import annotations

from src.schema import ReviewResult

COMMENT_MARKER_PREFIX = "<!-- code-review-agent:review_id="


def render_markdown_report(result: ReviewResult) -> str:
    """Render a minimal markdown report from a review result."""
    lines = [f"# Review {result.review_id}", "", f"Status: `{result.status}`", ""]
    lines.append("## Summary")
    lines.append(result.summary or "No summary provided.")
    lines.append("")
    lines.append("## Issues")
    if not result.issues:
        lines.append("- No issues found.")
        return "\n".join(lines)

    for issue in result.issues:
        location = f"{issue.file}:{issue.line_start}"
        lines.append(
            f"- **{issue.severity} / {issue.category}** at `{location}`: {issue.description}"
        )
    return "\n".join(lines)
