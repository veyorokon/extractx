from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest
from extractx import ValueKind, extract_field
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import StrategyBinding
from extractx.core.outcomes import ExecutionTrace, Extraction
from extractx.replay import read_replay
from extractx.storage import LocalFilesystemStore
from pydantic import BaseModel, field_validator

import extractx_eval.smoke as harness
from extractx_eval import (
    ExpectedField,
    ExpectedInstance,
    SmokeCase,
    smoke_check_values,
    smoke_run,
    smoke_run_and_check,
)


class _Phone(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )


class _PhonePlusRejectedZip(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )
    zip_code: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="zip code",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{5}"},
                kind="candidate",
            ),
        ),
    )

    @field_validator("zip_code")
    @classmethod
    def _reject_zip(cls, value: str) -> str:
        del value
        raise ValueError("zip code rejected")


class _UnmatchedPhone(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"PHONE:\s*[A-Z]+"},
                kind="candidate",
            ),
        ),
    )


def _expected_phone(
    value: object,
    *,
    source_text: str | None = None,
) -> tuple[ExpectedInstance, ...]:
    return (
        ExpectedInstance(
            fields=(
                ExpectedField(
                    field_id="phone",
                    value=value,
                    source_text=source_text,
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_complete_case_writes_replay_and_report(tmp_path: Path) -> None:
    store = LocalFilesystemStore(tmp_path)
    case = SmokeCase(
        case_id="complete-phone",
        document="Call 555-1234.",
        schema=_Phone,
        store=store,
        expected_instances=_expected_phone("555-1234"),
    )

    result = await smoke_run(case)

    assert result.error is None
    assert result.run_status == "completed"
    assert result.extraction_outcome == "complete"
    assert result.replay_artifact_ref is not None
    artifact = read_replay(store, result.replay_artifact_ref)
    assert result.producer_versions == artifact.producer_versions
    check = smoke_check_values(result, case)
    assert check.status == "matched"
    assert check.misses == ()


@pytest.mark.asyncio
async def test_partial_case_still_writes_replay_and_report(tmp_path: Path) -> None:
    case = SmokeCase(
        case_id="partial-phone",
        document="Call 555-1234. ZIP 90210.",
        schema=_PhonePlusRejectedZip,
        store=LocalFilesystemStore(tmp_path),
        expected_instances=_expected_phone("555-1234"),
    )

    result = await smoke_run(case)

    assert result.error is None
    assert result.run_status == "completed_with_outcome"
    assert result.extraction_outcome == "partial"
    assert result.replay_artifact_ref != ""
    check = smoke_check_values(result, case)
    assert check.status == "matched"


@pytest.mark.asyncio
async def test_report_aggregates_miss_counts(tmp_path: Path) -> None:
    first = SmokeCase(
        case_id="complete-phone",
        document="Call 555-1234.",
        schema=_Phone,
        store=LocalFilesystemStore(tmp_path / "first"),
        expected_instances=_expected_phone("555-1234"),
    )
    second = SmokeCase(
        case_id="missing-phone",
        document="Call 555-1234.",
        schema=_UnmatchedPhone,
        store=LocalFilesystemStore(tmp_path / "second"),
        expected_instances=_expected_phone("555-1234"),
    )

    report = await smoke_run_and_check((first, second))

    assert report.total_cases == 2
    assert report.total_value_mismatches == 1
    assert report.total_errors == 0
    assert report.counts_by_miss_kind["instance_count_mismatch"] == 1
    assert report.counts_by_miss_kind["missing_field"] == 0


@pytest.mark.asyncio
async def test_failed_result_scores_instance_count_mismatch(tmp_path: Path) -> None:
    case = SmokeCase(
        case_id="failed-result",
        document="Call 555-1234.",
        schema=_UnmatchedPhone,
        store=LocalFilesystemStore(tmp_path),
        expected_instances=_expected_phone("555-1234"),
    )

    result = await smoke_run(case)

    assert result.extraction_outcome == "failed"
    check = smoke_check_values(result, case)
    assert len(check.misses) == 1
    assert check.misses[0].kind == "instance_count_mismatch"
    assert check.misses[0].replay_artifact_ref == result.replay_artifact_ref


@pytest.mark.asyncio
async def test_value_mismatch_bucket(tmp_path: Path) -> None:
    case = SmokeCase(
        case_id="value-mismatch",
        document="Call 555-1234.",
        schema=_Phone,
        store=LocalFilesystemStore(tmp_path),
        expected_instances=_expected_phone("555-9999"),
    )

    result = await smoke_run(case)
    check = smoke_check_values(result, case)

    assert len(check.misses) == 1
    miss = check.misses[0]
    assert miss.kind == "value_mismatch"
    assert miss.instance_id != ""
    assert miss.actual == "555-1234"
    assert miss.expected == "555-9999"
    assert miss.source_text == "555-1234"


@pytest.mark.asyncio
async def test_partial_result_scores_missing_field(tmp_path: Path) -> None:
    case = SmokeCase(
        case_id="missing-field",
        document="Call 555-1234. ZIP 90210.",
        schema=_PhonePlusRejectedZip,
        store=LocalFilesystemStore(tmp_path),
        expected_instances=(
            ExpectedInstance(
                fields=(
                    ExpectedField(field_id="phone", value="555-1234"),
                    ExpectedField(field_id="zip_code", value="90210"),
                ),
            ),
        ),
    )

    result = await smoke_run(case)
    check = smoke_check_values(result, case)

    assert {miss.kind for miss in check.misses} == {"missing_field"}


@pytest.mark.asyncio
async def test_multi_expected_without_observed_instance_scores_count_mismatch(
    tmp_path: Path,
) -> None:
    case = SmokeCase(
        case_id="multi-expected",
        document="Call 555-1234.",
        schema=_Phone,
        store=LocalFilesystemStore(tmp_path),
        expected_instances=(
            ExpectedInstance(fields=(ExpectedField(field_id="phone", value="555-1234"),)),
            ExpectedInstance(fields=(ExpectedField(field_id="phone", value="555-5678"),)),
        ),
    )

    result = await smoke_run(case)
    check = smoke_check_values(result, case)

    assert {miss.kind for miss in check.misses} == {"instance_count_mismatch"}


@pytest.mark.asyncio
async def test_empty_replay_ref_is_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_extract(
        document: str | bytes,
        schema: type[BaseModel],
        *,
        store: object | None = None,
    ) -> Extraction:
        del document, schema, store
        return Extraction(
            document_id="doc-1",
            spec_version="v1",
            outcome="failed",
            strategy="independent",
            instances=(),
            trace=ExecutionTrace(trace_id="trace-1"),
            replay_artifact_ref="",
        )

    monkeypatch.setattr(harness, "extract", fake_extract)
    case = SmokeCase(
        case_id="setup-failure",
        document="Call 555-1234.",
        schema=_Phone,
        store=LocalFilesystemStore(tmp_path),
        expected_instances=_expected_phone("555-1234"),
    )

    result = await smoke_run(case)

    assert result.run_status == "errored"
    assert result.error is not None
    assert "replay_artifact_ref" in result.error.message
    check = smoke_check_values(result, case)
    assert check.status == "not_checked"
    assert check.misses == ()


def test_smoke_case_rejects_empty_case_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="case_id"):
        SmokeCase(
            case_id="",
            document="Call 555-1234.",
            schema=_Phone,
            store=LocalFilesystemStore(tmp_path),
            expected_instances=_expected_phone("555-1234"),
        )
