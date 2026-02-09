"""Schema contract roundtrip tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from src.schema import (
    Category,
    EvalResult,
    Issue,
    Language,
    ReviewResult,
    ReviewStatus,
    Severity,
)


def make_valid_issue() -> Issue:
    """Create a valid issue for schema tests."""
    return Issue(
        file="src/example.py",
        line_start=10,
        line_end=12,
        severity=Severity.HIGH,
        category=Category.BUG,
        description="Potential null dereference.",
        suggestion="Guard against None before access.",
        evidence_snippet="src/example.py:L10-L12\nobj = maybe_none.value",
        dedupe_key="src/example.py:10:bug:abcd1234",
        confidence=0.81,
        language=Language.PYTHON,
    )


@pytest.mark.unit
def test_issue_json_roundtrip() -> None:
    original = make_valid_issue()
    payload = original.model_dump_json()
    restored = Issue.model_validate_json(payload)
    assert restored == original


@pytest.mark.unit
def test_review_result_json_roundtrip() -> None:
    issue = make_valid_issue()
    original = ReviewResult(
        review_id="1a2b3c4d5e6f7a8b",
        status=ReviewStatus.OK,
        model_used="gpt-4.1-mini",
        warnings=[],
        issues=[issue],
        summary="One issue found.",
        files_reviewed=["src/example.py"],
    )

    payload = original.model_dump_json()
    restored = ReviewResult.model_validate_json(payload)

    assert restored == original


@pytest.mark.unit
def test_eval_result_json_roundtrip() -> None:
    original = EvalResult(
        recall=0.8,
        precision=0.9,
        f1=0.85,
        avg_confidence_calibration=0.75,
        cost_usd=0.12,
        latency_seconds=21.7,
    )
    payload = original.model_dump_json()
    restored = EvalResult.model_validate_json(payload)
    assert restored == original


@pytest.mark.unit
def test_issue_rejects_invalid_line_range() -> None:
    with pytest.raises(ValidationError):
        Issue.model_validate({**make_valid_issue().model_dump(), "line_start": 20, "line_end": 19})


@pytest.mark.unit
def test_issue_rejects_invalid_dedupe_key() -> None:
    with pytest.raises(ValidationError):
        Issue.model_validate({**make_valid_issue().model_dump(), "dedupe_key": "invalid-key"})


@pytest.mark.unit
def test_issue_rejects_invalid_evidence_format() -> None:
    with pytest.raises(ValidationError):
        Issue.model_validate(
            {**make_valid_issue().model_dump(), "evidence_snippet": "src/example.py:L10-L12"}
        )


@pytest.mark.unit
def test_issue_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        Issue.model_validate({**make_valid_issue().model_dump(), "confidence": 1.5})


@pytest.mark.unit
def test_issue_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Issue.model_validate({**make_valid_issue().model_dump(), "unexpected": "value"})


@pytest.mark.unit
def test_review_result_rejects_invalid_review_id() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(
            review_id="short",
            status=ReviewStatus.OK,
            model_used="gpt-4.1-mini",
            issues=[make_valid_issue()],
            summary="Summary",
            files_reviewed=["src/example.py"],
        )


@pytest.mark.unit
def test_review_result_rejects_invalid_schema_version() -> None:
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(
            {
                "schema_version": "version-one",
                "review_id": "1a2b3c4d5e6f7a8b",
                "status": "ok",
                "model_used": "gpt-4.1-mini",
                "issues": [make_valid_issue().model_dump()],
                "summary": "Summary",
                "files_reviewed": ["src/example.py"],
                "stats": {},
            }
        )


@pytest.mark.unit
def test_eval_result_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        EvalResult.model_validate(
            {
                "precision": 0.9,
                "f1": 0.85,
                "avg_confidence_calibration": 0.75,
                "cost_usd": 0.12,
                "latency_seconds": 21.7,
            }
        )
