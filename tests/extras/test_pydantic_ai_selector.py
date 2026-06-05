"""focused tests for the ADR-0008 pydantic-ai selector preparation."""

from __future__ import annotations

from typing import Any

import pytest

from extractx.candidates.generators.literal_set import LiteralSetCandidateStrategy
from extractx.core import (
    BudgetSpec,
    Candidate,
    CandidateSet,
    Cardinality,
    ClassificationContextSet,
    ClassificationContextWindow,
    ContextPack,
    DistanceMetric,
    ExtractionSpec,
    FieldSpec,
    GroupingPolicy,
    InfrastructureError,
    PromptPolicy,
    ProviderResult,
    RenderedPrompt,
    SourceRef,
    SourceSpan,
    StrategyBinding,
    StructuralStatus,
    UsageEvent,
    ValidationPolicy,
    ValueKind,
)
from extractx.execution.deferred import SoftCallResponse
from extractx.extras.pydantic_ai import (
    PydanticAIBatchSelector,
    PydanticAISelector,
    SelectorObservationResponse,
    SelectorOutputMalformedError,
)
from extractx.proposals.adapter import CardinalitySelectionAdapter
from extractx.selection import SelectorContractError
from extractx.selection.examples import (
    ExpectedObservation,
    SelectorDemo,
    SelectorDemoSet,
    SelectorPromptPolicy,
)


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def _normalized_span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="normalized_text",
        byte_start=start,
        byte_end=end,
    )


def _candidate(candidate_id: str, text: str, start: int) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        text=text,
        context=f"local context for {candidate_id}",
        source_span=_span(start, start + len(text)),
        entity_type="amount",
        structured_payload={"kind": "money", "value": text},
    )


def _candidate_with_context(
    candidate_id: str,
    text: str,
    start: int,
    context: str,
) -> Candidate:
    return _candidate(candidate_id, text, start).model_copy(update={"context": context})


def _candidate_with_normalized_window(
    *,
    candidate_id: str,
    text: str,
    document: str,
    context_start: int,
    context_end: int,
) -> Candidate:
    match_start = document.index(text)
    match_end = match_start + len(text)
    return _candidate(candidate_id, text, match_start).model_copy(
        update={
            "context": document[context_start:context_end],
            "context_span": _normalized_span(context_start, context_end),
            "normalized_span": _normalized_span(match_start, match_end),
        },
    )


def _candidate_set(candidates: tuple[Candidate, ...]) -> CandidateSet:
    return CandidateSet(
        field_id="total",
        document_id="doc-1",
        candidates=candidates,
        strategy_id="regex:abc",
    )


def _field_spec(cardinality: Cardinality = Cardinality.ONE) -> FieldSpec:
    return FieldSpec(
        field_id="total",
        description="invoice total",
        value_kind=ValueKind.register("MONEY"),
        cardinality=cardinality,
        python_type=str,
    )


def _literal_category_field_spec() -> FieldSpec:
    return FieldSpec(
        field_id="document_type",
        description="document type",
        value_kind=ValueKind.CATEGORY,
        cardinality=Cardinality.ONE,
        python_type=str,
        literal_values=("invoice", "receipt", "irrelevant"),
        strategy_bindings=(
            StrategyBinding(
                cls=LiteralSetCandidateStrategy,
                kind="candidate",
            ),
        ),
    )


def _literal_category_candidate_set() -> CandidateSet:
    span = _normalized_span(0, 0)
    return CandidateSet(
        field_id="document_type",
        document_id="doc-1",
        strategy_id="literal_set:test",
        candidates=(
            Candidate(
                candidate_id="literal-invoice",
                text="invoice",
                source_kind="structured",
                source_id="literal_set",
                source_span=span,
                normalized_hint="invoice",
                structured_payload={"literal": "invoice"},
                structural_status=StructuralStatus(
                    passed=True,
                    contract_id="literal_set_strategy_v1",
                ),
            ),
            Candidate(
                candidate_id="literal-receipt",
                text="receipt",
                source_kind="structured",
                source_id="literal_set",
                source_span=span,
                normalized_hint="receipt",
                structured_payload={"literal": "receipt"},
                structural_status=StructuralStatus(
                    passed=True,
                    contract_id="literal_set_strategy_v1",
                ),
            ),
            Candidate(
                candidate_id="literal-irrelevant",
                text="irrelevant",
                source_kind="structured",
                source_id="literal_set",
                source_span=span,
                normalized_hint="irrelevant",
                structured_payload={"literal": "irrelevant"},
                structural_status=StructuralStatus(
                    passed=True,
                    contract_id="literal_set_strategy_v1",
                ),
            ),
        ),
    )


def _context_pack() -> ContextPack:
    return ContextPack(
        schema_description="schema description should not be rendered",
        document_summary="full document summary should not be rendered",
        field_context={"other_field": "cross-field context must stay out"},
    )


def _retry_context_pack() -> ContextPack:
    return ContextPack(
        schema_description="schema description should not be rendered",
        document_summary="full document summary should not be rendered",
        field_context={"other_field": "cross-field context must stay out"},
        retry_feedback=("end_date must be on or after start_date",),
    )


class _FakeProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[RenderedPrompt] = []

    def __call__(
        self,
        rendered: RenderedPrompt,
        output_type: type[SelectorObservationResponse],
    ) -> SelectorObservationResponse:
        self.calls.append(rendered)
        return output_type.model_validate(_strict_selector_payload(self.payload))


def _strict_selector_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": payload["instance_id"],
        "field_id": payload["field_id"],
        "evidence_id": payload.get("evidence_id"),
        "selected_candidate_ids": payload.get("selected_candidate_ids", ()),
        "abstain": payload.get("abstain", False),
        "reason": payload.get("reason"),
    }


class _PromptRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RenderedPrompt]] = []

    def record(self, rendered: RenderedPrompt, *, seam: str) -> str:
        self.calls.append((seam, rendered))
        return "prompt-ref"


class _PromptAssetResolver:
    def __init__(self, demo_set: SelectorDemoSet) -> None:
        self.demo_set = demo_set

    def resolve_demo_set(self, ref: str) -> SelectorDemoSet:
        assert ref == "demo-set-1"
        return self.demo_set

    def resolve_instruction(self, ref: str) -> str:
        assert ref == "instruction-1"
        return "Prefer invoice totals and reject invoice subtotals."


def _demo_set() -> SelectorDemoSet:
    return SelectorDemoSet(
        demo_set_id="demo-set-1",
        version="v1",
        source="test",
        demos=(
            SelectorDemo(
                field_id="total",
                document_context="The total due is $12.00. The subtotal is $9.00.",
                candidate_set=_candidate_set(
                    (
                        _candidate("demo-total", "$12.00", 0),
                        _candidate("demo-subtotal", "$9.00", 20),
                    ),
                ),
                expected=ExpectedObservation(
                    selected_candidate_ids=("demo-total",),
                    abstain=False,
                ),
                note="Select the total due, not subtotal.",
            ),
        ),
    )


def test_fake_provider_valid_id_returns_observation_and_selection() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c001",
            "abstain": False,
            "reason": "best bounded candidate",
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    observation = selector.select_observation(_field_spec(), cset, _context_pack())
    selection = selector.select(_field_spec(), cset, _context_pack())

    assert observation.evidence_id == "cand-1"
    assert observation.instance_id == "inst_0"
    assert selection.outcome == "SELECTED"
    assert selection.selected_candidate_ids == ("cand-1",)
    assert selection.reason == "best bounded candidate"

    rendered = provider.calls[0]
    assert rendered.metadata["allowed_field_ids"] == ("total",)
    assert rendered.metadata["allowed_instance_ids"] == ("inst_0",)
    assert rendered.metadata["allowed_evidence_ids"] == ("c001",)
    assert rendered.metadata["canonical_allowed_evidence_ids"] == ("cand-1",)
    assert rendered.metadata["prompt_candidate_id_map"] == {"c001": "cand-1"}
    assert rendered.metadata["temperature"] == 0
    assert rendered.metadata["seed"] == 0
    schema = rendered.structured_output_schema
    assert schema is not None
    properties = schema["properties"]
    assert properties["field_id"]["enum"] == ["total"]
    assert properties["instance_id"]["enum"] == ["inst_0"]
    assert properties["evidence_id"]["anyOf"][0]["enum"] == ["c001"]
    body = rendered.messages[1].content
    assert "schema description should not be rendered" not in body
    assert "full document summary should not be rendered" not in body
    assert "cross-field context must stay out" not in body
    assert body.startswith("<task>")
    assert "<selection_procedure>" in body
    assert "Review only this field's candidate blocks and linked contexts." in body
    assert "never return the value text" in body
    assert "<output_rules>" in body
    assert "<output_example>" in body
    assert "raw values as evidence_id" in body
    assert '"field_id":"example_field"' in body
    assert '"evidence_id":"c001"' in body
    assert '<field id="total">' in body
    assert '<candidate id="c001">' in body
    assert "text: $12.00" in body
    assert "structured_payload_keys: kind, value" in body
    assert "allowed_evidence_ids" not in body
    assert '<candidate id="cand-1">' not in body
    assert "raw value" in rendered.messages[0].content
    assert "candidate blocks" in rendered.messages[0].content
    assert "optional or nullable fields" in rendered.messages[0].content
    assert 'returning "None", "null", "N/A"' in rendered.messages[0].content
    assert "candidate_id" in properties["evidence_id"]["description"]
    assert "raw values" in properties["evidence_id"]["description"]


def test_selector_prompt_renders_resolved_demo_assets_and_hashes_them() -> None:
    demo_set = _demo_set()
    selector = PydanticAISelector(
        model_id="fake:model",
        provider=_FakeProvider(
            {
                "instance_id": "inst_0",
                "field_id": "total",
                "evidence_id": "c001",
                "abstain": False,
                "reason": "best bounded candidate",
            },
        ),
        prompt_asset_resolver=_PromptAssetResolver(demo_set),
        prompt_policy=SelectorPromptPolicy(
            instruction_ref="instruction-1",
            demo_refs=("demo-set-1",),
        ),
    )

    rendered = selector.render_prompt(
        _field_spec(),
        _candidate_set((_candidate("cand-1", "$12.00", 0),)),
        _context_pack(),
    )

    body = rendered.messages[1].content
    assert "<selector_instruction>" in body
    assert "Prefer invoice totals" in body
    assert '<demo_set id="demo-set-1" version="v1">' in body
    assert '<demo index="1" field_id="total">' in body
    assert "Select the total due, not subtotal." in body
    assert '<candidate id="demo-total">' in body
    assert '"selected_candidate_ids":["demo-total"]' in body
    assert rendered.metadata["selector_prompt_policy"] == {
        "instruction_ref": "instruction-1",
        "demo_refs": ["demo-set-1"],
        "document_context_mode": "full",
        "document_window_overlap_chars": 1000,
        "document_reducer": None,
        "classification_context_binding": None,
    }
    assert rendered.metadata["selector_demo_set_hashes"]


def test_selector_prompt_policy_requires_resolver_for_refs() -> None:
    selector = PydanticAISelector(
        model_id="fake:model",
        provider=_FakeProvider(
            {
                "instance_id": "inst_0",
                "field_id": "total",
                "evidence_id": "c001",
            },
        ),
        prompt_policy=SelectorPromptPolicy(demo_refs=("demo-set-1",)),
    )

    with pytest.raises(InfrastructureError, match="selector_prompt_assets.missing_resolver"):
        selector.render_prompt(
            _field_spec(),
            _candidate_set((_candidate("cand-1", "$12.00", 0),)),
            _context_pack(),
        )


def test_retry_feedback_is_rendered_for_llm_selector_prompt() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c001",
            "abstain": False,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    selector.select(_field_spec(), cset, _retry_context_pack())

    rendered = provider.calls[0]
    body = rendered.messages[1].content
    assert "<retry_feedback>" in body
    assert "end_date must be on or after start_date" in body
    assert "validator feedback" in rendered.messages[0].content
    assert "choose again from the candidate blocks" in rendered.messages[0].content


def test_retry_feedback_is_rendered_for_batch_selector_prompt() -> None:
    selector = PydanticAIBatchSelector(model_id="fake:model")
    spec = ExtractionSpec(
        fields=(_field_spec(),),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    rendered = selector.render_prompt(
        spec=spec,
        candidate_sets=(cset,),
        context_pack=_retry_context_pack(),
    )

    body = rendered.messages[1].content
    assert "<retry_feedback>" in body
    assert "end_date must be on or after start_date" in body


def test_batch_selector_literal_category_prompt_includes_document_context() -> None:
    selector = PydanticAIBatchSelector(model_id="fake:model")
    field_spec = _literal_category_field_spec()
    spec = ExtractionSpec(
        fields=(field_spec,),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )

    rendered = selector.render_prompt(
        spec=spec,
        candidate_sets=(_literal_category_candidate_set(),),
        context_pack=ContextPack(
            schema_description="schema description should not be rendered",
            document_summary="This document is an invoice for $12.00.",
        ),
    )

    body = rendered.messages[1].content
    assert '<field id="document_type">' in body
    assert "<document_context>" in body
    assert "This document is an invoice for $12.00." in body
    assert '<candidate id="f001_c001">' in body
    assert "text: invoice" in body
    assert "text: receipt" in body
    assert "text: irrelevant" in body
    assert rendered.metadata["canonical_allowed_evidence_ids_by_field"][
        "document_type"
    ] == (
        "literal-invoice",
        "literal-receipt",
        "literal-irrelevant",
    )


def test_batch_selector_literal_category_prompt_requires_document_context() -> None:
    selector = PydanticAIBatchSelector(model_id="fake:model")
    field_spec = _literal_category_field_spec()
    spec = ExtractionSpec(
        fields=(field_spec,),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )

    with pytest.raises(
        SelectorContractError,
        match="document-level classification requires document context",
    ):
        selector.render_prompt(
            spec=spec,
            candidate_sets=(_literal_category_candidate_set(),),
            context_pack=ContextPack(schema_description="", document_summary=""),
        )


def test_batch_selector_literal_category_prompt_accepts_classification_context() -> None:
    selector = PydanticAIBatchSelector(model_id="fake:model")
    field_spec = _literal_category_field_spec()
    spec = ExtractionSpec(
        fields=(field_spec,),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )
    context_set = ClassificationContextSet(
        field_id="document_type",
        document_id="doc-1",
        strategy_id="regex_window_classification_context:v1",
        windows=(
            ClassificationContextWindow(
                window_id="ctx-1",
                field_id="document_type",
                text="The document says invoice INV-1001.",
                source_span=_normalized_span(0, 33),
                matched_terms=("invoice",),
                strategy_id="regex_window_classification_context:v1",
                rank=1,
            ),
        ),
    )

    rendered = selector.render_prompt(
        spec=spec,
        candidate_sets=(_literal_category_candidate_set(),),
        context_pack=ContextPack(
            schema_description="",
            document_summary="",
            classification_context_by_field={"document_type": context_set},
        ),
    )

    body = rendered.messages[1].content
    assert "<document_context>" not in body
    assert "<classification_context>" in body
    assert '<context_window id="ctx-1"' in body
    assert "The document says invoice INV-1001." in body
    assert rendered.metadata["classification_context_by_field"]["document_type"][
        "windows"
    ][0]["window_id"] == "ctx-1"


def test_batch_selector_ordinary_field_prompt_omits_document_context() -> None:
    selector = PydanticAIBatchSelector(model_id="fake:model")
    spec = ExtractionSpec(
        fields=(_field_spec(),),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )

    rendered = selector.render_prompt(
        spec=spec,
        candidate_sets=(_candidate_set((_candidate("cand-1", "$12.00", 0),)),),
        context_pack=_context_pack(),
    )

    body = rendered.messages[1].content
    assert "<document_context>" not in body
    assert "full document summary should not be rendered" not in body


def test_batch_selector_prompt_renders_field_scoped_demo_assets() -> None:
    demo_set = _demo_set()
    selector = PydanticAIBatchSelector(
        model_id="fake:model",
        prompt_asset_resolver=_PromptAssetResolver(demo_set),
        prompt_policies={
            "total": SelectorPromptPolicy(
                instruction_ref="instruction-1",
                demo_refs=("demo-set-1",),
            ),
        },
    )
    spec = ExtractionSpec(
        fields=(_field_spec(),),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    rendered = selector.render_prompt(
        spec=spec,
        candidate_sets=(cset,),
        context_pack=_context_pack(),
    )

    body = rendered.messages[1].content
    assert "<selector_instructions>" in body
    assert '<instruction field_id="total">' in body
    assert "Prefer invoice totals" in body
    assert "<selector_worked_examples>" in body
    assert '<field_examples field_id="total">' in body
    assert '<demo_set id="demo-set-1" version="v1">' in body
    assert '"selected_candidate_ids":["demo-total"]' in body
    assert rendered.metadata["selector_demo_set_hashes_by_field"]["total"]
    assert rendered.metadata["selector_prompt_policies"]["total"] == {
        "instruction_ref": "instruction-1",
        "demo_refs": ["demo-set-1"],
        "document_context_mode": "full",
        "document_window_overlap_chars": 1000,
        "document_reducer": None,
        "classification_context_binding": None,
    }


def test_selector_prompt_interns_duplicate_contexts_without_merging_candidates() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c002",
            "abstain": False,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    shared_context = "The invoice total is $12.00 and the subtotal is $10.00."
    cset = _candidate_set(
        (
            _candidate_with_context("cand-1", "$10.00", 0, shared_context),
            _candidate_with_context("cand-2", "$12.00", 10, shared_context),
        ),
    )

    selection = selector.select(_field_spec(), cset, _context_pack())

    rendered = provider.calls[0]
    body = rendered.messages[1].content
    assert selection.selected_candidate_ids == ("cand-2",)
    assert body.count(shared_context) == 1
    assert '<context id="ctx001">' in body
    assert '<candidate id="c001">' in body
    assert '<candidate id="c002">' in body
    assert body.count("context_id: ctx001") == 2
    assert rendered.metadata["prompt_contexts_by_field"]["total"]["ctx001"][
        "candidate_ids"
    ] == ["c001", "c002"]


def test_selector_prompt_interns_substring_contexts_to_longer_context() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c001",
            "abstain": False,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    short_context = "invoice total is $12.00"
    long_context = f"The final {short_context} after tax."
    cset = _candidate_set(
        (
            _candidate_with_context("cand-1", "$12.00", 0, short_context),
            _candidate_with_context("cand-2", "$12.00", 10, long_context),
        ),
    )

    selector.select(_field_spec(), cset, _context_pack())

    rendered = provider.calls[0]
    body = rendered.messages[1].content
    assert body.count("<context id=") == 1
    assert short_context in body
    assert long_context in body
    assert body.count("context_id: ctx001") == 2


def test_selector_prompt_merges_overlapping_span_contexts_with_inline_anchors() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c002",
            "abstain": False,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    document = "aaaa 1111 bbbb cccc 2222 dddd"
    cset = _candidate_set(
        (
            _candidate_with_normalized_window(
                candidate_id="cand-1",
                text="1111",
                document=document,
                context_start=0,
                context_end=19,
            ),
            _candidate_with_normalized_window(
                candidate_id="cand-2",
                text="2222",
                document=document,
                context_start=10,
                context_end=len(document),
            ),
        ),
    )

    selection = selector.select(_field_spec(), cset, _context_pack())

    rendered = provider.calls[0]
    body = rendered.messages[1].content
    assert selection.selected_candidate_ids == ("cand-2",)
    assert body.count("<context id=") == 1
    assert '<context id="ctx001" source_span="0:29">' in body
    assert '<cand id="c001">1111</cand>' in body
    assert '<cand id="c002">2222</cand>' in body
    assert body.count("context_id: ctx001") == 2
    assert "local_span: 5:9" in body
    assert "local_span: 20:24" in body
    assert '<candidate id="cand-1">' not in body
    assert '<candidate id="cand-2">' not in body
    assert rendered.metadata["prompt_candidate_id_map"] == {
        "c001": "cand-1",
        "c002": "cand-2",
    }
    context = rendered.metadata["prompt_contexts_by_field"]["total"]["ctx001"]
    assert context["candidate_ids"] == ["c001", "c002"]
    assert context["byte_start"] == 0
    assert context["byte_end"] == len(document)


def test_selector_seed_can_be_explicitly_disabled_for_provider_compatibility() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c001",
            "abstain": False,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider, seed=None)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    selector.select(_field_spec(), cset, _context_pack())

    assert provider.calls[0].metadata["seed"] is None


def test_provider_result_usage_event_is_captured_by_selector() -> None:
    usage = UsageEvent(
        producer_version="soft:test",
        operation="selector",
        field_id="total",
        instance_id="inst_0",
        model_id="fake:model",
        input_tokens=11,
        output_tokens=3,
        total_tokens=14,
        timestamp_ns=123,
        raw_usage={"input_tokens": 11, "output_tokens": 3},
    )

    def provider(
        rendered: RenderedPrompt,
        output_type: type[SelectorObservationResponse],
    ) -> ProviderResult[SelectorObservationResponse]:
        return ProviderResult(
            output=output_type.model_validate(
                {
                    "instance_id": rendered.metadata["allowed_instance_ids"][0],
                    "field_id": "total",
                    "evidence_id": "c001",
                    "selected_candidate_ids": ("c001",),
                    "abstain": False,
                    "reason": None,
                },
            ),
            usage_event=usage,
        )

    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    selector.select(_field_spec(), cset, _context_pack())

    assert selector.last_usage_event == usage
    diagnostic = selector.last_call_diagnostic
    assert diagnostic is not None
    assert diagnostic["rendered_prompt_hash"]
    assert diagnostic["allowed_evidence_ids"] == ("c001",)
    assert diagnostic["allowed_evidence_ids_by_field"] == {"total": ("c001",)}
    assert diagnostic["prompt_candidate_id_map"] == {"c001": "cand-1"}
    assert diagnostic["prompt_candidate_id_map_by_field"] == {"total": {"c001": "cand-1"}}
    assert diagnostic["selector_response_before_translation_hash"]
    assert diagnostic["selector_response_after_translation_hash"]
    assert diagnostic["usage_event"] == usage


def test_selector_can_render_soft_call_request_for_immediate_adapter_path() -> None:
    selector = PydanticAISelector(model_id="fake:model")
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))
    rendered = selector.render_prompt(_field_spec(), cset, _context_pack())

    request = selector.render_soft_call_request(
        rendered,
        field_id="total",
        instance_id="inst_0",
        spec_hash="spec-v1",
    )

    assert request.output_model_ref == "extractx.pydantic_ai.selector_response.v1"
    assert request.rendered_prompt.messages == rendered.messages
    assert request.rendered_prompt.structured_output_schema == rendered.structured_output_schema
    assert request.routing.field_id == "total"
    assert request.routing.instance_id == "inst_0"
    assert request.request_id


def test_batch_selector_can_render_soft_call_request_for_immediate_adapter_path() -> None:
    selector = PydanticAIBatchSelector(model_id="fake:model")
    spec = ExtractionSpec(
        fields=(_field_spec(),),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))
    rendered = selector.render_prompt(
        spec=spec,
        candidate_sets=(cset,),
        context_pack=_context_pack(),
    )

    request = selector.render_soft_call_request(rendered, spec_hash=spec.version)

    assert request.output_model_ref == "extractx.pydantic_ai.batch_selector_response.v1"
    assert request.rendered_prompt.messages == rendered.messages
    assert request.rendered_prompt.structured_output_schema == rendered.structured_output_schema
    assert request.request_id


def test_batch_selector_coalesces_split_many_field_soft_call_response() -> None:
    selector = PydanticAIBatchSelector(model_id="fake:model")
    field_spec = _field_spec(cardinality=Cardinality.MANY)
    spec = ExtractionSpec(
        fields=(field_spec,),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )
    cset = _candidate_set(
        (
            _candidate("cand-1", "$12.00", 0),
            _candidate("cand-2", "$14.00", 10),
        ),
    )
    rendered = selector.render_prompt(
        spec=spec,
        candidate_sets=(cset,),
        context_pack=_context_pack(),
    )
    request = selector.render_soft_call_request(rendered, spec_hash=spec.version)

    observations = selector.observations_from_soft_call_response(
        request=request,
        response=SoftCallResponse(
            request_id=request.request_id,
            response_payload={
                "observations": [
                    {
                        "instance_id": "inst_0",
                        "field_id": "f001",
                        "evidence_id": "f001_c001",
                        "selected_candidate_ids": ["f001_c001"],
                        "abstain": False,
                        "reason": "first label",
                    },
                    {
                        "instance_id": "inst_0",
                        "field_id": "f001",
                        "evidence_id": "f001_c002",
                        "selected_candidate_ids": ["f001_c002"],
                        "abstain": False,
                        "reason": "second label",
                    },
                ],
            },
        ),
        spec=spec,
        candidate_sets=(cset,),
    )

    assert len(observations) == 1
    assert observations[0].field_id == "total"
    assert observations[0].selected_candidate_ids == ("cand-1", "cand-2")
    assert observations[0].evidence_id == "cand-1"
    assert observations[0].abstain is False


def test_prompt_recorder_captures_rendered_selector_prompt_before_provider_call() -> None:
    recorder = _PromptRecorder()
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c001",
            "abstain": False,
        },
    )
    selector = PydanticAISelector(
        model_id="fake:model",
        provider=provider,
        prompt_recorder=recorder,
    )
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    selector.select(_field_spec(), cset, _context_pack())

    assert len(recorder.calls) == 1
    seam, rendered = recorder.calls[0]
    assert seam == "selector"
    assert rendered == provider.calls[0]


def test_fake_provider_abstain_returns_abstained_selection() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": None,
            "abstain": True,
            "reason": "not enough evidence",
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    selection = selector.select(_field_spec(), cset, _context_pack())

    assert selection.outcome == "ABSTAINED"
    assert selection.selected_candidate_ids == ()
    assert selection.reason == "not enough evidence"


def test_fake_provider_many_field_accepts_positive_empty_selection() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "selected_candidate_ids": [],
            "abstain": False,
            "reason": "no labels apply",
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    selection = selector.select(
        _field_spec(Cardinality.MANY),
        cset,
        _context_pack(),
    )

    assert selection.outcome == "SELECTED"
    assert selection.selected_candidate_ids == ()
    assert selection.abstain is False


def test_fake_provider_one_field_rejects_positive_empty_selection() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "selected_candidate_ids": [],
            "abstain": False,
            "reason": "no labels apply",
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    with pytest.raises(
        SelectorOutputMalformedError,
        match="abstain=False requires bounded selected ids",
    ):
        selector.select(_field_spec(Cardinality.ONE), cset, _context_pack())


def test_fake_provider_fabricated_evidence_id_raises_selector_contract_error() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "ghost",
            "abstain": False,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    with pytest.raises(SelectorContractError):
        selector.select_observation(_field_spec(), cset, _context_pack())


def test_observation_construction_wraps_validation_error_as_selector_contract_error() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c003",
            "selected_candidate_ids": ("c001", "c002"),
            "abstain": False,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set(
        (
            _candidate("cand-1", "$12.00", 0),
            _candidate("cand-2", "$13.00", 10),
            _candidate("cand-3", "$14.00", 20),
        ),
    )

    with pytest.raises(SelectorContractError, match="failed Observation contract"):
        selector.select(_field_spec(), cset, _context_pack())


def test_single_pick_normalization_forces_evidence_id_to_selected_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c002",
            "selected_candidate_ids": ("c001",),
            "abstain": False,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set(
        (
            _candidate("cand-1", "$12.00", 0),
            _candidate("cand-2", "$13.00", 10),
        ),
    )

    observation = selector.select(_field_spec(), cset, _context_pack())

    assert observation.evidence_id == "cand-1"
    assert observation.selected_candidate_ids == ("cand-1",)
    assert "extractx.selector.evidence_id_normalized" in caplog.text


def test_single_pick_with_null_evidence_id_mirrors_selected_id() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": None,
            "selected_candidate_ids": ("c001",),
            "abstain": False,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    observation = selector.select(_field_spec(), cset, _context_pack())

    assert observation.evidence_id == "cand-1"
    assert observation.selected_candidate_ids == ("cand-1",)


def test_fake_provider_fabricated_instance_or_field_raises_selector_contract_error() -> None:
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))
    for payload in (
        {
            "instance_id": "not-bounded",
            "field_id": "total",
            "evidence_id": "c001",
            "abstain": False,
        },
        {
            "instance_id": "inst_0",
            "field_id": "not-total",
            "evidence_id": "c001",
            "abstain": False,
        },
    ):
        selector = PydanticAISelector(model_id="fake:model", provider=_FakeProvider(payload))
        with pytest.raises(SelectorContractError):
            selector.select_observation(_field_spec(), cset, _context_pack())


def test_malformed_abstain_shape_raises_output_malformed() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c001",
            "abstain": True,
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))

    with pytest.raises(SelectorOutputMalformedError):
        selector.select_observation(_field_spec(), cset, _context_pack())


def test_response_schema_has_no_llm_authored_value_span_or_domain_fields() -> None:
    forbidden = {
        "value",
        "raw_value",
        "normalized_value",
        "source_span",
        "evidence_span",
        "evidence_spans",
        "span",
        "domain_id",
        "business_entity_id",
        "return_id",
    }
    assert forbidden.isdisjoint(SelectorObservationResponse.model_fields)


def test_reason_is_diagnostic_only_and_not_projected_into_proposed_field() -> None:
    provider = _FakeProvider(
        {
            "instance_id": "inst_0",
            "field_id": "total",
            "evidence_id": "c001",
            "abstain": False,
            "reason": "do not copy this into evidence",
        },
    )
    selector = PydanticAISelector(model_id="fake:model", provider=provider)
    field_spec = _field_spec()
    cset = _candidate_set((_candidate("cand-1", "$12.00", 0),))
    selection = selector.select(field_spec, cset, _context_pack())

    result = CardinalitySelectionAdapter().adapt(selection, cset, field_spec)

    assert isinstance(result, tuple)
    proposed = result[0]
    assert proposed.raw_value == "$12.00"
    assert proposed.evidence_text == "$12.00"
    assert proposed.source_span == cset.candidates[0].source_span
    assert proposed.evidence_spans == ()
    assert proposed.normalized_hint is None
    assert proposed.raw_value != selection.reason
    assert proposed.evidence_text != selection.reason
