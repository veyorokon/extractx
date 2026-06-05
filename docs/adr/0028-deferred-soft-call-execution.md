# ADR-0028: Deferred Soft-Call Execution

**Status:** Proposed
**Date:** 2026-05-06

## Context

ADR-0023 added batch selector observations: multiple field decisions can be
packed into one selector call. ADR-0025 added prompt-budgeted planning and
candidate sharding so large documents can still be selected without provider
timeouts. Those decisions keep the selector contract grounded, but corpus-scale
workloads still pay synchronous wall time and online rate-limit pressure for
work that has no real-time SLA.

Provider batch APIs such as OpenAI Batch API and Anthropic Message Batches API
offer a different execution lifecycle: submit requests now, collect results
later. That lifecycle is orthogonal to extractx's existing `BatchSelector`
vocabulary. In extractx, "batch" already means field-packing within selection;
the new lifecycle should be named "deferred" to avoid overloading the term.

## Decision

extractx will add deferred soft-call execution as an opt-in execution lifecycle
alongside immediate execution.

Immediate and deferred execution both operate over the same extractx-owned
`SoftCallRequest` contract. Immediate execution sends the request to the
configured provider, records the provider output into a `SoftCallResponse`
envelope, and adapts that envelope immediately. Deferred execution
persists a `DeferredSubmissionManifest`, submits the requests through a
`DeferredProvider`, later collects `SoftCallResponse`s, and adapts them through
the same `adapt_soft_call_response(...)` function used by immediate execution.

Deferred execution is transport-only. Selector, proposer, observation, replay,
scoring, validation, and resolution contracts do not change.

## Vocabulary

Use "deferred" for the lifecycle and reserve "batch" for existing selector
field-packing or provider-native field names.

Canonical names:

- `ExecutionMode = Literal["immediate", "deferred"]`
- `SoftCallRequest`
- `SoftCallResponse`
- `SoftCallError`
- `DeferredProvider`
- `DeferredHandle`
- `DeferredPending`
- `DeferredResults`
- `DeferredSubmissionManifest`
- `adapt_soft_call_response(...)`

Provider-native vocabulary may remain inside provider-specific fields. For
example, `DeferredHandle.provider_batch_id` stores the OpenAI or Anthropic
native batch id, but extractx type names should still describe the deferred
lifecycle.

## Contract

Deferred execution is an execution lifecycle over soft-compute calls.

Required invariants:

- `ExecutorPolicy.execution_mode` selects lifecycle explicitly. The default is
  `"immediate"` and existing behavior remains unchanged.
- Deferred execution is never an automatic fallback from immediate execution.
- Strategies and selectors do not know provider batch APIs exist. They render
  soft-call work and consume typed results.
- extractx owns the `DeferredSubmissionManifest` contract. Consumers may
  persist it in their own storage, but they do not define its shape.
- `SoftCallRequest` is extractx-owned and provider-agnostic. It is not a raw
  HTTP request or provider SDK payload.
- `SoftCallResponse` / `SoftCallError` are recorded provider-response envelopes
  for one soft call before typed validation.
- `SoftCallResponse.response_payload` is the recorded provider-native response
  shape, pre-validation. Provider adapters store what the provider returned;
  they do not normalize the response payload into extractx semantic output
  before adaptation.
- `ProviderResult[T]` remains the typed validated output plus optional usage
  metadata after adaptation.
- The same `adapt_soft_call_response(...)` code path validates immediate and
  deferred responses into `ProviderResult[T]`. Immediate execution must record
  provider output into a `SoftCallResponse` envelope before adaptation; otherwise
  the "same adapter" invariant is only nominal.
- Deferred results are keyed by extractx `request_id`, not by provider order.
  The manifest may preserve original request order for diagnostics, but lookup
  is request-id based.
- Per-request failures are isolated as `SoftCallError`s. A partial provider
  completion must not convert successful requests into failures.
- Deferred mode initially rejects `repair=True` with a typed configuration
  error. Chained deferred repair is a later contract.
- Deferred cancellation is best-effort. In-flight provider work may complete
  and may still be billed. Providers without native cancel support may treat
  `cancel(...)` as a no-op and let the deferred submission run to completion.
- `request_id` is deterministic for a given soft call. It is derived from the
  soft-call identity, spec hash, output model ref, and routing salt. Consumers
  may use the resulting manifest fingerprint to detect duplicate submissions;
  extractx does not maintain cross-run submission history.
- `DeferredSubmissionManifest` contains rendered prompts, which may include
  source document excerpts. It has transcript-grade privacy posture and must
  not be treated as lightweight operational metadata.

The load-bearing acceptance criterion is:

```text
same SoftCallRequest + same recorded SoftCallResponse
  -> same ProviderResult[T]
  -> same Observation/proposal contract
```

If immediate and deferred execution satisfy that criterion, deferred execution
is transport-only by construction.

## Core Types

Sketch:

```python
class ExecutionMode(StrEnum):
    IMMEDIATE = "immediate"
    DEFERRED = "deferred"


class SoftCallRouting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: FieldId | None = None
    instance_id: str | None = None
    shard_index: int | None = None
    shard_count: int | None = None
    reducer_round: int | None = None
    parent_request_id: str | None = None


class SoftCallRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    rendered_prompt: RenderedPrompt
    output_model_ref: str
    soft_call_identity: str
    structured_output_mode: StructuredOutputMode | None = None
    routing: SoftCallRouting = Field(default_factory=SoftCallRouting)


class SoftCallResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    response_payload: Mapping[str, Any]
    raw_usage: Mapping[str, Any] | None = None
    raw_response_metadata: Mapping[str, Any] | None = None


class SoftCallError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    error_type: str
    message: str
    raw_error: Mapping[str, Any] | None = None


class DeferredHandle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    provider_batch_id: str
    submitted_at: datetime
    request_count: int


class DeferredSubmissionManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_hash: str
    document_id: str
    document_content_hash: str
    handle: DeferredHandle
    requests: tuple[SoftCallRequest, ...]
    manifest_fingerprint: str
    provider_request_ids: Mapping[str, str] = Field(default_factory=dict)


class DeferredSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: DeferredSubmissionManifest
    handle: DeferredHandle
    submitted_at: datetime
    spec_hash: str
    request_count: int


class DeferredPending(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    handle: DeferredHandle
    checked_at: datetime


class DeferredResults(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    handle: DeferredHandle
    completed_at: datetime
    successful: Mapping[str, SoftCallResponse] = Field(default_factory=dict)
    failed: Mapping[str, SoftCallError] = Field(default_factory=dict)


class DeferredProvider(Protocol):
    async def submit(
        self,
        requests: tuple[SoftCallRequest, ...],
    ) -> DeferredHandle: ...

    async def poll(
        self,
        handle: DeferredHandle,
    ) -> DeferredPending | DeferredResults: ...

    async def cancel(self, handle: DeferredHandle) -> None: ...


def adapt_soft_call_response(
    request: SoftCallRequest,
    response: SoftCallResponse,
    *,
    output_model: type[T],
) -> ProviderResult[T]: ...
```

`output_model_ref` must be durable and serializable. A persisted manifest must
not store Python `type` objects. Collect resolves `output_model_ref` to the
known DTO type through extractx-owned selector/proposer metadata.
The ref is set by the selector or proposer when rendering the `SoftCallRequest`;
consumers do not construct refs directly. Collect-time resolution uses an
extractx-internal registry keyed by that ref before calling
`adapt_soft_call_response(...)`.

`DeferredSubmission` is the public return shape for a deferred extraction
submission. `DeferredSubmissionManifest` is the durable artifact inside that
return. Callers should not need to unpack the manifest to find common operation
fields such as handle, submit time, spec hash, or request count.

Collect verifies the manifest identity before interpreting any recorded
response. `manifest.spec_hash`, `manifest.document_id`, and
`manifest.document_content_hash` must match the spec and document passed to
collect. A mismatch raises an `InfrastructureError` with the
`deferred_collect.stale_manifest` prefix. Collect also rejects unknown,
duplicated, or missing request ids before adapting successful responses.

The tier-1 collect entry point is `collect_deferred_submission(...)`. It accepts
the original document, spec, runtime, policy, `DeferredSubmissionManifest`, and
`DeferredResults`, then returns an `Extraction`. Initial support is intentionally
narrow: `strategy="batch"`, `execution_mode="deferred"`, `repair=False`, all
requests successful, and no sharded reducer follow-up. If sharded collection
needs a reducer soft call, collect raises `deferred_collect.reducer_required`
instead of silently making an immediate provider call.

## Usage Events

Deferred execution preserves provider-supplied operational metadata; it does not
price or normalize it. When a provider response includes usage,
`SoftCallResponse.raw_usage` carries the provider-native object unchanged and
`usage_event_from_response(request, response)` projects it into a `UsageEvent`.
When usage is absent, `raw_usage` may be `None`; consumers should treat that as
unknown or unattributed cost, not as zero cost.

The collect path uses the same projection through
`adapt_soft_call_response(...)`, so `collect_deferred_submission(...)` returns an
`Extraction` whose `usage_events` include deferred provider usage when the
provider supplied it. Provider-specific cost calculation, pricing tables, and
ledger persistence remain consumer-owned.

## Executor Policy

`ExecutorPolicy` gains an execution lifecycle axis:

```python
ExecutorPolicy(
    strategy="batch",
    execution_mode="immediate",
    repair=True,
)
```

`strategy` answers how extractx composes candidate generation, selection,
repair, and validation.

`execution_mode` answers when soft-call provider work is resolved:

- `"immediate"`: call provider now and continue execution;
- `"deferred"`: render soft calls, submit/persist a deferred manifest, and stop
  until collect resolves provider responses.

`Runtime` still owns capability bindings: which provider, model, and transport
adapter are available. `ExecutorPolicy` owns execution lifecycle.

`run_extraction(...)` returns different types for different lifecycles:

```python
run_extraction(...) -> Extraction | DeferredSubmission
```

Immediate execution returns `Extraction`. Deferred execution returns
`DeferredSubmission` because no extraction exists yet. Raising on deferred mode
would make a valid policy value look exceptional, and returning a bare
`DeferredSubmissionManifest` would expose an internal artifact as the ergonomic
API surface.

## Diagnostics

Deferred lifecycle diagnostics should use "deferred" vocabulary.

Required log-facing fields:

- `execution_mode`
- `provider`
- `provider_batch_id`
- `request_count`
- `submitted_at`
- `completed_at`
- `elapsed_from_submit_to_complete_ms`
- `successful_request_count`
- `failed_request_count`

Provider-native ids may appear as metadata, but diagnosis should identify the
extractx seam first: deferred submission, deferred poll, or deferred collect.
Per-request failure details live in `DeferredResults.failed[request_id]` as
`SoftCallError`; diagnostic counts are derived from `DeferredResults`.

## Consumer Lifecycle

Consumers may wrap a `DeferredSubmissionManifest` in their own lifecycle record,
such as a consumer-owned `DeferredExtractionRun`. That consumer-owned record owns
persistence status, polling cadence, operator visibility, and output links.
extractx owns only the manifest contract, deferred provider contract, and
collect/adaptation contract.

Consumer lifecycle state machines are intentionally outside extractx. A
consumer may distinguish states such as rendering, submitted, collecting,
partially failed, completed, failed, and cancelled; extractx should expose
enough manifest/result data for those states without owning their names or
scheduling policy.

## Implementation Phases

- **Phase 1 — contract, fake provider, and narrow collect:** add `execution_mode`,
  `SoftCallRequest`, deferred manifest/result types, a fake `DeferredProvider`,
  deterministic request ids, `DeferredSubmission`, shared response adaptation,
  and `collect_deferred_submission(...)` for successful batch-selector
  submissions that do not require follow-up soft calls. Completion condition: a
  fake deferred submission round-trips through submit, poll, collect, typed
  output adaptation, and final `Extraction`; the immediate path records a
  `SoftCallResponse` envelope and uses the same adapter; existing immediate
  tests remain behaviorally unchanged.
- **Phase 2 — selector/proposer deferred collect:** wire strategies/executor so
  selector and proposer soft calls can be rendered into a deferred manifest and
  later collected into the same typed observations/proposals when follow-up
  reducer or proposer calls are required. Completion condition: sharded reducer
  follow-up and proposer collection match immediate extraction for the same
  recorded responses.
- **Phase 3 — OpenAI deferred provider:** add an OpenAI Batch API adapter.
  Initial adapter support targets `/v1/chat/completions` Batch requests with
  JSON Schema structured output, using `SoftCallRequest.request_id` as the
  provider `custom_id`. Completion condition: recorded OpenAI-shaped batch
  output resolves through the same collect path and preserves usage metadata;
  opt-in live coverage may be added separately.
- **Phase 4 — Anthropic deferred provider:** add an Anthropic Message Batches
  adapter behind the same `DeferredProvider` protocol.
- **Phase 5 — chained deferred repair:** allow `repair=True` under deferred
  execution by submitting repair soft calls as a follow-up deferred submission
  linked to the parent request ids. This likely deserves its own ADR once the
  initial deferred lifecycle is proven.

## Alternatives Considered

- **Extend ADR-0027.** Rejected. ADR-0027 is about structured-output transport
  for one provider call. Deferred execution is a lifecycle decision spanning
  submit, persistence, polling, and collect.
- **Use "batch" type names.** Rejected. extractx already uses `BatchSelector`
  for field-packing. Deferred lifecycle types named `BatchHandle` or
  `BatchResults` would blur two different concerns.
- **Let consumers define the manifest.** Rejected. Consumers may own
  persistence, but extractx must own the manifest contract so replay,
  collection, and forensics do not fragment.
- **Put deferred provider calls inside strategies.** Rejected. Strategies own
  extraction orchestration, not provider lifecycle. They should remain
  execution-mode agnostic.
- **Persist Python output model types in the manifest.** Rejected. Manifests
  must be durable and serializable. Store an `output_model_ref` and resolve it
  through extractx-owned metadata at collect time.
- **Return a bare manifest from `run_extraction(...)`.** Rejected. The manifest
  is a durable artifact, not the ergonomic API return. `DeferredSubmission`
  carries the manifest plus common operational fields.
- **Raise from `run_extraction(...)` when deferred mode is selected.** Rejected.
  Deferred is a valid execution lifecycle. The API should return a typed
  submission object, not force callers into exception-based dispatch.
- **Automatically switch immediate calls to deferred under load.** Rejected.
  Deferred execution changes lifecycle and latency guarantees. It must be an
  explicit policy choice.

## Consequences

- Corpus-scale consumers can use provider deferred APIs without changing
  selector, observation, or scoring contracts.
- The immediate path remains the default and stays suitable for online
  ingestion.
- Extractx gains a durable soft-call manifest contract that can support replay
  and forensics across provider lifecycles.
- Initial deferred execution cannot use repair; consumers that require repair
  must stay immediate until chained deferred repair is designed.
- Provider adapters become responsible for mapping provider-native batch ids
  and result payloads into extractx deferred result types.

## Related

- [ADR-0015: Minimal Soft-Compute Usage Events](0015-minimal-soft-compute-usage-events.md)
- [ADR-0023: Batch Selector Observations](0023-batch-selector-observations.md)
- [ADR-0025: Budgeted Selector Task Planning](0025-budgeted-selector-task-planning.md)
- [ADR-0027: Negotiate Provider Structured Output Modes](0027-provider-structured-output-modes.md)
