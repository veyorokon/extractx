"""ADR-0028 deferred soft-call kernel tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel

from extractx import (
    ExecutorPolicy,
    ExtractionSpec,
    Runtime,
    ValueKind,
    collect_deferred_submission,
    deferred_results_for_document,
    extract_field,
    render_deferred_submission,
    run_extraction,
    submit_deferred_aggregate,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import (
    Message,
    PromptPolicy,
    RenderedPrompt,
    SelectorBinding,
    StrategyBinding,
)
from extractx.execution.deferred import (
    DeferredHandle,
    DeferredPending,
    DeferredProvider,
    DeferredResults,
    DeferredSubmission,
    DeferredSubmissionManifest,
    FakeDeferredProvider,
    RenderedDeferredSubmission,
    SoftCallRequest,
    SoftCallResponse,
    SoftCallRouting,
    adapt_soft_call_response,
    deferred_submission_manifest_fingerprint,
    make_soft_call_request_id,
    usage_event_from_response,
    validate_deferred_collect_contract,
)
from extractx.extras.pydantic_ai import PydanticAIBatchSelector
from extractx.selection import (
    DocumentClassificationReducerPolicy,
    SelectorPromptPolicy,
)


class _Output(BaseModel):
    value: str


class _DeferredPhone(BaseModel):
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
            cls=PydanticAIBatchSelector,
            params={"model_id": "fake:model"},
        ),
    )


class _DeferredDocumentCategory(BaseModel):
    verdict: Annotated[
        Literal["receipt", "review", "irrelevant"],
        ValueKind.CATEGORY,
    ] = extract_field(
        description="document category verdict",
        selector_binding=SelectorBinding(
            cls=PydanticAIBatchSelector,
            params={"model_id": "fake:model"},
        ),
    )
    document_signals: Annotated[
        list[Literal["total_present", "tax_present", "vendor_present"]],
        ValueKind.CATEGORY,
    ] = extract_field(
        description="bounded structural signals",
        selector_binding=SelectorBinding(
            cls=PydanticAIBatchSelector,
            params={"model_id": "fake:model"},
        ),
    )


class _CapturingDeferredProvider:
    def __init__(self) -> None:
        self.requests: tuple[SoftCallRequest, ...] = ()

    async def submit(
        self,
        requests: tuple[SoftCallRequest, ...],
    ) -> DeferredHandle:
        self.requests = requests
        return DeferredHandle(
            provider="fake",
            provider_batch_id="fake-batch-submit",
            submitted_at=datetime(2026, 5, 6, tzinfo=UTC),
            request_count=len(requests),
        )

    async def poll(self, handle: DeferredHandle) -> DeferredPending | DeferredResults:
        return DeferredPending(handle=handle, checked_at=datetime(2026, 5, 6, tzinfo=UTC))

    async def cancel(self, handle: DeferredHandle) -> None:
        del handle


def _phone_response(
    request: SoftCallRequest,
    *,
    raw_usage: dict[str, int] | None = None,
) -> SoftCallResponse:
    prompt_field_ids = request.rendered_prompt.metadata["prompt_field_id_map"]
    prompt_candidate_ids = request.rendered_prompt.metadata["prompt_candidate_id_map_by_field"]
    field_id = prompt_field_ids["phone"]
    candidate_id = next(iter(prompt_candidate_ids["phone"]))
    return SoftCallResponse(
        request_id=request.request_id,
        response_payload={
            "observations": [
                {
                    "instance_id": "inst_0",
                    "field_id": field_id,
                    "evidence_id": candidate_id,
                    "selected_candidate_ids": [candidate_id],
                    "abstain": False,
                    "reason": "selected first phone",
                },
            ],
        },
        raw_usage=raw_usage,
        raw_response_metadata={"provider": "fake", "model": "fake:model"},
    )


def _literal_category_response(
    request: SoftCallRequest,
    *,
    canonical_literals: tuple[str, ...],
) -> SoftCallResponse:
    prompt_field_ids = request.rendered_prompt.metadata["prompt_field_id_map"]
    prompt_candidate_ids = request.rendered_prompt.metadata["prompt_candidate_id_map_by_field"]
    canonical_field_id = request.rendered_prompt.metadata["allowed_field_ids"][0]
    local_field_id = prompt_field_ids[canonical_field_id]
    canonical_candidates = request.rendered_prompt.metadata[
        "canonical_allowed_evidence_ids_by_field"
    ][canonical_field_id]
    literal_order = (
        ("receipt", "review", "irrelevant")
        if canonical_field_id == "verdict"
        else ("total_present", "tax_present", "vendor_present")
    )
    local_candidate_ids: list[str] = []
    for literal in canonical_literals:
        canonical_id = canonical_candidates[literal_order.index(literal)]
        local_candidate_ids.append(
            next(
                local_id
                for local_id, mapped in prompt_candidate_ids[canonical_field_id].items()
                if mapped == canonical_id
            ),
        )
    return SoftCallResponse(
        request_id=request.request_id,
        response_payload={
            "observations": [
                {
                    "instance_id": "inst_0",
                    "field_id": local_field_id,
                    "evidence_id": local_candidate_ids[0] if local_candidate_ids else None,
                    "selected_candidate_ids": local_candidate_ids,
                    "abstain": False,
                    "reason": f"selected {canonical_literals!r}",
                },
            ],
        },
    )


def _rendered_prompt() -> RenderedPrompt:
    return RenderedPrompt(
        messages=(Message(role="user", content="return a value"),),
        metadata={
            "model_id": "fake-model",
            "producer_version": "fake-model|prompt|code",
            "prompt_template_id": "extractx.selection.fake.v1",
        },
    )


def _request(*, routing: SoftCallRouting | None = None) -> SoftCallRequest:
    routing = routing or SoftCallRouting(field_id="answer", instance_id="inst_0")
    request_id = make_soft_call_request_id(
        soft_call_identity="soft-call-1",
        spec_hash="spec-hash",
        output_model_ref="test.output.v1",
        routing=routing,
    )
    return SoftCallRequest(
        request_id=request_id,
        rendered_prompt=_rendered_prompt(),
        output_model_ref="test.output.v1",
        soft_call_identity="soft-call-1",
        structured_output_mode="tool_call",
        routing=routing,
    )


def test_soft_call_request_id_is_deterministic_and_routing_sensitive() -> None:
    base = SoftCallRouting(
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        field_id="answer",
        instance_id="inst_0",
    )
    same = SoftCallRouting(
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        field_id="answer",
        instance_id="inst_0",
    )
    different_shard = SoftCallRouting(
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        field_id="answer",
        instance_id="inst_0",
        shard_index=1,
    )
    different_document = SoftCallRouting(
        document_id="doc-2",
        document_content_hash="doc-hash-2",
        field_id="answer",
        instance_id="inst_0",
    )
    different_window = SoftCallRouting(
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        field_id="answer",
        instance_id="inst_0",
        window_index=1,
        window_count=2,
    )

    base_id = make_soft_call_request_id(
        soft_call_identity="soft-call-1",
        spec_hash="spec-hash",
        output_model_ref="test.output.v1",
        routing=base,
    )

    assert base_id == make_soft_call_request_id(
        soft_call_identity="soft-call-1",
        spec_hash="spec-hash",
        output_model_ref="test.output.v1",
        routing=same,
    )
    assert base_id != make_soft_call_request_id(
        soft_call_identity="soft-call-1",
        spec_hash="spec-hash",
        output_model_ref="test.output.v1",
        routing=different_shard,
    )
    assert base_id != make_soft_call_request_id(
        soft_call_identity="soft-call-1",
        spec_hash="spec-hash",
        output_model_ref="test.output.v1",
        routing=different_document,
    )
    assert base_id != make_soft_call_request_id(
        soft_call_identity="soft-call-1",
        spec_hash="spec-hash",
        output_model_ref="test.output.v1",
        routing=different_window,
    )


def test_deferred_submission_manifest_is_json_round_trippable() -> None:
    request = _request().model_copy(
        update={
            "rendered_prompt": _rendered_prompt().model_copy(
                update={
                    "structured_output_schema": {
                        "type": "object",
                        "enum_values": ("f001_c001", "f001_c002"),
                    },
                    "metadata": {
                        "model_id": "fake-model",
                        "allowed_field_ids": ("phone",),
                        "canonical_allowed_evidence_ids_by_field": {
                            "phone": ("candidate-1", "candidate-2"),
                        },
                    },
                },
            ),
        },
    )
    handle = DeferredHandle(
        provider="fake",
        provider_batch_id="fake-batch-1",
        submitted_at=datetime(2026, 5, 6, tzinfo=UTC),
        request_count=1,
    )
    fingerprint = deferred_submission_manifest_fingerprint(
        spec_hash="spec-hash",
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        handle=handle,
        requests=(request,),
        provider_request_ids={request.request_id: "provider-1"},
    )
    manifest = DeferredSubmissionManifest(
        spec_hash="spec-hash",
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        handle=handle,
        requests=(request,),
        provider_request_ids={request.request_id: "provider-1"},
        manifest_fingerprint=fingerprint,
    )

    dumped = manifest.model_dump_json()
    loaded = DeferredSubmissionManifest.model_validate_json(dumped)

    assert loaded == manifest
    assert loaded.requests[0] == request
    assert loaded.manifest_fingerprint == fingerprint


def test_adapt_soft_call_response_validates_into_provider_result() -> None:
    request = _request()
    response = SoftCallResponse(
        request_id=request.request_id,
        response_payload={"value": "ok"},
        raw_usage={"input_tokens": 10},
        raw_response_metadata={"provider": "fake"},
    )

    result = adapt_soft_call_response(request, response, output_model=_Output)

    assert result.output == _Output(value="ok")
    assert result.usage_event is not None
    assert result.usage_event.operation == "selector"
    assert result.usage_event.field_id == "answer"
    assert result.usage_event.raw_usage == {"input_tokens": 10}
    assert result.usage_event.raw_response_metadata == {"provider": "fake"}


def test_usage_event_from_response_is_public_deferred_usage_seam() -> None:
    request = _request()
    response = SoftCallResponse(
        request_id=request.request_id,
        response_payload={"value": "ok"},
        raw_usage={"input_tokens": 10, "output_tokens": 4},
        raw_response_metadata={"provider": "fake"},
    )

    event = usage_event_from_response(request, response)

    assert event is not None
    assert event.raw_usage == {"input_tokens": 10, "output_tokens": 4}
    assert event.raw_response_metadata == {"provider": "fake"}


def test_usage_event_from_response_returns_none_when_provider_supplies_no_usage_metadata() -> None:
    request = _request()
    response = SoftCallResponse(
        request_id=request.request_id,
        response_payload={"value": "ok"},
    )

    assert usage_event_from_response(request, response) is None


async def _round_trip_fake_provider(
    provider: DeferredProvider,
    request: SoftCallRequest,
) -> _Output:
    handle = await provider.submit((request,))
    result = await provider.poll(handle)
    assert isinstance(result, DeferredResults)
    response = result.successful[request.request_id]
    return adapt_soft_call_response(request, response, output_model=_Output).output


@pytest.mark.asyncio
async def test_fake_deferred_provider_round_trips_submit_poll_collect() -> None:
    request = _request()
    provider = FakeDeferredProvider(
        successful={
            request.request_id: SoftCallResponse(
                request_id=request.request_id,
                response_payload={"value": "ok"},
            ),
        },
    )

    output = await _round_trip_fake_provider(provider, request)

    assert output == _Output(value="ok")


@pytest.mark.asyncio
async def test_run_extraction_deferred_returns_submission_with_soft_call_manifest() -> None:
    provider = _CapturingDeferredProvider()

    submission = await run_extraction(
        document="Call 555-1234 or 555-9999.",
        spec=ExtractionSpec.from_pydantic(_DeferredPhone),
        runtime=Runtime(llm=object(), deferred_provider=provider),
        policy=ExecutorPolicy(strategy="batch", execution_mode="deferred", repair=False),
    )

    assert isinstance(submission, DeferredSubmission)
    assert submission.handle.provider_batch_id == "fake-batch-submit"
    assert submission.request_count == 1
    assert submission.manifest.requests == provider.requests
    assert submission.manifest.spec_hash == submission.spec_hash
    assert submission.manifest.document_id
    assert submission.manifest.document_content_hash
    assert submission.manifest.requests[0].routing.document_id == (
        submission.manifest.document_id
    )
    assert submission.manifest.requests[0].routing.document_content_hash == (
        submission.manifest.document_content_hash
    )
    assert submission.manifest.requests[0].output_model_ref == (
        "extractx.pydantic_ai.batch_selector_response.v1"
    )
    assert submission.manifest.requests[0].rendered_prompt.messages
    assert submission.manifest.manifest_fingerprint


@pytest.mark.asyncio
async def test_collect_deferred_submission_resolves_recorded_response_to_extraction() -> None:
    provider = FakeDeferredProvider()
    spec = ExtractionSpec.from_pydantic(_DeferredPhone)
    policy = ExecutorPolicy(strategy="batch", execution_mode="deferred", repair=False)
    document = "Call 555-1234 or 555-9999."
    submission = await run_extraction(
        document=document,
        spec=spec,
        runtime=Runtime(llm=object(), deferred_provider=provider),
        policy=policy,
    )
    assert isinstance(submission, DeferredSubmission)
    request = submission.manifest.requests[0]
    provider.set_results(
        successful={
            request.request_id: _phone_response(
                request,
                raw_usage={"input_tokens": 12, "output_tokens": 5},
            ),
        },
    )
    polled = await provider.poll(submission.handle)
    assert isinstance(polled, DeferredResults)

    extraction = await collect_deferred_submission(
        document=document,
        spec=spec,
        runtime=Runtime(llm=object()),
        policy=policy,
        manifest=submission.manifest,
        results=polled,
    )

    assert extraction.outcome == "complete"
    assert extraction.to_pydantic(_DeferredPhone)[0].phone == "555-1234"
    assert len(extraction.usage_events) == 1
    usage = extraction.usage_events[0]
    assert usage.raw_usage == {"input_tokens": 12, "output_tokens": 5}
    assert usage.raw_response_metadata == {"provider": "fake", "model": "fake:model"}


@pytest.mark.asyncio
async def test_deferred_document_classifier_windows_and_reduces_to_one_observation() -> None:
    provider = FakeDeferredProvider()
    spec = ExtractionSpec.from_pydantic(
        _DeferredDocumentCategory,
        prompt_policy=PromptPolicy(selector_prompt_max_chars=8_000),
    )
    policy = ExecutorPolicy(strategy="batch", execution_mode="deferred", repair=False)
    document = (
        "irrelevant background. " * 250
        + "RECEIPT_MARKER the document announces a receipt transaction. "
        + "more irrelevant text. " * 250
    )
    runtime = Runtime(
        llm=object(),
        deferred_provider=provider,
        selector_prompt_policies={
            "verdict": SelectorPromptPolicy(
                document_context_mode="budgeted_windows",
                document_window_overlap_chars=0,
                document_reducer=DocumentClassificationReducerPolicy(
                    priority=("receipt", "review", "irrelevant"),
                ),
            ),
            "document_signals": SelectorPromptPolicy(
                document_context_mode="budgeted_windows",
                document_window_overlap_chars=0,
                document_reducer=DocumentClassificationReducerPolicy(strategy="union"),
            ),
        },
    )

    submission = await run_extraction(
        document=document,
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert isinstance(submission, DeferredSubmission)
    assert len(submission.manifest.requests) > 1
    requests_by_field: dict[str, list[SoftCallRequest]] = {}
    for request in submission.manifest.requests:
        assert request.routing.field_id is not None
        requests_by_field.setdefault(request.routing.field_id, []).append(request)
    assert set(requests_by_field) == {"verdict", "document_signals"}
    for requests in requests_by_field.values():
        assert {request.routing.window_count for request in requests} == {len(requests)}
        assert [request.routing.window_index for request in requests] == list(
            range(1, len(requests) + 1),
        )
    assert all(
        len(request.rendered_prompt.messages[1].content) <= 8_000
        for request in submission.manifest.requests
    )

    provider.set_results(
        successful={
            request.request_id: _literal_category_response(
                request,
                canonical_literals=(
                    ("receipt",)
                    if request.routing.field_id == "verdict"
                    and "RECEIPT_MARKER" in request.rendered_prompt.messages[1].content
                    else (
                        ("irrelevant",)
                        if request.routing.field_id == "verdict"
                        else (
                            ("total_present",)
                            if "RECEIPT_MARKER" in request.rendered_prompt.messages[1].content
                            else ()
                        )
                    )
                ),
            )
            for request in submission.manifest.requests
        },
    )
    polled = await provider.poll(submission.handle)
    assert isinstance(polled, DeferredResults)

    extraction = await collect_deferred_submission(
        document=document,
        spec=spec,
        runtime=runtime,
        policy=policy,
        manifest=submission.manifest,
        results=polled,
    )

    assert extraction.outcome == "complete"
    document_category = extraction.to_pydantic(_DeferredDocumentCategory)[0]
    assert document_category.verdict == "receipt"
    assert document_category.document_signals == ["total_present"]


@pytest.mark.asyncio
async def test_submit_deferred_aggregate_collects_each_document_independently() -> None:
    provider = FakeDeferredProvider(provider_batch_id="fake-aggregate-batch-1")
    spec = ExtractionSpec.from_pydantic(_DeferredPhone)
    policy = ExecutorPolicy(strategy="batch", execution_mode="deferred", repair=False)
    documents = {
        "doc-a": "Call 555-1234 or 555-9999.",
        "doc-b": "Call 212-0000.",
    }
    rendered = {
        document_key: render_deferred_submission(
            document=document,
            spec=spec,
            runtime=Runtime(llm=object()),
            policy=policy,
        )
        for document_key, document in documents.items()
    }

    aggregate = await submit_deferred_aggregate(
        rendered,
        provider=provider,
        route_metadata={
            "doc-a": {"document_id": "document-a"},
            "doc-b": {"document_id": "document-b"},
        },
    )

    assert aggregate.handle.provider_batch_id == "fake-aggregate-batch-1"
    assert aggregate.document_count == 2
    assert aggregate.request_count == 2
    assert provider.requests == aggregate.manifest.requests
    assert len(aggregate.manifest.document_manifests) == 2
    assert {
        route.route_metadata["document_id"]
        for route in aggregate.manifest.request_routes.values()
    } == {"document-a", "document-b"}

    provider.set_results(
        successful={
            request.request_id: _phone_response(
                request,
                raw_usage={"input_tokens": index * 10, "output_tokens": index},
            )
            for index, request in enumerate(aggregate.manifest.requests, start=1)
        },
    )
    polled = await provider.poll(aggregate.handle)
    assert isinstance(polled, DeferredResults)

    collected: dict[str, str] = {}
    for document_key, document_manifest in zip(
        documents,
        aggregate.manifest.document_manifests,
        strict=True,
    ):
        subset = deferred_results_for_document(polled, document_manifest)
        assert sum(
            int(response.raw_usage["input_tokens"])
            for response in subset.successful.values()
            if response.raw_usage is not None
        ) in {10, 20}
        extraction = await collect_deferred_submission(
            document=documents[document_key],
            spec=spec,
            runtime=Runtime(llm=object()),
            policy=policy,
            manifest=document_manifest,
            results=subset,
        )
        collected[document_key] = extraction.to_pydantic(_DeferredPhone)[0].phone
        assert len(extraction.usage_events) == 1
        assert extraction.usage_events[0].raw_usage is not None

    assert collected == {"doc-a": "555-1234", "doc-b": "212-0000"}


@pytest.mark.asyncio
async def test_deferred_aggregate_accepts_same_prompt_requests_for_different_documents() -> None:
    provider = FakeDeferredProvider(provider_batch_id="fake-aggregate-batch-doc-routes")
    request_a = _request(
        routing=SoftCallRouting(
            document_id="doc-a",
            document_content_hash="hash-a",
            field_id="answer",
            instance_id="inst_0",
        ),
    )
    request_b = _request(
        routing=SoftCallRouting(
            document_id="doc-b",
            document_content_hash="hash-b",
            field_id="answer",
            instance_id="inst_0",
        ),
    )
    assert request_a.rendered_prompt == request_b.rendered_prompt
    assert request_a.soft_call_identity == request_b.soft_call_identity
    assert request_a.request_id != request_b.request_id

    rendered = {
        "doc-a": RenderedDeferredSubmission(
            spec_hash="spec-hash",
            document_id="doc-a",
            document_content_hash="hash-a",
            requests=(request_a,),
        ),
        "doc-b": RenderedDeferredSubmission(
            spec_hash="spec-hash",
            document_id="doc-b",
            document_content_hash="hash-b",
            requests=(request_b,),
        ),
    }

    aggregate = await submit_deferred_aggregate(rendered, provider=provider)

    assert aggregate.request_count == 2
    assert provider.requests == (request_a, request_b)


@pytest.mark.asyncio
async def test_deferred_aggregate_preserves_raw_usage_per_request() -> None:
    provider = FakeDeferredProvider(provider_batch_id="fake-aggregate-batch-usage")
    rendered = {
        "doc-a": RenderedDeferredSubmission(
            spec_hash="spec-hash",
            document_id="doc-a",
            document_content_hash="hash-a",
            requests=(_request().model_copy(update={"request_id": "request-a"}),),
        ),
        "doc-b": RenderedDeferredSubmission(
            spec_hash="spec-hash",
            document_id="doc-b",
            document_content_hash="hash-b",
            requests=(_request().model_copy(update={"request_id": "request-b"}),),
        ),
    }
    aggregate = await submit_deferred_aggregate(rendered, provider=provider)
    provider.set_results(
        successful={
            "request-a": SoftCallResponse(
                request_id="request-a",
                response_payload={"value": "a"},
                raw_usage={"input_tokens": 11, "output_tokens": 1},
            ),
            "request-b": SoftCallResponse(
                request_id="request-b",
                response_payload={"value": "b"},
                raw_usage={"input_tokens": 13, "output_tokens": 2},
            ),
        },
    )

    polled = await provider.poll(aggregate.handle)

    assert isinstance(polled, DeferredResults)
    assert polled.successful["request-a"].raw_usage == {
        "input_tokens": 11,
        "output_tokens": 1,
    }
    assert polled.successful["request-b"].raw_usage == {
        "input_tokens": 13,
        "output_tokens": 2,
    }
    total_input_tokens = sum(
        int(response.raw_usage["input_tokens"])
        for response in polled.successful.values()
        if response.raw_usage is not None
    )
    assert total_input_tokens == 24


@pytest.mark.asyncio
async def test_submit_deferred_aggregate_rejects_duplicate_request_ids_before_submit() -> None:
    request = _request()
    rendered = RenderedDeferredSubmission(
        spec_hash="spec-hash",
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        requests=(request,),
    )
    provider = FakeDeferredProvider()

    with pytest.raises(InfrastructureError, match="deferred_aggregate.duplicate_request_id"):
        await submit_deferred_aggregate(
            {"doc-a": rendered, "doc-b": rendered},
            provider=provider,
        )

    assert provider.requests == ()


def test_deferred_results_split_successful_and_failed_maps() -> None:
    handle = DeferredHandle(
        provider="fake",
        provider_batch_id="fake-batch-1",
        submitted_at=datetime(2026, 5, 6, tzinfo=UTC),
        request_count=2,
    )

    results = DeferredResults(
        handle=handle,
        completed_at=datetime(2026, 5, 6, 0, 1, tzinfo=UTC),
        successful={
            "ok": SoftCallResponse(request_id="ok", response_payload={"value": "ok"}),
        },
        failed={
            "bad": {
                "request_id": "bad",
                "error_type": "provider_error",
                "message": "failed",
            },
        },
    )

    assert set(results.successful) == {"ok"}
    assert set(results.failed) == {"bad"}
    assert results.failed["bad"].error_type == "provider_error"


def test_validate_deferred_collect_contract_accepts_matching_manifest_and_results() -> None:
    request = _request()
    handle = DeferredHandle(
        provider="fake",
        provider_batch_id="fake-batch-1",
        submitted_at=datetime(2026, 5, 6, tzinfo=UTC),
        request_count=1,
    )
    manifest = DeferredSubmissionManifest(
        spec_hash="spec-hash",
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        handle=handle,
        requests=(request,),
        manifest_fingerprint=deferred_submission_manifest_fingerprint(
            spec_hash="spec-hash",
            document_id="doc-1",
            document_content_hash="doc-hash-1",
            handle=handle,
            requests=(request,),
        ),
    )
    results = DeferredResults(
        handle=handle,
        completed_at=datetime(2026, 5, 6, 0, 1, tzinfo=UTC),
        successful={
            request.request_id: SoftCallResponse(
                request_id=request.request_id,
                response_payload={"value": "ok"},
            ),
        },
    )

    validate_deferred_collect_contract(
        manifest=manifest,
        results=results,
        spec_hash="spec-hash",
        document_id="doc-1",
        document_content_hash="doc-hash-1",
    )


def test_validate_deferred_collect_contract_rejects_stale_document_hash() -> None:
    request = _request()
    handle = DeferredHandle(
        provider="fake",
        provider_batch_id="fake-batch-1",
        submitted_at=datetime(2026, 5, 6, tzinfo=UTC),
        request_count=1,
    )
    manifest = DeferredSubmissionManifest(
        spec_hash="spec-hash",
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        handle=handle,
        requests=(request,),
        manifest_fingerprint=deferred_submission_manifest_fingerprint(
            spec_hash="spec-hash",
            document_id="doc-1",
            document_content_hash="doc-hash-1",
            handle=handle,
            requests=(request,),
        ),
    )
    results = DeferredResults(
        handle=handle,
        completed_at=datetime(2026, 5, 6, 0, 1, tzinfo=UTC),
        successful={
            request.request_id: SoftCallResponse(
                request_id=request.request_id,
                response_payload={"value": "ok"},
            ),
        },
    )

    with pytest.raises(InfrastructureError, match="deferred_collect.stale_manifest"):
        validate_deferred_collect_contract(
            manifest=manifest,
            results=results,
            spec_hash="spec-hash",
            document_id="doc-1",
            document_content_hash="changed",
        )


def test_validate_deferred_collect_contract_rejects_unknown_result_ids() -> None:
    request = _request()
    handle = DeferredHandle(
        provider="fake",
        provider_batch_id="fake-batch-1",
        submitted_at=datetime(2026, 5, 6, tzinfo=UTC),
        request_count=1,
    )
    manifest = DeferredSubmissionManifest(
        spec_hash="spec-hash",
        document_id="doc-1",
        document_content_hash="doc-hash-1",
        handle=handle,
        requests=(request,),
        manifest_fingerprint=deferred_submission_manifest_fingerprint(
            spec_hash="spec-hash",
            document_id="doc-1",
            document_content_hash="doc-hash-1",
            handle=handle,
            requests=(request,),
        ),
    )
    results = DeferredResults(
        handle=handle,
        completed_at=datetime(2026, 5, 6, 0, 1, tzinfo=UTC),
        successful={
            request.request_id: SoftCallResponse(
                request_id=request.request_id,
                response_payload={"value": "ok"},
            ),
            "unknown": SoftCallResponse(
                request_id="unknown",
                response_payload={"value": "ok"},
            ),
        },
    )

    with pytest.raises(InfrastructureError, match="deferred_collect.unknown_result_request_ids"):
        validate_deferred_collect_contract(
            manifest=manifest,
            results=results,
            spec_hash="spec-hash",
            document_id="doc-1",
            document_content_hash="doc-hash-1",
        )
