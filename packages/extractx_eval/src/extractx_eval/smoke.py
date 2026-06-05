"""Live replay-backed smoke surface for extractx.

Smoke runs are not benchmarks. They execute the production `extract(...)` path,
require replay, and leave deterministic diagnosis to replay/value-check
projections.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from extractx import extract
from extractx.core.outcomes import Extraction, UsageEvent
from extractx.replay import read_replay, reconstruct_extraction
from extractx.storage.protocol import ExtractxStore
from pydantic import BaseModel, ConfigDict

from .scoring import (
    VALUE_DIFF_KINDS,
    ExpectedInstance,
    ValueDiff,
    ValueDiffKind,
    score_instances,
)

type ExtractionOutcome = Literal["complete", "partial", "failed"]
type SmokeRunStatus = Literal["completed", "completed_with_outcome", "errored"]
type ValueCheckStatus = Literal["matched", "mismatched", "not_checked"]
type SmokeErrorKind = Literal["setup", "schema", "runtime", "provider", "timeout"]


@dataclass(frozen=True)
class SmokeCase:
    case_id: str
    document: str | bytes
    schema: type[BaseModel]
    store: ExtractxStore
    expected_instances: tuple[ExpectedInstance, ...] = ()

    def __post_init__(self) -> None:
        if self.case_id == "":
            raise ValueError("SmokeCase.case_id must be non-empty")


class ErrorInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SmokeErrorKind
    message: str


class SmokeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    run_status: SmokeRunStatus
    extraction_outcome: ExtractionOutcome | None
    replay_artifact_ref: str | None
    producer_versions: Mapping[str, str]
    usage_events: tuple[UsageEvent, ...] = ()
    error: ErrorInfo | None = None


class ValueCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    status: ValueCheckStatus
    replay_artifact_ref: str | None
    misses: tuple[ValueDiff, ...] = ()
    error: ErrorInfo | None = None


class SmokeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    smoke_results: tuple[SmokeResult, ...]
    value_checks: tuple[ValueCheckResult, ...] = ()
    counts_by_miss_kind: Mapping[ValueDiffKind, int]
    total_cases: int
    total_errors: int
    total_value_mismatches: int


async def smoke_run(case: SmokeCase) -> SmokeResult:
    """Run production extraction once and require a readable replay artifact."""

    try:
        result = await extract(case.document, case.schema, store=case.store)
    except Exception as exc:  # noqa: BLE001 - smoke captures production-path failure.
        return SmokeResult(
            case_id=case.case_id,
            run_status="errored",
            extraction_outcome=None,
            replay_artifact_ref=None,
            producer_versions={},
            usage_events=(),
            error=ErrorInfo(kind="runtime", message=f"{type(exc).__name__}: {exc!s}"),
        )

    if result.replay_artifact_ref == "":
        return _errored_result(
            case,
            result,
            ErrorInfo(
                kind="setup",
                message="smoke.setup: extract(...) returned an empty replay_artifact_ref",
            ),
        )

    try:
        artifact = read_replay(case.store, result.replay_artifact_ref)
    except Exception as exc:  # noqa: BLE001 - setup failure, not benchmark miss.
        return _errored_result(
            case,
            result,
            ErrorInfo(
                kind="setup",
                message=f"smoke.setup: replay artifact could not be read: {exc!s}",
            ),
        )

    return SmokeResult(
        case_id=case.case_id,
        run_status="completed" if result.outcome == "complete" else "completed_with_outcome",
        extraction_outcome=result.outcome,
        replay_artifact_ref=result.replay_artifact_ref,
        producer_versions=artifact.producer_versions,
        usage_events=result.usage_events,
        error=None,
    )


async def smoke_run_many(cases: Iterable[SmokeCase]) -> SmokeReport:
    smoke_results = tuple([await smoke_run(case) for case in cases])
    return SmokeReport(
        smoke_results=smoke_results,
        value_checks=(),
        counts_by_miss_kind={kind: 0 for kind in VALUE_DIFF_KINDS},
        total_cases=len(smoke_results),
        total_errors=sum(1 for result in smoke_results if result.run_status == "errored"),
        total_value_mismatches=0,
    )


def smoke_check_values(result: SmokeResult, case: SmokeCase) -> ValueCheckResult:
    """Compare expected final values by reconstructing canonical output from replay."""

    if result.run_status == "errored" or result.replay_artifact_ref is None:
        return ValueCheckResult(
            case_id=case.case_id,
            status="not_checked",
            replay_artifact_ref=result.replay_artifact_ref,
            error=result.error,
        )

    try:
        artifact = read_replay(case.store, result.replay_artifact_ref)
        extraction = reconstruct_extraction(
            artifact,
            artifact_id=result.replay_artifact_ref,
        )
    except Exception as exc:  # noqa: BLE001 - deterministic projection failure.
        return ValueCheckResult(
            case_id=case.case_id,
            status="not_checked",
            replay_artifact_ref=result.replay_artifact_ref,
            error=ErrorInfo(
                kind="setup",
                message=f"smoke.value_check: replay could not be reconstructed: {exc!s}",
            ),
        )

    misses = score_instances(
        case_id=case.case_id,
        expected=case.expected_instances,
        actual=extraction.instances,
        replay_artifact_ref=result.replay_artifact_ref,
    )
    return ValueCheckResult(
        case_id=case.case_id,
        status="matched" if not misses else "mismatched",
        replay_artifact_ref=result.replay_artifact_ref,
        misses=misses,
        error=None,
    )


async def smoke_run_and_check(cases: Iterable[SmokeCase]) -> SmokeReport:
    case_tuple = tuple(cases)
    smoke_results = tuple([await smoke_run(case) for case in case_tuple])
    value_checks = tuple(
        smoke_check_values(result, case)
        for result, case in zip(smoke_results, case_tuple, strict=True)
    )
    counter: Counter[ValueDiffKind] = Counter()
    for check in value_checks:
        counter.update(miss.kind for miss in check.misses)
    return SmokeReport(
        smoke_results=smoke_results,
        value_checks=value_checks,
        counts_by_miss_kind={kind: counter[kind] for kind in VALUE_DIFF_KINDS},
        total_cases=len(smoke_results),
        total_errors=sum(1 for result in smoke_results if result.run_status == "errored"),
        total_value_mismatches=sum(1 for check in value_checks if check.status == "mismatched"),
    )


def _errored_result(case: SmokeCase, result: Extraction, error: ErrorInfo) -> SmokeResult:
    return SmokeResult(
        case_id=case.case_id,
        run_status="errored",
        extraction_outcome=result.outcome,
        replay_artifact_ref=result.replay_artifact_ref or None,
        producer_versions={},
        usage_events=result.usage_events,
        error=error,
    )
