"""end-to-end proof for document-level Literal classification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel

from extractx import (
    ExecutorPolicy,
    ExtractionSpec,
    Runtime,
    SpecError,
    ValueKind,
    extract_field,
    run_extraction,
)
from extractx.core import (
    ClassificationContextBinding,
    ContextPack,
    ProviderResult,
    SelectorBinding,
    UsageEvent,
)
from extractx.extras.pydantic_ai import PydanticAISelector, SelectorObservationResponse
from extractx.replay import read_replay
from extractx.selection import RegexWindowClassificationContextStrategy, SelectorPromptPolicy
from extractx.selection.prompts import ClassificationPrompt
from extractx.storage import LocalFilesystemStore


class _OneArmVerdict(BaseModel):
    verdict: Annotated[Literal["invoice"], ValueKind.CATEGORY] = extract_field(
        description="constant document-type verdict",
    )


class _DocumentTypeVerdict(BaseModel):
    verdict: Annotated[
        Literal["invoice", "receipt", "irrelevant"],
        ValueKind.CATEGORY,
    ] = extract_field(
        description="classify document type",
        selector_binding=SelectorBinding(
            cls=PydanticAISelector,
            params={"model_id": "fake:model"},
        ),
    )
    structural_signals: Annotated[
        list[
            Literal[
                "invoice_number",
                "subtotal_line",
                "total_line",
                "payment_terms",
            ]
        ],
        ValueKind.CATEGORY,
    ] = extract_field(
        description="bounded structural signals",
        selector_binding=SelectorBinding(
            cls=PydanticAISelector,
            params={"model_id": "fake:model"},
        ),
    )


class _UnboundDocumentTypeVerdict(BaseModel):
    verdict: Annotated[
        Literal["invoice", "receipt", "irrelevant"],
        ValueKind.CATEGORY,
    ] = extract_field(description="classify document type")


class _ClassificationProvider:
    def __init__(
        self,
        *,
        verdict: str = "invoice",
        signals: tuple[str, ...] = ("invoice_number", "total_line"),
        emit_usage: bool = False,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.verdict = verdict
        self.signals = signals
        self.emit_usage = emit_usage

    def __call__(
        self,
        rendered: Any,
        output_type: type[SelectorObservationResponse],
    ) -> SelectorObservationResponse | ProviderResult[SelectorObservationResponse]:
        payload = json.loads(rendered.messages[1].content)
        self.calls.append(payload)
        by_literal = {
            candidate["literal"]: candidate["candidate_id"] for candidate in payload["candidates"]
        }
        field_id = payload["field"]["field_id"]
        if field_id == "verdict":
            response = output_type.model_validate(
                {
                    "instance_id": rendered.metadata["allowed_instance_ids"][0],
                    "field_id": field_id,
                    "evidence_id": by_literal[self.verdict],
                    "abstain": False,
                    "reason": f"{self.verdict} language",
                },
            )
        else:
            response = output_type.model_validate(
                {
                    "instance_id": rendered.metadata["allowed_instance_ids"][0],
                    "field_id": field_id,
                    "selected_candidate_ids": tuple(by_literal[signal] for signal in self.signals),
                    "abstain": False,
                    "reason": "detected bounded structural signals",
                },
            )
        if not self.emit_usage:
            return response
        return ProviderResult(
            output=response,
            usage_event=UsageEvent(
                producer_version=rendered.metadata["producer_version"],
                operation="selector",
                field_id=field_id,
                instance_id=rendered.metadata["allowed_instance_ids"][0],
                model_id=rendered.metadata["model_id"],
                input_tokens=10,
                output_tokens=2,
                total_tokens=12,
                timestamp_ns=123,
                raw_usage={"input_tokens": 10, "output_tokens": 2},
            ),
        )


@pytest.mark.asyncio
async def test_one_arm_literal_auto_selects_without_llm() -> None:
    spec = ExtractionSpec.from_pydantic(_OneArmVerdict)

    result = await run_extraction(
        document="anything",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )

    instance = result.instances[0]
    assert result.outcome == "complete"
    assert len(instance.evidence) == 1
    assert instance.evidence[0].field_id == "verdict"
    assert instance.evidence[0].normalized_value == "invoice"
    assert instance.evidence[0].source_span.text_anchor_space == "normalized_text"
    assert instance.evidence[0].source_span.byte_start == 0
    assert instance.evidence[0].source_span.byte_end == 0


@pytest.mark.asyncio
async def test_literal_classification_uses_llm_for_three_arm_and_many_fields() -> None:
    provider = _ClassificationProvider()
    spec = ExtractionSpec.from_pydantic(_DocumentTypeVerdict)

    result = await run_extraction(
        document=(
            "Invoice INV-1001 for Example Customer. "
            "Subtotal: $120.00. Total: $128.45. Payment due in 30 days."
        ),
        spec=spec,
        runtime=Runtime(llm=provider),
        policy=ExecutorPolicy(strategy="independent"),
    )

    evidence_by_field: dict[str, list[Any]] = {}
    for evidence in result.instances[0].evidence:
        evidence_by_field.setdefault(evidence.field_id, []).append(evidence.normalized_value)

    assert evidence_by_field["verdict"] == ["invoice"]
    assert evidence_by_field["structural_signals"] == ["invoice_number", "total_line"]
    assert [call["task"] for call in provider.calls] == [
        "document_classification",
        "document_classification",
    ]
    assert "Invoice INV-1001" in provider.calls[0]["document_context"]


@pytest.mark.asyncio
async def test_usage_events_are_recorded_for_llm_classification(tmp_path: Path) -> None:
    provider = _ClassificationProvider(emit_usage=True)
    runtime = Runtime(llm=provider)
    spec = ExtractionSpec.from_pydantic(_DocumentTypeVerdict)

    from extractx.execution.executor.serial import SerialExecutor

    store = LocalFilesystemStore(tmp_path / "store")
    result = await SerialExecutor(storage=store).execute(
        document="Invoice INV-1001. Total: $128.45.",
        spec=spec,
        runtime=runtime,
        policy=ExecutorPolicy(strategy="independent"),
    )

    usage = result.usage()

    assert result.outcome == "complete"
    assert len(usage) == 2
    assert [event.operation for event in usage] == ["selector", "selector"]
    assert [event.field_id for event in usage] == ["verdict", "structural_signals"]
    assert sum(event.input_tokens or 0 for event in usage) == 20
    assert runtime.budget.input_tokens == 20
    assert runtime.budget.output_tokens == 4
    assert result.replay_artifact_ref
    artifact = read_replay(store, result.replay_artifact_ref)
    assert artifact.usage_events == usage


@pytest.mark.asyncio
async def test_literal_classification_can_use_grounded_classification_context(
    tmp_path: Path,
) -> None:
    provider = _ClassificationProvider()
    runtime = Runtime(
        llm=provider,
        selector_prompt_policies={
            "verdict": SelectorPromptPolicy(
                document_context_mode="classification_context",
                classification_context_binding=ClassificationContextBinding(
                    cls=RegexWindowClassificationContextStrategy,
                    params={
                        "patterns": (r"service receipt",),
                        "before_chars": 20,
                        "after_chars": 20,
                        "boundary_mode": "punctuation",
                    },
                ),
            ),
        },
    )
    spec = ExtractionSpec.from_pydantic(_DocumentTypeVerdict)

    from extractx.execution.executor.serial import SerialExecutor

    store = LocalFilesystemStore(tmp_path / "store")
    result = await SerialExecutor(storage=store).execute(
        document=(
            "Unrelated preamble. The customer uploaded service receipt. "
            "Unrelated exhibit text."
        ),
        spec=spec,
        runtime=runtime,
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result.outcome == "complete"
    verdict_call = next(call for call in provider.calls if call["field"]["field_id"] == "verdict")
    assert verdict_call["document_context"] == ""
    assert len(verdict_call["classification_context"]) == 1
    assert "service receipt" in verdict_call["classification_context"][0]["text"]

    artifact = read_replay(store, result.replay_artifact_ref)
    diagnostic = next(
        item for item in artifact.selector_call_diagnostics if item.field_ids == ("verdict",)
    )
    context_payload = diagnostic.classification_context_by_field["verdict"]
    assert isinstance(context_payload, dict)
    assert context_payload["windows"][0]["matched_terms"] == ["service receipt"]


@pytest.mark.asyncio
async def test_many_literal_classification_allows_positive_empty_selection() -> None:
    provider = _ClassificationProvider(verdict="irrelevant", signals=())
    spec = ExtractionSpec.from_pydantic(_DocumentTypeVerdict)

    result = await run_extraction(
        document="Board update notice. A director resigned and a successor was appointed.",
        spec=spec,
        runtime=Runtime(llm=provider),
        policy=ExecutorPolicy(strategy="independent"),
    )

    verdict = result.instances[0].to_pydantic(_DocumentTypeVerdict)

    assert result.outcome == "complete"
    assert verdict.verdict == "irrelevant"
    assert verdict.structural_signals == []


def test_classification_prompt_renders_whole_document_context() -> None:
    provider = _ClassificationProvider()
    spec = ExtractionSpec.from_pydantic(_DocumentTypeVerdict)
    verdict_field = next(field for field in spec.fields if field.field_id == "verdict")
    selector = PydanticAISelector(
        model_id="fake:model",
        provider=provider,
        prompt=ClassificationPrompt(),
    )
    assert verdict_field.strategy_bindings
    candidate_set = (
        verdict_field.strategy_bindings[0]
        .cls()
        .generate(
            field_spec=verdict_field,
            document_view=RuntimeDocumentView.for_text("full document context"),
        )
    )

    rendered = selector.render_prompt(
        verdict_field,
        candidate_set,
        context_pack=ContextPackFactory.with_document("full document context"),
    )

    payload = json.loads(rendered.messages[1].content)
    assert payload["task"] == "document_classification"
    assert payload["document_context"] == "full document context"
    assert payload["retry_feedback"] == []
    assert payload["allowed_evidence_ids"]
    assert "selected_candidate_ids=[]" in rendered.messages[0].content

    rendered_with_retry = selector.render_prompt(
        verdict_field,
        candidate_set,
        context_pack=ContextPack(
            schema_description="schema",
            document_summary="full document context",
            retry_feedback=("previous label combination failed validation",),
        ),
    )
    retry_payload = json.loads(rendered_with_retry.messages[1].content)
    assert retry_payload["retry_feedback"] == [
        "previous label combination failed validation",
    ]
    assert "retry_feedback" in rendered_with_retry.messages[0].content


def test_multi_arm_category_without_selector_binding_fails_at_spec_load() -> None:
    with pytest.raises(SpecError, match="category.selector_binding_required"):
        ExtractionSpec.from_pydantic(_UnboundDocumentTypeVerdict)


class RuntimeDocumentView:
    @staticmethod
    def for_text(text: str) -> Any:
        from extractx.core import SourceRef
        from extractx.source import TextAdapter

        return TextAdapter().adapt(
            text.encode("utf-8"),
            SourceRef(source_id="test", content_hash="sha256:test"),
        )


class ContextPackFactory:
    @staticmethod
    def with_document(text: str) -> ContextPack:
        return ContextPack(schema_description="schema", document_summary=text)
