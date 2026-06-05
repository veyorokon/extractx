"""focused proof for `extract_one(...)` per docs/tasks/api-phase-2-extract-one.md."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, field_validator

import extractx
import extractx.api as api
from extractx import (
    Extraction,
    ExtractionFailed,
    SpecError,
    ValueKind,
    extract_field,
    extract_one,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.anchors import SourceRef, SourceSpan
from extractx.core.objects import GroupingEvidence, InstanceGroupingKey, StrategyBinding
from extractx.core.outcomes import (
    Evidence,
    ExecutionTrace,
    Instance,
    ProposalProvenance,
)
from extractx.storage import LocalFilesystemStore


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


class _PhonePlusReject(BaseModel):
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
    rejected: Annotated[str, ValueKind.PERSON] = extract_field(
        description="zip code",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{5}"},
                kind="candidate",
            ),
        ),
    )

    @field_validator("rejected")
    @classmethod
    def _reject(cls, value: str) -> str:
        del value
        raise ValueError("disallowed zip")


def _span(start: int = 0, end: int = 1) -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="doc-1", content_hash="sha256:abc"),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def _key(ordinal: int = 0) -> InstanceGroupingKey:
    return InstanceGroupingKey(
        group_id=f"group-{ordinal}",
        ordinal=ordinal,
        group_anchors=(_span(),),
    )


def _proposal(
    field_id: str,
    normalized_value: object,
    *,
    ordinal: int = 0,
) -> Evidence:
    instance_key = _key(ordinal)
    return Evidence(
        field_id=field_id,
        instance_key=instance_key,
        raw_value=str(normalized_value),
        evidence_text=str(normalized_value),
        source_span=_span(ordinal, ordinal + 1),
        evidence_spans=(),
        normalized_value=normalized_value,
        proposal_provenance=ProposalProvenance(strategy_id="test"),
    )


def _instance(ordinal: int = 0) -> Instance:
    return Instance(
        instance_key=_key(ordinal),
        outcome="complete",
        evidence=(_proposal("phone", f"555-123{ordinal}", ordinal=ordinal),),
        negative_outcomes=(),
        grouping_evidence=GroupingEvidence(
            stage="resolved",
            anchor_spans=(_span(),),
            producer_version="test",
        ),
    )


def _result(
    instances: tuple[Instance, ...],
    *,
    outcome: Literal["complete", "partial", "failed"] = "complete",
) -> Extraction:
    return Extraction(
        document_id="doc-1",
        spec_version="v1",
        outcome=outcome,
        strategy="independent",
        instances=instances,
        trace=ExecutionTrace(trace_id="trace-1"),
        replay_artifact_ref="",
    )


def test_extract_one_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(extract_one)


def test_extract_one_signature_matches_brief() -> None:
    sig = inspect.signature(extract_one)
    params = list(sig.parameters.values())

    assert [p.name for p in params] == [
        "document",
        "schema",
        "runtime",
        "store",
        "capture_interviews",
    ]

    document_p, schema_p, runtime_p, store_p, capture_p = params
    assert document_p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert document_p.default is inspect.Parameter.empty
    assert schema_p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert schema_p.default is inspect.Parameter.empty
    assert runtime_p.kind is inspect.Parameter.KEYWORD_ONLY
    assert runtime_p.default is None
    assert store_p.kind is inspect.Parameter.KEYWORD_ONLY
    assert store_p.default is None
    assert capture_p.kind is inspect.Parameter.KEYWORD_ONLY
    assert capture_p.default is False


def test_extract_one_and_exception_are_tier1_exports() -> None:
    assert "extract_one" in extractx.__all__
    assert "ExtractionFailed" in extractx.__all__
    assert extractx.extract_one is api.extract_one
    assert extractx.ExtractionFailed is ExtractionFailed


@pytest.mark.asyncio
async def test_extract_one_happy_path_returns_schema_instance() -> None:
    phone = await extract_one("Call us at 555-1234.", _Phone)

    assert isinstance(phone, _Phone)
    assert phone.phone == "555-1234"


@pytest.mark.asyncio
async def test_extract_one_calls_extract_with_same_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = LocalFilesystemStore(tmp_path)
    calls: list[tuple[str | bytes, type[BaseModel], object | None, object | None, bool]] = []
    expected_result = _result((_instance(),))

    async def spy_extract(
        document: str | bytes,
        schema: type[BaseModel],
        *,
        runtime: object | None = None,
        store: object | None = None,
        capture_interviews: bool = False,
    ) -> Extraction:
        calls.append((document, schema, runtime, store, capture_interviews))
        return expected_result

    monkeypatch.setattr(api, "extract", spy_extract)
    runtime = object()

    phone = await extract_one(
        "Call us at 555-1234.",
        _Phone,
        runtime=runtime,  # type: ignore[arg-type]
        store=store,
        capture_interviews=True,
    )

    assert phone.phone == "555-1230"
    assert calls == [("Call us at 555-1234.", _Phone, runtime, store, True)]


@pytest.mark.asyncio
async def test_extract_one_failed_outcome_raises_with_result() -> None:
    with pytest.raises(ExtractionFailed, match=r"^extract_one\.failed: ") as exc_info:
        await extract_one("no digits here", _Phone)

    assert exc_info.value.result.outcome == "failed"
    assert exc_info.value.result.instances == ()


@pytest.mark.asyncio
async def test_extract_one_partial_outcome_raises_with_result() -> None:
    with pytest.raises(ExtractionFailed, match=r"^extract_one\.failed: ") as exc_info:
        await extract_one("Call us at 555-1234. ZIP 90210.", _PhonePlusReject)

    assert exc_info.value.result.outcome == "partial"
    assert exc_info.value.result.instances[0].outcome == "partial"


@pytest.mark.asyncio
async def test_extract_one_many_materialized_objects_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_result = _result((_instance(0), _instance(1)))

    async def spy_extract(
        document: str | bytes,
        schema: type[BaseModel],
        *,
        runtime: object | None = None,
        store: object | None = None,
        capture_interviews: bool = False,
    ) -> Extraction:
        del document, schema, runtime, store, capture_interviews
        return expected_result

    monkeypatch.setattr(api, "extract", spy_extract)

    with pytest.raises(ExtractionFailed, match=r"expected exactly one") as exc_info:
        await extract_one("Call us at 555-1234.", _Phone)

    assert exc_info.value.result is expected_result


@pytest.mark.asyncio
async def test_extract_one_zero_materialized_objects_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_result = _result(())

    async def spy_extract(
        document: str | bytes,
        schema: type[BaseModel],
        *,
        runtime: object | None = None,
        store: object | None = None,
        capture_interviews: bool = False,
    ) -> Extraction:
        del document, schema, runtime, store, capture_interviews
        return expected_result

    monkeypatch.setattr(api, "extract", spy_extract)

    with pytest.raises(ExtractionFailed, match=r"expected exactly one") as exc_info:
        await extract_one("Call us at 555-1234.", _Phone)

    assert exc_info.value.result is expected_result


@pytest.mark.asyncio
async def test_extract_one_setup_errors_propagate_as_spec_error() -> None:
    class _NotAModel:
        pass

    with pytest.raises(SpecError):
        await extract_one("Call us at 555-1234.", _NotAModel)  # type: ignore[arg-type]
