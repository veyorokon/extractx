from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import pytest
from extractx import ValueKind, extract_field
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import StrategyBinding
from extractx.replay import read_replay
from extractx.replay.artifact import ReplayArtifact
from extractx.storage import LocalFilesystemStore
from pydantic import BaseModel, field_validator

from extractx_eval import (
    BenchmarkAggregate,
    BenchmarkFieldRow,
    BenchmarkFixture,
    BenchmarkReport,
    GoldEvidence,
    GoldField,
    GoldInstance,
    SmokeCase,
    score_replay,
    smoke_run,
)


def _regex_binding(pattern: str) -> StrategyBinding:
    return StrategyBinding(
        cls=RegexCandidateStrategy,
        params={"pattern": pattern},
        kind="candidate",
    )


class _Phone(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(_regex_binding(r"\d{3}-\d{4}"),),
    )


class _RejectingZip(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(_regex_binding(r"\d{3}-\d{4}"),),
    )
    zip_code: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="postal code",
        strategy_bindings=(_regex_binding(r"\d{5}"),),
    )

    @field_validator("zip_code")
    @classmethod
    def _reject_zip(cls, value: str) -> str:
        del value
        raise ValueError("postal code rejected")


class _MissingPhone(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(_regex_binding(r"PHONE:\s*[A-Z]+"),),
    )


class _TypedScalars(BaseModel):
    amount: Annotated[Decimal, ValueKind.MONEY] = extract_field(
        description="amount",
        strategy_bindings=(_regex_binding(r"\b150000000\b"),),
    )
    maturity_date: Annotated[date, ValueKind.DATE] = extract_field(
        description="maturity date",
        strategy_bindings=(_regex_binding(r"\b2030-03-15\b"),),
    )
    soft_call_days_required: Annotated[int, ValueKind.CARDINAL] = extract_field(
        description="soft call days required",
        strategy_bindings=(_regex_binding(r"\b20\b"),),
    )


def _fixture(
    *,
    case_id: str,
    document: str,
    fields: tuple[GoldField, ...],
) -> BenchmarkFixture:
    return BenchmarkFixture(
        case_id=case_id,
        schema_id="test_schema",
        document_path=f"{case_id}.txt",
        document=document,
        expected_instances=(GoldInstance(fields=fields),),
    )


def _aggregate(report_name: str, report: BenchmarkReport) -> BenchmarkAggregate:
    return next(aggregate for aggregate in report.aggregates if aggregate.name == report_name)


def _row(report: BenchmarkReport, *, field_id: str, stage: str) -> BenchmarkFieldRow:
    return next(row for row in report.field_rows if row.field_id == field_id and row.stage == stage)


async def _artifact_for(
    tmp_path: Path,
    *,
    case_id: str,
    document: str,
    schema: type[BaseModel],
) -> ReplayArtifact:
    store = LocalFilesystemStore(tmp_path / case_id)
    result = await smoke_run(
        SmokeCase(case_id=case_id, document=document, schema=schema, store=store),
    )
    assert result.replay_artifact_ref is not None
    return read_replay(store, result.replay_artifact_ref)


@pytest.mark.asyncio
async def test_score_replay_reports_complete_path(tmp_path: Path) -> None:
    artifact = await _artifact_for(
        tmp_path,
        case_id="complete-phone",
        document="Call 555-1234.",
        schema=_Phone,
    )
    fixture = _fixture(
        case_id="complete-phone",
        document="Call 555-1234.",
        fields=(
            GoldField(
                field_id="phone",
                expected_value="555-1234",
                evidence=(GoldEvidence(text="555-1234"),),
            ),
        ),
    )

    report = score_replay(artifact, (fixture,))

    assert report.case_rows[0].status == "passed"
    assert report.metadata.parameters["span_match"]["mode"] == "overlap"
    assert _aggregate("recall_at_replay_candidates", report).rate == 1.0
    assert _aggregate("recall_at_selection", report).rate == 1.0
    assert _aggregate("recall_at_validation", report).rate == 1.0
    assert _aggregate("recall_at_materialization", report).rate == 1.0
    assert _aggregate("value_accuracy_at_materialization", report).rate == 1.0


@pytest.mark.asyncio
async def test_score_replay_reports_candidate_absence(tmp_path: Path) -> None:
    artifact = await _artifact_for(
        tmp_path,
        case_id="missing-phone",
        document="Call 555-1234.",
        schema=_MissingPhone,
    )
    fixture = _fixture(
        case_id="missing-phone",
        document="Call 555-1234.",
        fields=(
            GoldField(
                field_id="phone",
                expected_value="555-1234",
                evidence=(GoldEvidence(text="555-1234"),),
            ),
        ),
    )

    report = score_replay(artifact, (fixture,))

    assert report.case_rows[0].status == "failed"
    row = _row(report, field_id="phone", stage="filtered_candidates")
    assert row.gap_kind == "missing_expected"
    assert row.attributions[0].kind == "not_generated"
    assert row.attributions[0].details["gold_text"] == "555-1234"
    selection = _row(
        report,
        field_id="phone",
        stage="selection",
    )
    assert selection.message is not None
    assert "expected evidence was not present" in selection.message


@pytest.mark.asyncio
async def test_score_replay_reports_validation_rejection(tmp_path: Path) -> None:
    artifact = await _artifact_for(
        tmp_path,
        case_id="rejected-zip",
        document="Call 555-1234. ZIP 90210.",
        schema=_RejectingZip,
    )
    fixture = _fixture(
        case_id="rejected-zip",
        document="Call 555-1234. ZIP 90210.",
        fields=(
            GoldField(
                field_id="zip_code",
                expected_value="90210",
                evidence=(GoldEvidence(text="90210"),),
            ),
        ),
    )

    report = score_replay(artifact, (fixture,))

    assert report.case_rows[0].status == "failed"
    validation = _row(report, field_id="zip_code", stage="validation")
    assert validation.gap_kind == "missing_expected"
    assert validation.attributions[0].kind == "validation_rejected"
    assert validation.attributions[0].details["validation_code"] == "field_failure"
    assert validation.observed["negative_outcomes"][0]["category"] == "validation"
    materialization = _row(report, field_id="zip_code", stage="materialization")
    assert materialization.gap_kind == "missing_expected"


@pytest.mark.asyncio
async def test_score_replay_attributes_materialized_value_mismatch(tmp_path: Path) -> None:
    artifact = await _artifact_for(
        tmp_path,
        case_id="value-mismatch-phone",
        document="Call 555-1234.",
        schema=_Phone,
    )
    fixture = _fixture(
        case_id="value-mismatch-phone",
        document="Call 555-1234.",
        fields=(
            GoldField(
                field_id="phone",
                expected_value="555-0000",
                evidence=(GoldEvidence(text="555-1234"),),
            ),
        ),
    )

    report = score_replay(artifact, (fixture,))

    materialization = _row(report, field_id="phone", stage="materialization")
    assert materialization.gap_kind == "value_mismatch"
    assert materialization.attributions[0].kind == "normalization_mismatch"
    assert materialization.attributions[0].details["fixture_value"] == "555-0000"


@pytest.mark.asyncio
async def test_score_replay_compares_fixture_values_to_replay_serialized_values(
    tmp_path: Path,
) -> None:
    """Replay msgpack serializes Decimal/date normalized values as strings.

    Score comparison is a semantic value check, not a transport byte check:
    typed fixture values must compare equal to their replay-safe persisted
    forms while primitive ints stay primitive.
    """
    artifact = await _artifact_for(
        tmp_path,
        case_id="typed-scalars",
        document="Amount 150000000. Matures 2030-03-15. Requires 20 days.",
        schema=_TypedScalars,
    )
    fixture = _fixture(
        case_id="typed-scalars",
        document="Amount 150000000. Matures 2030-03-15. Requires 20 days.",
        fields=(
            GoldField(
                field_id="amount",
                expected_value=Decimal("150000000"),
                evidence=(GoldEvidence(text="150000000"),),
            ),
            GoldField(
                field_id="maturity_date",
                expected_value=date(2030, 3, 15),
                evidence=(GoldEvidence(text="2030-03-15"),),
            ),
            GoldField(
                field_id="soft_call_days_required",
                expected_value=20,
                evidence=(GoldEvidence(text="20"),),
            ),
        ),
    )

    report = score_replay(artifact, (fixture,))

    assert report.case_rows[0].status == "passed"
    assert _aggregate("value_accuracy_at_materialization", report).rate == 1.0
    assert not [
        attr
        for row in report.field_rows
        for attr in row.attributions
        if attr.kind == "normalization_mismatch"
    ]


@pytest.mark.asyncio
async def test_score_replay_supports_case_id_mapping(tmp_path: Path) -> None:
    first = await _artifact_for(
        tmp_path,
        case_id="first-phone",
        document="Call 555-1234.",
        schema=_Phone,
    )
    second = await _artifact_for(
        tmp_path,
        case_id="second-phone",
        document="Call 555-9999.",
        schema=_Phone,
    )
    fixtures = (
        _fixture(
            case_id="first-phone",
            document="Call 555-1234.",
            fields=(
                GoldField(
                    field_id="phone",
                    expected_value="555-1234",
                    evidence=(GoldEvidence(text="555-1234"),),
                ),
            ),
        ),
        _fixture(
            case_id="second-phone",
            document="Call 555-9999.",
            fields=(
                GoldField(
                    field_id="phone",
                    expected_value="555-9999",
                    evidence=(GoldEvidence(text="555-9999"),),
                ),
            ),
        ),
    )

    report = score_replay({"first-phone": first, "second-phone": second}, fixtures)

    assert [row.status for row in report.case_rows] == ["passed", "passed"]
