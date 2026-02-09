"""Schema contract for review and eval outputs."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEDUPE_KEY_PATTERN = re.compile(
    r"^[^:\n]+:[1-9]\d*:(security|bug|error_handling|performance|style|logic):[a-f0-9]{8,64}$"
)
EVIDENCE_HEADER_PATTERN = re.compile(r"^[^:\n]+:L\d+-L\d+$")
REVIEW_ID_PATTERN = re.compile(r"^[a-f0-9]{16}$")


class Severity(StrEnum):
    """Supported issue severities."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(StrEnum):
    """Supported issue categories."""

    SECURITY = "security"
    BUG = "bug"
    ERROR_HANDLING = "error_handling"
    PERFORMANCE = "performance"
    STYLE = "style"
    LOGIC = "logic"


class Language(StrEnum):
    """Supported languages."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"


class ReviewStatus(StrEnum):
    """Review execution status."""

    OK = "ok"
    TRUNCATED = "truncated"
    ERROR = "error"


class Issue(BaseModel):
    """Issue found during a review."""

    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int | None = Field(default=None, ge=1)
    severity: Severity
    category: Category
    description: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    evidence_snippet: str = Field(min_length=1)
    dedupe_key: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    language: Language

    @field_validator("dedupe_key")
    @classmethod
    def validate_dedupe_key(cls, value: str) -> str:
        """Validate the dedupe key contract."""
        if not DEDUPE_KEY_PATTERN.fullmatch(value):
            raise ValueError(
                "dedupe_key must match "
                "{file_path}:{line_start}:{category}:{hash}, with a lowercase hex hash."
            )
        return value

    @field_validator("evidence_snippet")
    @classmethod
    def validate_evidence_snippet(cls, value: str) -> str:
        """Validate evidence format: header plus supporting body lines."""
        header, separator, body = value.partition("\n")
        if not separator or not body.strip():
            raise ValueError(
                "evidence_snippet must include a header line and at least one body line."
            )
        if not EVIDENCE_HEADER_PATTERN.fullmatch(header.strip()):
            raise ValueError("evidence_snippet header must match path:Lx-Ly format.")
        return value

    @model_validator(mode="after")
    def validate_line_range(self) -> Issue:
        """Validate that line_end is not smaller than line_start."""
        if self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        return self


class ReviewStats(BaseModel):
    """Rollup metrics for one review run."""

    model_config = ConfigDict(extra="forbid")

    tokens_used: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_seconds_e2e: float = Field(default=0.0, ge=0.0)
    latency_seconds_llm: float = Field(default=0.0, ge=0.0)
    llm_calls: int = Field(default=0, ge=0)


class ReviewResult(BaseModel):
    """Structured review output contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="v1", pattern=r"^v\d+$")
    review_id: str = Field(min_length=16, max_length=16)
    status: ReviewStatus
    model_used: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    summary: str = Field(default="")
    files_reviewed: list[str] = Field(default_factory=list)
    stats: ReviewStats = Field(default_factory=ReviewStats)

    @field_validator("review_id")
    @classmethod
    def validate_review_id(cls, value: str) -> str:
        """Validate review ID format derived from sha256 prefix."""
        if not REVIEW_ID_PATTERN.fullmatch(value):
            raise ValueError("review_id must be a 16-character lowercase hex string.")
        return value


class EvalResult(BaseModel):
    """Aggregated metrics for eval runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="v1", pattern=r"^v\d+$")
    recall: float = Field(ge=0.0, le=1.0)
    precision: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)
    avg_confidence_calibration: float = Field(ge=0.0, le=1.0)
    cost_usd: float = Field(ge=0.0)
    latency_seconds: float = Field(ge=0.0)
