"""Serializable benchmark report contracts.

Reports are derived projections over fixture packs, extraction outputs, and
replay artifacts. They are intentionally scorer-neutral so deterministic
candidate scoring and replay scoring can share one persistence shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BenchmarkAggregate",
    "BenchmarkCaseRow",
    "BenchmarkFieldRow",
    "BenchmarkReport",
    "BenchmarkStage",
    "GapKind",
    "MissAttribution",
    "MissAttributionKind",
    "MissAttributionStage",
    "ScorerMetadata",
]


type BenchmarkStage = Literal[
    "candidates",
    "filtered_candidates",
    "selection",
    "normalization",
    "validation",
    "materialization",
    "object_validation",
    "repair",
    "replay_comparability",
]

type GapKind = Literal[
    "missing_expected",
    "unexpected_observed",
    "value_mismatch",
    "span_mismatch",
    "setup_failure",
    "comparability_failure",
]

type MissAttributionStage = Literal[
    "candidates",
    "filtered_candidates",
    "selection",
    "normalization",
    "validation",
    "object_validation",
    "materialization",
    "comparability",
]

type MissAttributionKind = Literal[
    "not_generated",
    "generated_then_filtered",
    "wrong_candidate_selected",
    "selection_abstained",
    "span_near_miss",
    "normalization_mismatch",
    "validation_rejected",
    "object_issue",
    "materialization_missing",
    "fixture_schema_mismatch",
    "fixture_missing_evidence",
    "fixture_grounding_dispute",
    "setup_failure",
]


class MissAttribution(BaseModel):
    """Structured diagnosis for a benchmark miss at an extraction seam."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: MissAttributionStage
    kind: MissAttributionKind
    field_id: str = Field(min_length=1)
    gold_index: int | None = None
    candidate_id: str | None = None
    strategy_id: str | None = None
    filter_node: str | None = None
    reason: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class ScorerMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scorer_name: str = Field(min_length=1)
    scorer_version: str = Field(min_length=1)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    input_refs: tuple[str, ...] = ()
    parameters: dict[str, Any] = Field(default_factory=dict)


class BenchmarkCaseRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    status: Literal["passed", "failed", "setup_failed", "comparability_failed"]
    replay_artifact_ref: str | None = None
    producer_versions: dict[str, str] = Field(default_factory=dict)
    message: str | None = None


class BenchmarkFieldRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    field_id: str = Field(min_length=1)
    stage: BenchmarkStage
    gap_kind: GapKind | None = None
    expected: Any = None
    observed: Any = None
    replay_artifact_ref: str | None = None
    producer_versions: dict[str, str] = Field(default_factory=dict)
    attributions: tuple[MissAttribution, ...] = ()
    message: str | None = None


class BenchmarkAggregate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    count: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: float | None = None


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: ScorerMetadata
    case_rows: tuple[BenchmarkCaseRow, ...] = ()
    field_rows: tuple[BenchmarkFieldRow, ...] = ()
    aggregates: tuple[BenchmarkAggregate, ...] = ()

    @property
    def total_cases(self) -> int:
        return len(self.case_rows)

    @property
    def total_field_rows(self) -> int:
        return len(self.field_rows)
