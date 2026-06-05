"""integration proof that the M8 phase-1 vertical slice runs end-to-end.

covers:

- one explicit regex-bound spec runs end-to-end on `str` input.
- one explicit regex-bound spec runs end-to-end on `bytes` input.
- the result contains exactly one `Instance` whose
  `Evidence`s match the validated values from the landed
  seams.
- the manual seam-F path (no pydantic class registered) runs end-to-end
  with `schema_cls=None`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel

from extractx import (
    ExecutorPolicy,
    Extraction,
    ExtractionSpec,
    Runtime,
    ValueKind,
    extract_field,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.cardinality import Cardinality
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import (
    BudgetSpec,
    Candidate,
    CandidateSet,
    DistanceMetric,
    FieldSpec,
    GroupingPolicy,
    PromptPolicy,
    RenderedPrompt,
    SelectorBinding,
    SourceRef,
    SourceSpan,
    StrategyBinding,
    ValidationBinding,
    ValidationPolicy,
)
from extractx.core.versions import stable_hash
from extractx.execution.executor.serial import SerialExecutor
from extractx.extras.pydantic_ai import (
    BatchSelectorObservationResponse,
    PydanticAIBatchSelector,
    PydanticAISelector,
    SelectorObservationResponse,
)
from extractx.replay import read_replay
from extractx.selection.examples import (
    ExpectedObservation,
    SelectorDemo,
    SelectorDemoSet,
    SelectorPromptPolicy,
)
from extractx.storage import LocalFilesystemStore

# ---------------------------------------------------------------------------
# pydantic-backed spec
# ---------------------------------------------------------------------------


class _Phone(BaseModel):
    """phone field declared with `extract_field` + explicit regex binding.

    `ValueKind.PERSON` is a typing convenience here — the field is a
    string and the slice does not consume the `ValueKind` semantically,
    but `from_pydantic` requires one ValueKind marker per field.
    """

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


def _build_pydantic_spec() -> ExtractionSpec:
    return ExtractionSpec.from_pydantic(_Phone)


class _PhoneWithLlmSelector(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
        selector_binding=SelectorBinding(
            cls=PydanticAISelector,
            params={"model_id": "fake:model"},
        ),
    )


class _PhoneWithPluralStrategies(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"NO-MATCH"},
                kind="candidate",
            ),
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )


class _ContactWithBatchSelector(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="customer phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
        selector_binding=SelectorBinding(
            cls=PydanticAIBatchSelector,
            params={"model_id": "fake:model"},
        ),
    )
    zip_code: Annotated[str, ValueKind.PERSON] = extract_field(
        description="customer ZIP code",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\b\d{5}\b"},
                kind="candidate",
            ),
        ),
        selector_binding=SelectorBinding(
            cls=PydanticAIBatchSelector,
            params={"model_id": "fake:model"},
        ),
    )


class _FirstEvidenceProvider:
    def __init__(self) -> None:
        self.calls: list[RenderedPrompt] = []

    def __call__(
        self,
        rendered: RenderedPrompt,
        output_type: type[SelectorObservationResponse],
    ) -> SelectorObservationResponse:
        self.calls.append(rendered)
        return output_type.model_validate(
            {
                "instance_id": rendered.metadata["allowed_instance_ids"][0],
                "field_id": rendered.metadata["allowed_field_ids"][0],
                "evidence_id": rendered.metadata["allowed_evidence_ids"][0],
                "abstain": False,
                "reason": "selected first bounded evidence id",
            },
        )


class _BatchFirstEvidenceProvider:
    def __init__(self) -> None:
        self.calls: list[RenderedPrompt] = []

    def __call__(
        self,
        rendered: RenderedPrompt,
        output_type: type[BatchSelectorObservationResponse],
    ) -> BatchSelectorObservationResponse:
        self.calls.append(rendered)
        return output_type.model_validate(
            {
                "observations": [
                    {
                        "instance_id": rendered.metadata["allowed_instance_ids"][0],
                        "field_id": field_id,
                        "evidence_id": evidence_ids[0],
                        "abstain": False,
                        "reason": "selected first bounded evidence id",
                    }
                    for field_id, evidence_ids in rendered.metadata[
                        "allowed_evidence_ids_by_field"
                    ].items()
                ],
            },
        )


class _BatchPromptLocalIdProvider:
    def __init__(self) -> None:
        self.calls: list[RenderedPrompt] = []

    def __call__(
        self,
        rendered: RenderedPrompt,
        output_type: type[BatchSelectorObservationResponse],
    ) -> BatchSelectorObservationResponse:
        self.calls.append(rendered)
        prompt_field_ids = rendered.metadata["prompt_field_id_map"]
        prompt_candidate_ids = rendered.metadata["prompt_candidate_id_map_by_field"]
        return output_type.model_validate(
            {
                "observations": [
                    {
                        "instance_id": rendered.metadata["allowed_instance_ids"][0],
                        "field_id": prompt_field_ids[field_id],
                        "evidence_id": next(iter(prompt_candidate_ids[field_id])),
                        "abstain": False,
                        "reason": "selected prompt-local ids",
                    }
                    for field_id in rendered.metadata["allowed_field_ids"]
                ],
            },
        )


class _PromptRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RenderedPrompt]] = []

    def record(self, rendered: RenderedPrompt, *, seam: str) -> str:
        self.calls.append((seam, rendered))
        return "prompt-ref"


class _SelectorPromptAssetResolver:
    def __init__(self, demo_set: SelectorDemoSet) -> None:
        self.demo_set = demo_set

    def resolve_demo_set(self, ref: str) -> SelectorDemoSet:
        assert ref == "phone-demo-set"
        return self.demo_set

    def resolve_instruction(self, ref: str) -> str:
        assert ref == "phone-instruction"
        return "Prefer the primary phone number when multiple phone candidates appear."


def _demo_candidate(candidate_id: str, text: str, start: int) -> Candidate:
    source_ref = SourceRef(source_id="demo-doc", content_hash="sha256:demo")
    return Candidate(
        candidate_id=candidate_id,
        text=text,
        context="Primary 555-1111; backup 555-2222.",
        source_span=SourceSpan(
            source_ref=source_ref,
            text_anchor_space="source_bytes",
            byte_start=start,
            byte_end=start + len(text),
        ),
        entity_type="phone",
    )


def _phone_demo_set() -> SelectorDemoSet:
    return SelectorDemoSet(
        demo_set_id="phone-demo-set",
        version="v1",
        source="test",
        demos=(
            SelectorDemo(
                field_id="phone",
                document_context="Primary 555-1111; backup 555-2222.",
                candidate_set=CandidateSet(
                    field_id="phone",
                    document_id="demo-doc",
                    candidates=(
                        _demo_candidate("demo-primary", "555-1111", 8),
                        _demo_candidate("demo-backup", "555-2222", 25),
                    ),
                    strategy_id="demo",
                ),
                expected=ExpectedObservation(
                    selected_candidate_ids=("demo-primary",),
                    abstain=False,
                ),
                note="Pick the primary phone number over backup.",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_supported_path_str_document() -> None:
    spec = _build_pydantic_spec()
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert isinstance(result, Extraction)
    assert result.strategy == "independent"
    assert result.spec_version == spec.version
    assert result.outcome == "complete"
    assert len(result.instances) == 1

    sole = result.instances[0]
    assert sole.outcome == "complete"
    assert sole.negative_outcomes == ()
    assert len(sole.evidence) == 1

    proposal = sole.evidence[0]
    assert proposal.field_id == "phone"
    assert proposal.raw_value == "555-1234"
    assert proposal.normalized_value == "555-1234"
    assert proposal.proposal_provenance.strategy_id.startswith("regex:")
    assert proposal.proposal_provenance.candidate_id_refs != ()
    assert proposal.proposal_provenance.selector_producer_version is not None

    assert sole.grouping_evidence.discriminators != ()


@pytest.mark.asyncio
async def test_batch_strategy_selects_many_fields_in_one_provider_call() -> None:
    provider = _BatchFirstEvidenceProvider()
    spec = ExtractionSpec.from_pydantic(_ContactWithBatchSelector)
    spec = spec.model_copy(
        update={
            "fields": tuple(
                field.model_copy(
                    update={
                        "selector_binding": field.selector_binding.model_copy(
                            update={"params": {"model_id": "fake:model", "provider": provider}},
                        )
                    },
                )
                for field in spec.fields
            ),
        },
    )
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="batch")

    result = await run_extraction(
        document="Primary 555-1234 backup 555-5678 zip 10001 office 94105.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert result.strategy == "batch"
    assert result.outcome == "complete"
    assert len(provider.calls) == 1
    rendered = provider.calls[0]
    assert rendered.metadata["allowed_field_ids"] == ("phone", "zip_code")
    body = rendered.messages[1].content
    assert body.startswith("<task>")
    assert "<selection_procedure>" in body
    assert "Process each field block independently." in body
    assert "Review only that field's candidate blocks and linked contexts." in body
    assert "<output_rules>" in body
    assert "<output_example>" in body
    assert "Return exactly one observation for each field block." in body
    assert "Do not repeat a field_id." in body
    assert "Never use candidate prefixes like f001 as field_id." in body
    assert "For cardinality many, pick every candidate" in body
    assert "For optional or nullable fields, abstain" in body
    assert "Return observations in the same order as the field blocks." in body
    assert "Batch candidate ids are globally unique" in body
    assert "raw values as evidence_id" in body
    assert '"observations":' in body
    assert '"field_id":"phone"' in body
    assert '"field_id":"zip_code"' in body
    assert '"field_id":"example_field"' not in body
    assert '<field id="phone">' in body
    assert '<field id="zip_code">' in body
    assert "local_id=" not in body
    assert '<candidate id="f001_c001">' in body
    assert '<candidate id="f002_c001">' in body
    assert "context_id: ctx" in body
    assert "allowed_evidence_ids" not in body
    assert rendered.metadata["prompt_field_id_map"] == {
        "phone": "f001",
        "zip_code": "f002",
    }
    prompt_maps = rendered.metadata["prompt_candidate_id_map_by_field"]
    phone_ids = set(prompt_maps["phone"])
    zip_ids = set(prompt_maps["zip_code"])
    assert phone_ids
    assert zip_ids
    assert phone_ids.isdisjoint(zip_ids)
    assert rendered.metadata["prompt_contexts_by_field"]
    schema = rendered.structured_output_schema
    assert schema is not None
    observation_items = schema["properties"]["observations"]
    assert observation_items["minItems"] == 2
    assert observation_items["maxItems"] == 2
    phone_item, zip_item = observation_items["prefixItems"]
    assert phone_item["properties"]["field_id"]["enum"] == ["phone"]
    assert zip_item["properties"]["field_id"]["enum"] == ["zip_code"]
    assert phone_item["properties"]["evidence_id"]["anyOf"][0]["enum"] == [
        "f001_c001",
        "f001_c002",
    ]
    assert zip_item["properties"]["evidence_id"]["anyOf"][0]["enum"] == [
        "f002_c001",
        "f002_c002",
    ]
    assert phone_item["properties"]["selected_candidate_ids"]["items"]["enum"] == [
        "f001_c001",
        "f001_c002",
    ]
    assert zip_item["properties"]["selected_candidate_ids"]["items"]["enum"] == [
        "f002_c001",
        "f002_c002",
    ]
    values = {evidence.field_id: evidence.raw_value for evidence in result.instances[0].evidence}
    assert values == {"phone": "555-1234", "zip_code": "10001"}

    # trace stays minimal on success.
    assert result.trace.events == ()
    assert result.replay_artifact_ref == ""


@pytest.mark.asyncio
async def test_batch_strategy_translates_prompt_local_field_ids() -> None:
    provider = _BatchPromptLocalIdProvider()
    spec = ExtractionSpec.from_pydantic(_ContactWithBatchSelector)
    spec = spec.model_copy(
        update={
            "fields": tuple(
                field.model_copy(
                    update={
                        "selector_binding": field.selector_binding.model_copy(
                            update={"params": {"model_id": "fake:model", "provider": provider}},
                        )
                    },
                )
                for field in spec.fields
            ),
        },
    )

    result = await run_extraction(
        document="Primary 555-1234 backup 555-5678 zip 10001 office 94105.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="batch"),
    )

    assert result.outcome == "complete"
    assert len(provider.calls) == 1
    values = {evidence.field_id: evidence.raw_value for evidence in result.instances[0].evidence}
    assert values == {"phone": "555-1234", "zip_code": "10001"}


@pytest.mark.asyncio
async def test_batch_strategy_packs_soft_fields_under_prompt_budget() -> None:
    provider = _BatchFirstEvidenceProvider()
    spec = ExtractionSpec.from_pydantic(
        _ContactWithBatchSelector,
        prompt_policy=PromptPolicy(selector_prompt_max_chars=7_000),
    )
    spec = spec.model_copy(
        update={
            "fields": tuple(
                field.model_copy(
                    update={
                        "selector_binding": field.selector_binding.model_copy(
                            update={"params": {"model_id": "fake:model", "provider": provider}},
                        )
                    },
                )
                for field in spec.fields
            ),
        },
    )

    result = await run_extraction(
        document="Primary 555-1234 backup 555-5678 zip 10001 office 94105.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="batch"),
    )

    assert result.outcome == "complete"
    assert len(provider.calls) == 2
    assert provider.calls[0].metadata["allowed_field_ids"] == ("phone",)
    assert provider.calls[1].metadata["allowed_field_ids"] == ("zip_code",)
    assert '<candidate id="f001_c001">' in provider.calls[0].messages[1].content
    assert '<candidate id="f002_c001">' in provider.calls[1].messages[1].content
    assert provider.calls[0].structured_output_schema is not None
    assert provider.calls[1].structured_output_schema is not None
    assert provider.calls[0].structured_output_schema["properties"]["observations"][
        "prefixItems"
    ][0]["properties"]["evidence_id"]["anyOf"][0]["enum"] == [
        "f001_c001",
        "f001_c002",
    ]
    assert provider.calls[1].structured_output_schema["properties"]["observations"][
        "prefixItems"
    ][0]["properties"]["evidence_id"]["anyOf"][0]["enum"] == [
        "f002_c001",
        "f002_c002",
    ]
    values = {evidence.field_id: evidence.raw_value for evidence in result.instances[0].evidence}
    assert values == {"phone": "555-1234", "zip_code": "10001"}


@pytest.mark.asyncio
async def test_batch_strategy_fails_fast_when_one_candidate_exceeds_budget() -> None:
    provider = _BatchFirstEvidenceProvider()
    spec = ExtractionSpec.from_pydantic(
        _ContactWithBatchSelector,
        prompt_policy=PromptPolicy(selector_prompt_max_chars=1),
    )
    spec = spec.model_copy(
        update={
            "fields": tuple(
                field.model_copy(
                    update={
                        "selector_binding": field.selector_binding.model_copy(
                            update={"params": {"model_id": "fake:model", "provider": provider}},
                        )
                    },
                )
                for field in spec.fields
            ),
        },
    )

    with pytest.raises(
        InfrastructureError,
        match="selector_prompt_candidate_budget_exceeded",
    ):
        await run_extraction(
            document="Primary 555-1234 backup 555-5678 zip 10001 office 94105.",
            spec=spec,
            runtime=Runtime(),
            policy=ExecutorPolicy(strategy="batch"),
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_runtime_prompt_recorder_captures_executor_constructed_batch_prompt() -> None:
    provider = _BatchFirstEvidenceProvider()
    recorder = _PromptRecorder()
    spec = ExtractionSpec.from_pydantic(_ContactWithBatchSelector)
    runtime = Runtime(llm=provider, prompt_recorder=recorder)
    policy = ExecutorPolicy(strategy="batch")

    result = await run_extraction(
        document="Primary 555-1234 zip 10001.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert result.outcome == "complete"
    assert len(provider.calls) == 1
    assert len(recorder.calls) == 1
    seam, rendered = recorder.calls[0]
    assert seam == "selector.batch"
    assert rendered == provider.calls[0]


def test_deferred_batch_render_applies_demo_policy_to_soft_call_identity() -> None:
    demo_set = _phone_demo_set()
    spec = ExtractionSpec.from_pydantic(_ContactWithBatchSelector)
    runtime_with_demos = Runtime(
        llm=_BatchFirstEvidenceProvider(),
        selector_prompt_assets=_SelectorPromptAssetResolver(demo_set),
        selector_prompt_policies={
            "phone": SelectorPromptPolicy(
                instruction_ref="phone-instruction",
                demo_refs=("phone-demo-set",),
            ),
        },
    )
    runtime_without_demos = Runtime(llm=_BatchFirstEvidenceProvider())
    policy = ExecutorPolicy(strategy="batch", execution_mode="deferred")
    executor = SerialExecutor()

    rendered_with_demos = executor.render_deferred_submission(
        document="Primary 555-1234 zip 10001.",
        spec=spec,
        runtime=runtime_with_demos,
        policy=policy,
    )
    rendered_without_demos = executor.render_deferred_submission(
        document="Primary 555-1234 zip 10001.",
        spec=spec,
        runtime=runtime_without_demos,
        policy=policy,
    )

    assert len(rendered_with_demos.requests) == 1
    request_with_demos = rendered_with_demos.requests[0]
    request_without_demos = rendered_without_demos.requests[0]
    body = request_with_demos.rendered_prompt.messages[1].content
    assert '<field_examples field_id="phone">' in body
    assert '<demo_set id="phone-demo-set" version="v1">' in body
    assert request_with_demos.rendered_prompt.metadata["selector_prompt_policies"]["phone"] == {
        "instruction_ref": "phone-instruction",
        "demo_refs": ["phone-demo-set"],
        "document_context_mode": "full",
        "document_window_overlap_chars": 1000,
        "document_reducer": None,
        "classification_context_binding": None,
    }
    assert request_with_demos.rendered_prompt.metadata["selector_demo_set_hashes_by_field"][
        "phone"
    ]
    assert request_with_demos.soft_call_identity != request_without_demos.soft_call_identity
    assert request_with_demos.request_id != request_without_demos.request_id


@pytest.mark.asyncio
async def test_plural_strategy_bindings_compose_before_selection() -> None:
    spec = ExtractionSpec.from_pydantic(_PhoneWithPluralStrategies)
    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result.outcome == "complete"
    evidence = result.instances[0].evidence[0]
    assert evidence.normalized_value == "555-1234"
    assert evidence.proposal_provenance.strategy_id.startswith("composite:")


@pytest.mark.asyncio
async def test_supported_path_emits_seam_level_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="extractx")
    spec = _build_pydantic_spec()

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )

    events = [
        record.__dict__.get("extractx_event")
        for record in caplog.records
        if record.name.startswith("extractx.")
    ]

    assert result.outcome == "complete"
    assert events == [
        "extraction.started",
        "candidates.generated",
        "selector.started",
        "selector.completed",
        "extraction.completed",
    ]
    candidate_record = next(
        record
        for record in caplog.records
        if record.__dict__.get("extractx_event") == "candidates.generated"
    )
    assert candidate_record.__dict__["field_id"] == "phone"
    assert candidate_record.__dict__["candidate_count"] == 1
    assert "Call us" not in caplog.text


@pytest.mark.asyncio
async def test_llm_selector_path_emits_selector_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="extractx")
    spec = ExtractionSpec.from_pydantic(_PhoneWithLlmSelector)

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=Runtime(llm=_FirstEvidenceProvider()),
        policy=ExecutorPolicy(strategy="independent"),
    )

    events = [
        record.__dict__.get("extractx_event")
        for record in caplog.records
        if record.name.startswith("extractx.")
    ]

    assert result.outcome == "complete"
    assert "selector.started" in events
    assert "selector.completed" in events
    selector_record = next(
        record
        for record in caplog.records
        if record.__dict__.get("extractx_event") == "selector.completed"
    )
    assert selector_record.__dict__["field_id"] == "phone"
    assert selector_record.__dict__["operation"] == "selector"
    assert selector_record.__dict__["candidate_count"] == 1
    assert "555-1234" not in caplog.text


@pytest.mark.asyncio
async def test_supported_path_bytes_document() -> None:
    spec = _build_pydantic_spec()
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result_str = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    result_bytes = await run_extraction(
        document=b"Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    # str → utf8 bytes contract: the two runs share a document_id and
    # an entire `Extraction` payload.
    assert result_str.document_id == result_bytes.document_id
    assert result_str.model_dump(mode="json") == result_bytes.model_dump(mode="json")


@pytest.mark.asyncio
async def test_llm_selector_binding_runs_through_extraction_and_replay(tmp_path: Path) -> None:
    spec = ExtractionSpec.from_pydantic(_PhoneWithLlmSelector)
    provider = _FirstEvidenceProvider()
    runtime = Runtime(llm=provider)
    policy = ExecutorPolicy(strategy="independent")
    store = LocalFilesystemStore(tmp_path)

    result = await SerialExecutor(storage=store).execute(
        document="Call sales at 555-1234 or support at 555-9999.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert result.outcome == "complete"
    assert provider.calls != []
    rendered = provider.calls[0]
    assert rendered.metadata["allowed_field_ids"] == ("phone",)
    assert len(rendered.metadata["allowed_evidence_ids"]) == 2

    evidence = result.instances[0].evidence[0]
    assert evidence.field_id == "phone"
    assert evidence.raw_value == "555-1234"
    assert evidence.normalized_value == "555-1234"

    artifact = read_replay(store, result.replay_artifact_ref)
    assert (
        evidence.proposal_provenance.selector_producer_version
        == artifact.observations[0].producer_version
    )
    assert artifact.observations[0].field_id == "phone"
    assert artifact.observations[0].evidence_id in evidence.proposal_provenance.candidate_id_refs
    assert artifact.observations[0].reason == "selected first bounded evidence id"


@pytest.mark.asyncio
async def test_runtime_selector_demo_policy_reaches_prompt_and_replay_diagnostics(
    tmp_path: Path,
) -> None:
    spec = ExtractionSpec.from_pydantic(_PhoneWithLlmSelector)
    provider = _FirstEvidenceProvider()
    recorder = _PromptRecorder()
    demo_set = _phone_demo_set()
    runtime = Runtime(
        llm=provider,
        prompt_recorder=recorder,
        selector_prompt_assets=_SelectorPromptAssetResolver(demo_set),
        selector_prompt_policies={
            "phone": SelectorPromptPolicy(
                instruction_ref="phone-instruction",
                demo_refs=("phone-demo-set",),
            ),
        },
    )
    store = LocalFilesystemStore(tmp_path)

    result = await SerialExecutor(storage=store).execute(
        document="Primary 555-1234; backup 555-9999.",
        spec=spec,
        runtime=runtime,
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result.outcome == "complete"
    assert len(provider.calls) == 1
    rendered = provider.calls[0]
    body = rendered.messages[1].content
    assert "Prefer the primary phone number" in body
    assert '<demo_set id="phone-demo-set" version="v1">' in body
    assert '<candidate id="demo-primary">' in body
    assert '"selected_candidate_ids":["demo-primary"]' in body
    assert rendered.metadata["selector_prompt_policy"] == {
        "instruction_ref": "phone-instruction",
        "demo_refs": ["phone-demo-set"],
        "document_context_mode": "full",
        "document_window_overlap_chars": 1000,
        "document_reducer": None,
        "classification_context_binding": None,
    }
    assert rendered.metadata["selector_demo_set_hashes"]
    assert recorder.calls == [("selector", rendered)]

    artifact = read_replay(store, result.replay_artifact_ref)
    diagnostic = artifact.selector_call_diagnostics[0]
    assert diagnostic.rendered_prompt_hash == rendered.metadata["rendered_prompt_hash"]
    assert diagnostic.rendered_prompt_ref == "prompt-ref"
    assert diagnostic.model_metadata["selector_prompt_policy"] == {
        "instruction_ref": "phone-instruction",
        "demo_refs": ["phone-demo-set"],
        "document_context_mode": "full",
        "document_window_overlap_chars": 1000,
        "document_reducer": None,
        "classification_context_binding": None,
    }
    assert tuple(diagnostic.model_metadata["selector_demo_set_hashes"]) == rendered.metadata[
        "selector_demo_set_hashes"
    ]


@pytest.mark.asyncio
async def test_llm_selector_binding_requires_runtime_llm_before_extraction() -> None:
    spec = ExtractionSpec.from_pydantic(_PhoneWithLlmSelector)

    with pytest.raises(InfrastructureError, match="selector\\.missing_llm"):
        await run_extraction(
            document="Call us at 555-1234.",
            spec=spec,
            runtime=Runtime(),
            policy=ExecutorPolicy(strategy="independent"),
        )


# ---------------------------------------------------------------------------
# manual spec — no pydantic class registered
# ---------------------------------------------------------------------------


def _identity_normalizer(raw: Any) -> Any:
    return raw


def _build_manual_spec() -> ExtractionSpec:
    """build a manual `ExtractionSpec` carrying an explicit
    `ValidationBinding.normalizer`. no pydantic class is registered;
    seam F runs the manual path with `schema_cls=None`.
    """

    field = FieldSpec(
        field_id="phone",
        description="phone number",
        value_kind=ValueKind.PERSON,
        cardinality=Cardinality.ONE,
        priority=0,
        depends_on=(),
        python_type=str,
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
        validation_binding=ValidationBinding(
            normalizer=_identity_normalizer,
            field_validators=(),
        ),
    )
    fields = (field,)
    # build a stable manual `version` so determinism tests can rerun.
    payload = {
        "manual": True,
        "fields": [
            {
                "field_id": f.field_id,
                "cardinality": f.cardinality.value,
                "value_kind": f.value_kind.name,
            }
            for f in fields
        ],
    }
    version = stable_hash(payload)
    return ExtractionSpec(
        fields=fields,
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="default"),
        ),
        budget=BudgetSpec(),
        version=version,
        source_schema_ref=None,
    )


@pytest.mark.asyncio
async def test_manual_seam_f_path_runs_end_to_end() -> None:
    spec = _build_manual_spec()
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert result.outcome == "complete"
    assert len(result.instances) == 1
    proposal = result.instances[0].evidence[0]
    assert proposal.field_id == "phone"
    assert proposal.raw_value == "555-1234"
    assert proposal.normalized_value == "555-1234"
