"""Deferred soft-call execution primitives.

ADR-0028 defines deferred execution as a lifecycle over soft-compute calls:
render now, submit to a deferred provider, collect typed results later. This
module contains the first-class kernel objects for that lifecycle. It does not
own provider HTTP clients, polling cadence, or consumer job state.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import FieldId, ProviderResult, RenderedPrompt, UsageEvent
from extractx.core.versions import stable_hash

__all__ = [
    "DeferredAggregateSubmission",
    "DeferredAggregateSubmissionManifest",
    "DeferredHandle",
    "DeferredPending",
    "DeferredProvider",
    "DeferredRequestRoute",
    "DeferredResults",
    "DeferredSubmission",
    "DeferredSubmissionManifest",
    "ExecutionMode",
    "FakeDeferredProvider",
    "RenderedDeferredSubmission",
    "SoftCallError",
    "SoftCallRequest",
    "SoftCallResponse",
    "SoftCallRouting",
    "adapt_soft_call_response",
    "aggregate_deferred_submissions",
    "deferred_submission_manifest_from_rendered",
    "deferred_submission_manifest_fingerprint",
    "deferred_aggregate_submission_manifest_fingerprint",
    "deferred_results_for_document",
    "make_soft_call_request_id",
    "submit_deferred_aggregate",
    "usage_event_from_response",
    "validate_deferred_collect_contract",
]

T = TypeVar("T")


class ExecutionMode(StrEnum):
    """Execution lifecycle for soft-compute calls."""

    IMMEDIATE = "immediate"
    DEFERRED = "deferred"


class SoftCallRouting(BaseModel):
    """Routing metadata that maps a soft call back to extractx work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str | None = None
    document_content_hash: str | None = None
    field_id: FieldId | None = None
    instance_id: str | None = None
    shard_index: int | None = None
    shard_count: int | None = None
    window_index: int | None = None
    window_count: int | None = None
    reducer_round: int | None = None
    parent_request_id: str | None = None


class SoftCallRequest(BaseModel):
    """Serializable description of one extractx soft-compute call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    rendered_prompt: RenderedPrompt
    output_model_ref: str
    soft_call_identity: str
    structured_output_mode: str | None = None
    routing: SoftCallRouting = Field(default_factory=SoftCallRouting)

    @model_validator(mode="after")
    def _normalize_json_round_trip_shapes(self) -> SoftCallRequest:
        rendered = self.rendered_prompt
        normalized_schema = _json_round_trip_value(rendered.structured_output_schema)
        normalized_metadata = _json_round_trip_value(rendered.metadata)
        if (
            normalized_schema == rendered.structured_output_schema
            and normalized_metadata == rendered.metadata
        ):
            return self

        normalized = rendered.model_copy(
            update={
                "structured_output_schema": normalized_schema,
                "metadata": normalized_metadata,
            },
        )
        object.__setattr__(self, "rendered_prompt", normalized)
        return self


class SoftCallResponse(BaseModel):
    """Recorded provider-native response envelope for one soft call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    response_payload: Mapping[str, Any]
    raw_usage: Mapping[str, Any] | None = None
    raw_response_metadata: Mapping[str, Any] | None = None


class SoftCallError(BaseModel):
    """Recorded provider error envelope for one soft call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    error_type: str
    message: str
    raw_error: Mapping[str, Any] | None = None


class DeferredHandle(BaseModel):
    """Provider-native lifecycle identity for a deferred submission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    provider_batch_id: str
    submitted_at: datetime
    request_count: int


class DeferredSubmissionManifest(BaseModel):
    """Extractx-owned mapping from soft calls to a deferred provider handle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_hash: str
    document_id: str
    document_content_hash: str
    handle: DeferredHandle
    requests: tuple[SoftCallRequest, ...]
    manifest_fingerprint: str
    provider_request_ids: Mapping[str, str] = Field(default_factory=dict)


class DeferredSubmission(BaseModel):
    """Public return shape for a submitted deferred extraction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: DeferredSubmissionManifest
    handle: DeferredHandle
    submitted_at: datetime
    spec_hash: str
    request_count: int


class RenderedDeferredSubmission(BaseModel):
    """Document-scoped deferred requests before provider submission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_hash: str
    document_id: str
    document_content_hash: str
    requests: tuple[SoftCallRequest, ...]


class DeferredRequestRoute(BaseModel):
    """Route one aggregate request back to its document-scoped manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    document_manifest_fingerprint: str
    document_id: str
    document_content_hash: str
    spec_hash: str
    route_metadata: Mapping[str, str] = Field(default_factory=dict)


class DeferredAggregateSubmissionManifest(BaseModel):
    """Transport envelope for many document-scoped deferred manifests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handle: DeferredHandle
    requests: tuple[SoftCallRequest, ...]
    document_manifests: tuple[DeferredSubmissionManifest, ...]
    request_routes: Mapping[str, DeferredRequestRoute]
    manifest_fingerprint: str
    provider_request_ids: Mapping[str, str] = Field(default_factory=dict)


class DeferredAggregateSubmission(BaseModel):
    """Public return shape for one provider batch covering many documents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: DeferredAggregateSubmissionManifest
    handle: DeferredHandle
    submitted_at: datetime
    request_count: int
    document_count: int


class DeferredPending(BaseModel):
    """Provider has not completed the deferred submission yet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handle: DeferredHandle
    checked_at: datetime


class DeferredResults(BaseModel):
    """Completed deferred submission, keyed by extractx request id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handle: DeferredHandle
    completed_at: datetime
    successful: Mapping[str, SoftCallResponse] = Field(default_factory=dict)
    failed: Mapping[str, SoftCallError] = Field(default_factory=dict)


class DeferredProvider(Protocol):
    """Provider capability for submit-now, collect-later soft-call execution."""

    async def submit(
        self,
        requests: tuple[SoftCallRequest, ...],
    ) -> DeferredHandle: ...

    async def poll(
        self,
        handle: DeferredHandle,
    ) -> DeferredPending | DeferredResults: ...

    async def cancel(self, handle: DeferredHandle) -> None: ...


class FakeDeferredProvider:
    """Importable fake deferred provider for contract and consumer tests.

    The fake records submitted requests and returns caller-provided per-request
    responses on `poll`. It implements the real `DeferredProvider` protocol so
    consumers can wire the lifecycle without provider credentials.
    """

    def __init__(
        self,
        *,
        successful: Mapping[str, SoftCallResponse] | None = None,
        failed: Mapping[str, SoftCallError] | None = None,
        provider_batch_id: str = "fake-deferred-batch-1",
        submitted_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        self._successful = dict(successful or {})
        self._failed = dict(failed or {})
        self._provider_batch_id = provider_batch_id
        self._submitted_at = submitted_at or datetime.now(UTC)
        self._completed_at = completed_at or self._submitted_at
        self._handle: DeferredHandle | None = None
        self.requests: tuple[SoftCallRequest, ...] = ()
        self.cancelled_handles: tuple[DeferredHandle, ...] = ()

    async def submit(
        self,
        requests: tuple[SoftCallRequest, ...],
    ) -> DeferredHandle:
        self.requests = requests
        self._handle = DeferredHandle(
            provider="fake",
            provider_batch_id=self._provider_batch_id,
            submitted_at=self._submitted_at,
            request_count=len(requests),
        )
        return self._handle

    async def poll(self, handle: DeferredHandle) -> DeferredPending | DeferredResults:
        self._assert_known_handle(handle)
        return DeferredResults(
            handle=handle,
            completed_at=self._completed_at,
            successful=self._successful,
            failed=self._failed,
        )

    async def cancel(self, handle: DeferredHandle) -> None:
        self._assert_known_handle(handle)
        self.cancelled_handles = (*self.cancelled_handles, handle)

    def set_results(
        self,
        *,
        successful: Mapping[str, SoftCallResponse] | None = None,
        failed: Mapping[str, SoftCallError] | None = None,
    ) -> None:
        """Replace results returned by later `poll` calls."""

        self._successful = dict(successful or {})
        self._failed = dict(failed or {})

    def _assert_known_handle(self, handle: DeferredHandle) -> None:
        if self._handle is None:
            raise InfrastructureError(
                "fake_deferred_provider.not_submitted: submit(...) must run before poll/cancel",
            )
        if handle != self._handle:
            raise InfrastructureError(
                "fake_deferred_provider.handle_mismatch: received unknown deferred handle",
            )


def make_soft_call_request_id(
    *,
    soft_call_identity: str,
    spec_hash: str,
    output_model_ref: str,
    routing: SoftCallRouting,
) -> str:
    """Return the deterministic request id for one soft call."""

    return stable_hash(
        {
            "soft_call_identity": soft_call_identity,
            "spec_hash": spec_hash,
            "output_model_ref": output_model_ref,
            "routing": routing.model_dump(mode="json", exclude_none=True),
        },
    )


def deferred_submission_manifest_fingerprint(
    *,
    spec_hash: str,
    document_id: str,
    document_content_hash: str,
    handle: DeferredHandle,
    requests: tuple[SoftCallRequest, ...],
    provider_request_ids: Mapping[str, str] | None = None,
) -> str:
    """Return the deterministic fingerprint for a deferred manifest."""

    return stable_hash(
        {
            "spec_hash": spec_hash,
            "document_id": document_id,
            "document_content_hash": document_content_hash,
            "handle": handle.model_dump(mode="json"),
            "requests": [request.model_dump(mode="json") for request in requests],
            "provider_request_ids": dict(provider_request_ids or {}),
        },
    )


def deferred_aggregate_submission_manifest_fingerprint(
    *,
    handle: DeferredHandle,
    requests: tuple[SoftCallRequest, ...],
    document_manifests: tuple[DeferredSubmissionManifest, ...],
    request_routes: Mapping[str, DeferredRequestRoute],
    provider_request_ids: Mapping[str, str] | None = None,
) -> str:
    """Return the deterministic fingerprint for an aggregate deferred manifest."""

    return stable_hash(
        {
            "handle": handle.model_dump(mode="json"),
            "requests": [request.model_dump(mode="json") for request in requests],
            "document_manifests": [
                manifest.model_dump(mode="json") for manifest in document_manifests
            ],
            "request_routes": {
                request_id: route.model_dump(mode="json")
                for request_id, route in request_routes.items()
            },
            "provider_request_ids": dict(provider_request_ids or {}),
        },
    )


def deferred_submission_manifest_from_rendered(
    rendered: RenderedDeferredSubmission,
    *,
    handle: DeferredHandle,
    provider_request_ids: Mapping[str, str] | None = None,
) -> DeferredSubmissionManifest:
    """Attach a provider handle to one document's rendered deferred requests."""

    request_ids = {request.request_id for request in rendered.requests}
    child_provider_request_ids = {
        request_id: provider_request_id
        for request_id, provider_request_id in (provider_request_ids or {}).items()
        if request_id in request_ids
    }
    fingerprint = deferred_submission_manifest_fingerprint(
        spec_hash=rendered.spec_hash,
        document_id=rendered.document_id,
        document_content_hash=rendered.document_content_hash,
        handle=handle,
        requests=rendered.requests,
        provider_request_ids=child_provider_request_ids,
    )
    return DeferredSubmissionManifest(
        spec_hash=rendered.spec_hash,
        document_id=rendered.document_id,
        document_content_hash=rendered.document_content_hash,
        handle=handle,
        requests=rendered.requests,
        provider_request_ids=child_provider_request_ids,
        manifest_fingerprint=fingerprint,
    )


def aggregate_deferred_submissions(
    rendered_submissions: Mapping[str, RenderedDeferredSubmission],
    *,
    handle: DeferredHandle,
    route_metadata: Mapping[str, Mapping[str, str]] | None = None,
    provider_request_ids: Mapping[str, str] | None = None,
) -> DeferredAggregateSubmissionManifest:
    """Build one provider-transport manifest from rendered document requests.

    `rendered_submissions` is keyed by consumer-owned document key. The key is not
    interpreted by extractx; callers that need it later can copy it into
    `route_metadata`.
    """

    if not rendered_submissions:
        raise InfrastructureError(
            "deferred_aggregate.empty_submission: cannot aggregate zero manifests",
        )
    requests: list[SoftCallRequest] = []
    document_manifests: list[DeferredSubmissionManifest] = []
    request_routes: dict[str, DeferredRequestRoute] = {}
    seen_request_ids: set[str] = set()
    for document_key, rendered in rendered_submissions.items():
        manifest = deferred_submission_manifest_from_rendered(
            rendered,
            handle=handle,
            provider_request_ids=provider_request_ids,
        )
        document_manifests.append(manifest)
        for request in manifest.requests:
            if request.request_id in seen_request_ids:
                raise InfrastructureError(
                    "deferred_aggregate.duplicate_request_id: "
                    f"{request.request_id!r}",
                )
            seen_request_ids.add(request.request_id)
            requests.append(request)
            metadata = dict((route_metadata or {}).get(document_key, {}))
            metadata.setdefault("document_key", document_key)
            request_routes[request.request_id] = DeferredRequestRoute(
                request_id=request.request_id,
                document_manifest_fingerprint=manifest.manifest_fingerprint,
                document_id=manifest.document_id,
                document_content_hash=manifest.document_content_hash,
                spec_hash=manifest.spec_hash,
                route_metadata=metadata,
            )

    if handle.request_count != len(requests):
        raise InfrastructureError(
            "deferred_aggregate.handle_request_count_mismatch: handle request_count "
            f"{handle.request_count} != aggregate request_count {len(requests)}",
        )

    requests_tuple = tuple(requests)
    document_manifests_tuple = tuple(document_manifests)
    fingerprint = deferred_aggregate_submission_manifest_fingerprint(
        handle=handle,
        requests=requests_tuple,
        document_manifests=document_manifests_tuple,
        request_routes=request_routes,
        provider_request_ids=provider_request_ids,
    )
    return DeferredAggregateSubmissionManifest(
        handle=handle,
        requests=requests_tuple,
        document_manifests=document_manifests_tuple,
        request_routes=request_routes,
        provider_request_ids=dict(provider_request_ids or {}),
        manifest_fingerprint=fingerprint,
    )


async def submit_deferred_aggregate(
    rendered_submissions: Mapping[str, RenderedDeferredSubmission],
    *,
    provider: DeferredProvider,
    route_metadata: Mapping[str, Mapping[str, str]] | None = None,
    provider_request_ids: Mapping[str, str] | None = None,
) -> DeferredAggregateSubmission:
    """Submit many rendered document request sets as one provider-native batch."""

    requests = tuple(
        request
        for rendered in rendered_submissions.values()
        for request in rendered.requests
    )
    if not requests:
        raise InfrastructureError(
            "deferred_aggregate.empty_submission: cannot submit zero requests",
        )
    duplicate_request_ids = _duplicates(tuple(request.request_id for request in requests))
    if duplicate_request_ids:
        raise InfrastructureError(
            "deferred_aggregate.duplicate_request_id: "
            f"{duplicate_request_ids!r}",
        )
    handle = await provider.submit(requests)
    manifest = aggregate_deferred_submissions(
        rendered_submissions,
        handle=handle,
        route_metadata=route_metadata,
        provider_request_ids=provider_request_ids,
    )
    return DeferredAggregateSubmission(
        manifest=manifest,
        handle=handle,
        submitted_at=handle.submitted_at,
        request_count=len(requests),
        document_count=len(rendered_submissions),
    )


def deferred_results_for_document(
    aggregate_results: DeferredResults,
    document_manifest: DeferredSubmissionManifest,
) -> DeferredResults:
    """Return the subset of aggregate results for one document manifest."""

    request_ids = {request.request_id for request in document_manifest.requests}
    successful = {
        request_id: response
        for request_id, response in aggregate_results.successful.items()
        if request_id in request_ids
    }
    failed = {
        request_id: error
        for request_id, error in aggregate_results.failed.items()
        if request_id in request_ids
    }
    subset = DeferredResults(
        handle=aggregate_results.handle,
        completed_at=aggregate_results.completed_at,
        successful=successful,
        failed=failed,
    )
    validate_deferred_collect_contract(
        manifest=document_manifest,
        results=subset,
        spec_hash=document_manifest.spec_hash,
        document_id=document_manifest.document_id,
        document_content_hash=document_manifest.document_content_hash,
    )
    return subset


def validate_deferred_collect_contract(
    *,
    manifest: DeferredSubmissionManifest,
    results: DeferredResults,
    spec_hash: str,
    document_id: str,
    document_content_hash: str,
) -> None:
    """Validate submit→collect identity before interpreting responses.

    Deferred collection re-runs deterministic setup before recorded provider
    responses are adapted. This guard rejects stale or mismatched collect calls
    before any response can be interpreted against the wrong spec, document, or
    request set.
    """

    expected_fingerprint = deferred_submission_manifest_fingerprint(
        spec_hash=manifest.spec_hash,
        document_id=manifest.document_id,
        document_content_hash=manifest.document_content_hash,
        handle=manifest.handle,
        requests=manifest.requests,
        provider_request_ids=manifest.provider_request_ids,
    )
    if manifest.manifest_fingerprint != expected_fingerprint:
        raise InfrastructureError(
            "deferred_collect.manifest_fingerprint_mismatch: manifest fingerprint "
            "does not match manifest contents",
        )
    if manifest.spec_hash != spec_hash:
        raise InfrastructureError(
            "deferred_collect.stale_manifest: manifest spec_hash "
            f"{manifest.spec_hash!r} does not match collect spec_hash {spec_hash!r}",
        )
    if manifest.document_id != document_id:
        raise InfrastructureError(
            "deferred_collect.stale_manifest: manifest document_id "
            f"{manifest.document_id!r} does not match collect document_id {document_id!r}",
        )
    if manifest.document_content_hash != document_content_hash:
        raise InfrastructureError(
            "deferred_collect.stale_manifest: manifest document_content_hash "
            f"{manifest.document_content_hash!r} does not match collect "
            f"document_content_hash {document_content_hash!r}",
        )
    if results.handle != manifest.handle:
        raise InfrastructureError(
            "deferred_collect.handle_mismatch: result handle does not match manifest handle",
        )

    request_ids = tuple(request.request_id for request in manifest.requests)
    duplicate_request_ids = _duplicates(request_ids)
    if duplicate_request_ids:
        raise InfrastructureError(
            "deferred_collect.duplicate_manifest_request_ids: "
            f"{duplicate_request_ids!r}",
        )

    expected = set(request_ids)
    successful = set(results.successful)
    failed = set(results.failed)
    overlapping = sorted(successful & failed)
    if overlapping:
        raise InfrastructureError(
            "deferred_collect.duplicate_result_request_ids: "
            f"request ids appear in successful and failed results: {overlapping!r}",
        )
    unknown = sorted((successful | failed) - expected)
    if unknown:
        raise InfrastructureError(
            "deferred_collect.unknown_result_request_ids: "
            f"{unknown!r}",
        )
    missing = sorted(expected - (successful | failed))
    if missing:
        raise InfrastructureError(
            "deferred_collect.missing_result_request_ids: "
            f"{missing!r}",
        )

    for request_id, response in results.successful.items():
        if response.request_id != request_id:
            raise InfrastructureError(
                "deferred_collect.response_request_id_mismatch: successful result "
                f"key {request_id!r} contains response request_id {response.request_id!r}",
            )
    for request_id, error in results.failed.items():
        if error.request_id != request_id:
            raise InfrastructureError(
                "deferred_collect.error_request_id_mismatch: failed result "
                f"key {request_id!r} contains error request_id {error.request_id!r}",
            )


def adapt_soft_call_response[OutputT](
    request: SoftCallRequest,
    response: SoftCallResponse,
    *,
    output_model: type[OutputT],
) -> ProviderResult[OutputT]:
    """Validate a recorded soft-call response into typed provider output.

    The fake-provider and simple JSON-envelope path use `response_payload`
    directly as the output object. Provider-specific adapters may wrap this
    function after first extracting the equivalent output payload from their
    provider-native response envelope.
    """

    if response.request_id != request.request_id:
        raise ValueError(
            "adapt_soft_call_response.request_mismatch: response request_id "
            f"{response.request_id!r} does not match request {request.request_id!r}",
        )

    output = TypeAdapter(output_model).validate_python(response.response_payload)
    usage_event = usage_event_from_response(request, response)
    return ProviderResult(output=output, usage_event=usage_event)


def usage_event_from_response(
    request: SoftCallRequest,
    response: SoftCallResponse,
) -> UsageEvent | None:
    """Project one recorded deferred response into a `UsageEvent`.

    Usage is provider-supplied operational metadata. When a provider supplies
    usage or response metadata, extractx preserves it without pricing or token
    normalization. When neither is present, the response has no usage event and
    consumers should treat cost as unknown/unattributed rather than zero.
    """

    if response.raw_usage is None and response.raw_response_metadata is None:
        return None
    return UsageEvent(
        producer_version=request.rendered_prompt.metadata.get("producer_version", ""),
        operation=_operation_from_rendered_prompt(request.rendered_prompt),
        field_id=request.routing.field_id,
        instance_id=request.routing.instance_id,
        model_id=_metadata_str(request.rendered_prompt, "model_id"),
        soft_call_identity=request.soft_call_identity,
        timestamp_ns=0,
        raw_usage=response.raw_usage,
        raw_response_metadata=response.raw_response_metadata,
    )


def _metadata_str(rendered: RenderedPrompt, key: str) -> str | None:
    value = rendered.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _operation_from_rendered_prompt(rendered: RenderedPrompt) -> str | None:
    template_id = rendered.metadata.get("prompt_template_id")
    if template_id == "extractx.instances.proposer.v1":
        return "instance_proposer"
    if isinstance(template_id, str) and template_id.startswith("extractx.selection."):
        return "selector"
    return None


def _duplicates(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return tuple(duplicates)


def _json_round_trip_value(value: Any) -> Any:
    return json.loads(json.dumps(value))
