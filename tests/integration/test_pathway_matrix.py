"""explicit supported-pathway integration matrix.

This file is the CI-safe inventory of the runtime pathways extractx promises
today. By default it uses fake providers; set
`EXTRACTX_RUN_LIVE_PATHWAY_MATRIX=1` plus `OPENAI_API_KEY` to run the same
soft-compute matrix against the OpenAI provider.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel

from extractx import (
    And,
    ExecutorPolicy,
    ExtractionSpec,
    FilterBinding,
    LabelIn,
    NumericRange,
    Runtime,
    ValueKind,
    extract_field,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core import (
    Cardinality,
    InstanceProposerBinding,
    ProviderResult,
    RenderedPrompt,
    SelectorBinding,
    StrategyBinding,
    UsageEvent,
)
from extractx.execution.executor.serial import SerialExecutor
from extractx.extras.pydantic_ai import (
    InstanceProposalResponse,
    LLMInstanceProposer,
    PydanticAIOpenAIProvider,
    PydanticAISelector,
    SelectorObservationResponse,
)
from extractx.replay import read_replay
from extractx.storage import LocalFilesystemStore

_RUN_LIVE_MATRIX = os.environ.get("EXTRACTX_RUN_LIVE_PATHWAY_MATRIX") == "1"
_MODEL_ID = (
    os.environ.get("EXTRACTX_OPENAI_MODEL", "gpt-5.4-nano") if _RUN_LIVE_MATRIX else "fake:model"
)


class _OrderNumber(BaseModel):
    order_number: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="order number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"Order #(?P<value>\d+)", "group": "value"},
                kind="candidate",
            ),
        ),
    )


class _FilteredAmount(BaseModel):
    amount: Annotated[Decimal, ValueKind.MONEY] = extract_field(
        description="invoice amount under 100",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={
                    "pattern": r"\$(?P<value>\d+(?:\.\d+)?)",
                    "group": "value",
                    "entity_type": "MONEY",
                },
                kind="candidate",
            ),
        ),
        filter_binding=FilterBinding(
            expr=And(
                exprs=(
                    LabelIn(labels=("MONEY",)),
                    NumericRange(hi="100"),
                ),
            ),
        ),
    )


class _OneArmCategory(BaseModel):
    document_type: Annotated[Literal["invoice"], ValueKind.CATEGORY] = extract_field(
        description="constant invoice classification",
    )


class _DocumentCategory(BaseModel):
    document_type: Annotated[
        Literal["invoice", "receipt", "irrelevant"],
        ValueKind.CATEGORY,
    ] = extract_field(
        description="document type",
        selector_binding=SelectorBinding(
            cls=PydanticAISelector,
            params={"model_id": _MODEL_ID},
        ),
    )


class _SupportPhone(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="support phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
        selector_binding=SelectorBinding(
            cls=PydanticAISelector,
            params={"model_id": _MODEL_ID},
        ),
    )


class _StudyDose(BaseModel):
    dose: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="dose amount for the study arm",
        cardinality=Cardinality.PER_INSTANCE,
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d+(?:\.\d+)? mg"},
                kind="candidate",
            ),
        ),
        selector_binding=SelectorBinding(
            cls=PydanticAISelector,
            params={"model_id": _MODEL_ID},
        ),
    )


class _MatrixProvider:
    """Fake provider that exercises soft seams without live network calls."""

    def __init__(self, *, emit_usage: bool = False) -> None:
        self.calls: list[RenderedPrompt] = []
        self.emit_usage = emit_usage

    def __call__(self, rendered: RenderedPrompt, output_type: type[Any]) -> Any:
        self.calls.append(rendered)
        if output_type is InstanceProposalResponse:
            response = output_type.model_validate(
                {
                    "selected_instance_ids": rendered.metadata["allowed_instance_ids"],
                    "reason": "select all bounded instances",
                },
            )
            return self._with_usage(rendered, response, operation="instance_proposer")
        if output_type is SelectorObservationResponse:
            response = self._selector_response(rendered, output_type)
            return self._with_usage(rendered, response, operation="selector")
        raise AssertionError(f"unexpected output_type {output_type!r}")

    def _selector_response(
        self,
        rendered: RenderedPrompt,
        output_type: type[SelectorObservationResponse],
    ) -> SelectorObservationResponse:
        field_id = rendered.metadata["allowed_field_ids"][0]
        if field_id == "document_type":
            payload = json.loads(rendered.messages[1].content)
            by_literal = {
                candidate["literal"]: candidate["candidate_id"]
                for candidate in payload["candidates"]
            }
            evidence_id = by_literal["invoice"]
        else:
            evidence_ids = rendered.metadata["allowed_evidence_ids"]
            evidence_id = evidence_ids[0] if evidence_ids else None
        return output_type.model_validate(
            {
                "instance_id": rendered.metadata["allowed_instance_ids"][0],
                "field_id": field_id,
                "evidence_id": evidence_id,
                "abstain": evidence_id is None,
                "reason": "matrix provider selected bounded id",
            },
        )

    def _with_usage(
        self,
        rendered: RenderedPrompt,
        response: Any,
        *,
        operation: str,
    ) -> Any:
        if not self.emit_usage:
            return response
        return ProviderResult(
            output=response,
            usage_event=UsageEvent(
                producer_version=rendered.metadata["producer_version"],
                operation=operation,
                field_id=rendered.metadata.get("allowed_field_ids", (None,))[0],
                instance_id=rendered.metadata.get("allowed_instance_ids", (None,))[0],
                model_id=rendered.metadata["model_id"],
                input_tokens=7,
                output_tokens=3,
                total_tokens=10,
                timestamp_ns=456,
                raw_usage={"input_tokens": 7, "output_tokens": 3},
            ),
        )


class _RecordingLiveProvider:
    """OpenAI provider wrapper that preserves the matrix's prompt assertions."""

    def __init__(self) -> None:
        self.calls: list[RenderedPrompt] = []
        self._delegate = PydanticAIOpenAIProvider.from_env()

    def __call__(self, rendered: RenderedPrompt, output_type: type[Any]) -> Any:
        self.calls.append(rendered)
        return self._delegate(rendered, output_type)


def _matrix_provider(*, emit_usage: bool = False) -> _MatrixProvider | _RecordingLiveProvider:
    if not _RUN_LIVE_MATRIX:
        return _MatrixProvider(emit_usage=emit_usage)
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip(
            "live pathway matrix is opt-in: set EXTRACTX_RUN_LIVE_PATHWAY_MATRIX=1 "
            "and OPENAI_API_KEY",
        )
    return _RecordingLiveProvider()


def _assert_usage_present(usage: tuple[UsageEvent, ...]) -> None:
    if _RUN_LIVE_MATRIX:
        assert all(event.input_tokens is not None for event in usage)
        assert all(event.output_tokens is not None for event in usage)
    else:
        assert all(event.input_tokens == 7 for event in usage)
        assert all(event.output_tokens == 3 for event in usage)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schema", "document", "field_id", "expected"),
    (
        (
            _OrderNumber,
            "Order #12345 ships tomorrow.",
            "order_number",
            "12345",
        ),
        (
            _FilteredAmount,
            "Candidate amounts: $250.00 and $42.50.",
            "amount",
            Decimal("42.50"),
        ),
        (
            _OneArmCategory,
            "This text is irrelevant because the schema has one literal arm.",
            "document_type",
            "invoice",
        ),
    ),
)
async def test_deterministic_pathways(
    schema: type[BaseModel],
    document: str,
    field_id: str,
    expected: Any,
) -> None:
    result = await run_extraction(
        document=document,
        spec=ExtractionSpec.from_pydantic(schema),
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result.outcome == "complete"
    evidence = result.instances[0].evidence[0]
    assert evidence.field_id == field_id
    assert evidence.normalized_value == expected
    assert result.usage() == ()
    if schema is _FilteredAmount:
        assert evidence.raw_value == "42.50"


@pytest.mark.asyncio
async def test_llm_category_selector_pathway_with_usage_and_replay(tmp_path: Path) -> None:
    provider = _matrix_provider(emit_usage=True)
    store = LocalFilesystemStore(tmp_path / "store")

    result = await SerialExecutor(storage=store).execute(
        document="Invoice INV-1001. Total: $42.50.",
        spec=ExtractionSpec.from_pydantic(_DocumentCategory),
        runtime=Runtime(llm=provider),
        policy=ExecutorPolicy(strategy="independent"),
    )

    usage = result.usage()
    assert result.outcome == "complete"
    assert result.instances[0].to_pydantic(_DocumentCategory).document_type == "invoice"
    assert [event.operation for event in usage] == ["selector"]
    assert usage[0].field_id == "document_type"
    _assert_usage_present(usage)
    artifact = read_replay(store, result.replay_artifact_ref)
    assert artifact.usage_events == usage


@pytest.mark.asyncio
async def test_llm_scalar_selector_pathway_uses_bounded_candidate_ids(tmp_path: Path) -> None:
    provider = _matrix_provider()
    store = LocalFilesystemStore(tmp_path / "store")

    result = await SerialExecutor(storage=store).execute(
        document="Support 555-1111. Alternate 555-2222.",
        spec=ExtractionSpec.from_pydantic(_SupportPhone),
        runtime=Runtime(llm=provider),
        policy=ExecutorPolicy(strategy="independent"),
    )

    artifact = read_replay(store, result.replay_artifact_ref)
    observation = artifact.observations[0]

    assert result.outcome == "complete"
    assert result.instances[0].to_pydantic(_SupportPhone).phone == "555-1111"
    assert len(provider.calls) == 1
    assert len(provider.calls[0].metadata["allowed_evidence_ids"]) == 2
    assert observation.evidence_id in provider.calls[0].metadata[
        "canonical_allowed_evidence_ids"
    ]


@pytest.mark.asyncio
async def test_llm_many_instance_proposer_pathway_with_usage_and_replay(tmp_path: Path) -> None:
    provider = _matrix_provider(emit_usage=True)
    store = LocalFilesystemStore(tmp_path / "store")
    spec = ExtractionSpec.from_pydantic(
        _StudyDose,
        instance_type="StudyArm",
        instance_cardinality=Cardinality.MANY,
        instance_proposer_binding=InstanceProposerBinding(
            cls=LLMInstanceProposer,
            params={"model_id": _MODEL_ID},
        ),
    )

    result = await SerialExecutor(storage=store).execute(
        document="Arm A receives 20 mg daily.\nArm B receives 30 mg daily.",
        spec=spec,
        runtime=Runtime(llm=provider),
        policy=ExecutorPolicy(strategy="independent"),
    )

    usage = result.usage()
    artifact = read_replay(store, result.replay_artifact_ref)

    assert result.outcome == "complete"
    assert [instance.instance_id for instance in result.instances] == ["inst_0", "inst_1"]
    assert [instance.evidence[0].normalized_value for instance in result.instances] == [
        "20 mg",
        "30 mg",
    ]
    assert [event.operation for event in usage] == [
        "instance_proposer",
        "selector",
        "selector",
    ]
    _assert_usage_present(usage)
    assert artifact.instance_proposer_response is not None
    assert artifact.instance_proposer_response.selected_instance_ids == ("inst_0", "inst_1")
    assert artifact.usage_events == usage
