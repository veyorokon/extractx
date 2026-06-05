# ADR-0029: Aggregate Deferred Submissions

**Status:** Proposed
**Date:** 2026-05-08

## Context

ADR-0028 introduced deferred soft-call execution as a transport lifecycle over
`SoftCallRequest`s. The first implementation deliberately used a narrow
one-document shape:

```text
one document -> one DeferredSubmission -> one provider-native batch
```

That shape was correct for proving lifecycle safety: each document had one
auditable row, one manifest, one collect operation, and one extraction result.
It also let a consumer verify OpenAI Batch end-to-end without changing extraction
semantics.

However, provider batch APIs are bulk primitives. OpenAI Batch accepts one JSONL
file containing many independent requests and returns unordered per-request
results keyed by `custom_id`. Submitting one provider-native batch per document
creates many small provider jobs, makes command-level submit concurrency look
like "batching the batch," and wastes the operational simplicity that deferred
execution is supposed to provide.

The architectural gap is now visible:

- extractx owns soft-call request, provider, manifest, and collect contracts.
- consumers own lifecycle orchestration, persistence, polling cadence, retry,
  resubmission, and operator state.
- corpus-scale callers need to submit many documents' soft calls in one
  provider-native batch while still collecting and auditing one extraction per
  document.

## Decision

Add an aggregate deferred submission shape above ADR-0028's per-document
`DeferredSubmission`.

The aggregate is an operational envelope over many document-scoped deferred
submissions:

```text
many documents
  -> many document-scoped DeferredSubmissionManifest fragments
  -> one provider-native deferred submission
  -> many document-scoped collect results
```

This does not replace per-document extraction records. A consumer such as
a consumer should still keep one per-document lifecycle row for each document. The
aggregate provider submission is a separate operational record that owns the
provider handle, request/result counts, and the multiplexed request map.

## Contract

Aggregate deferred submission is an orchestration contract, not a new
extraction strategy.

Required invariants:

- `SoftCallRequest` remains the atomic provider request.
- `request_id` remains globally stable within the aggregate and is the provider
  `custom_id` when the provider supports custom ids.
- A document-scoped manifest remains the authority for collecting one document
  into one `Extraction`.
- The aggregate manifest is a routing envelope over request ids; it does not
  author observations, proposals, or extraction results.
- Provider output order is never trusted. Results are keyed by request id and
  demultiplexed back to document-scoped manifests.
- Per-document collect uses the same `collect_deferred_submission(...)` contract
  as ADR-0028 after selecting that document's successful and failed request ids.
- One document's provider failure must not fail successful sibling documents in
  the same aggregate.
- Aggregate lifecycle status is derived from provider status plus child
  document-run terminal states.
- Consumers own polling cadence, polling concurrency, retry/backoff,
  resubmission policy, and operator-visible lifecycle rows.
- extractx owns aggregate manifest and provider-result contracts only.

The key acceptance criterion:

```text
same document-scoped manifest + same subset of aggregate DeferredResults
  -> same Extraction as collecting an equivalent one-document DeferredSubmission
```

## Core Types

Sketch:

```python
class RenderedDeferredSubmission(BaseModel):
    spec_hash: str
    document_id: str
    document_content_hash: str
    requests: tuple[SoftCallRequest, ...]


class DeferredAggregateSubmissionManifest(BaseModel):
    handle: DeferredHandle
    requests: tuple[SoftCallRequest, ...]
    document_manifests: tuple[DeferredSubmissionManifest, ...]
    request_routes: Mapping[str, DeferredRequestRoute]
    manifest_fingerprint: str


class DeferredRequestRoute(BaseModel):
    request_id: str
    document_manifest_fingerprint: str
    document_id: str
    document_content_hash: str
    spec_hash: str
    route_metadata: Mapping[str, str] = Field(default_factory=dict)
```

`RenderedDeferredSubmission` is the render-stage artifact. It deliberately has
no provider handle and is not persisted as a submitted manifest. Submission
attaches the single aggregate `DeferredHandle` to each document's rendered
request set, producing document-scoped `DeferredSubmissionManifest`s that can be
collected through the existing ADR-0028 path.

`DeferredRequestRoute` carries only routing identity. Domain identifiers such as
consumer `document_record_id` may live in `route_metadata`, but extractx must not interpret
them.

An extractx helper may split aggregate results for one document:

```python
def deferred_results_for_document(
    aggregate_results: DeferredResults,
    document_manifest: DeferredSubmissionManifest,
) -> DeferredResults: ...
```

The helper validates that every request id in the document manifest is present
in the aggregate results and that no unrelated request ids are included in the
returned subset.

## Consumer Shape

For a consumer, the clean production shape is:

```text
DeferredProviderSubmission
  provider
  provider_batch_id
  status
  request_count
  submitted_at
  completed_at
  aggregate_manifest

DeferredExtractionRun
  document
  status
  spec_hash
  document_content_hash
  request_ids
  provider_submission -> DeferredProviderSubmission
  document_manifest
```

Submit:

1. Select N documents.
2. Render each document into one or more `SoftCallRequest`s.
3. Persist one `DeferredExtractionRun` per document.
4. Upload all requests in one provider-native deferred submission.
5. Persist one aggregate provider-submission row with handle and aggregate
   manifest.
6. Link each per-document run to the provider-submission row.

Collect:

1. Poll the provider-submission row.
2. Parse all provider results once.
3. Group results by document manifest / document run.
4. For each child run, call `collect_deferred_submission(...)` with that
   document manifest and result subset.
5. Mark each child run terminal independently.
6. Mark the aggregate provider submission terminal when all child runs are
   terminal.

## Orchestration Boundary

extractx must not become a scheduler.

extractx owns:

- `SoftCallRequest`
- document and aggregate manifest contracts
- provider adapter protocols
- response adaptation
- collect contracts

Consumers own:

- when to submit
- how many documents to aggregate
- when to poll
- polling concurrency
- retry/backoff
- resubmission policy
- cron/ofelia/celery integration
- operator-visible status rows

Command options such as `--submit-concurrency`, `--poll-concurrency`, and
`--limit` belong to consumers. They are orchestration policy, not extractx
semantics.

## Failure Semantics

- Provider-level submission failure: no aggregate handle exists; consumers may
  mark all child runs errored or leave them pending for resubmission.
- Provider-level terminal failure with per-request errors: parse into
  `DeferredResults.failed` keyed by request id; collect successful child runs
  independently.
- Missing result ids: fail the affected document collect with the existing
  deferred collect contract; do not fabricate abstentions.
- Duplicate request ids: aggregate construction fails before provider submit.
- Resubmission: consumers may create a new aggregate provider submission for
  terminal errored/cancelled child runs. Old aggregate and child rows remain
  auditable.

## Out Of Scope

- Chained deferred reducer or repair submissions. ADR-0028 still rejects
  `repair=True` under deferred and raises `deferred_collect.reducer_required`
  when a sharded reducer follow-up is needed.
- Automatic provider-batch sizing. Consumers choose how many documents to group.
- Cross-document semantic extraction. Documents remain independent; aggregation
  is transport-level multiplexing only.
- extractx-owned polling loops or background workers.

## Consequences

Positive:

- Corpus backfills use provider batch APIs as intended: one provider job can
  contain many documents' requests.
- Per-document auditability is preserved.
- Provider polling and output-file parsing happen once per aggregate instead of
  once per document.
- The one-document ADR-0028 path remains useful for tests, small pilots, and
  consumers that do not need aggregation.

Costs:

- Consumers need an aggregate provider-submission record or equivalent storage
  concept.
- Idempotency must distinguish document-scoped runs from aggregate provider
  submissions.
- Collect code must demultiplex aggregate results before invoking the
  document-scoped collect contract.

## Implementation Notes

Phase 1 should avoid changing selector or provider DTO contracts. It can add
only:

- aggregate manifest type
- duplicate request-id guard
- result-subsetting helper
- tests proving aggregate result subsets collect to the same extraction as
  one-document deferred collection

Provider adapters such as `OpenAIDeferredProvider` already accept
`tuple[SoftCallRequest, ...]`; no provider API change is required to submit many
documents' requests together. The missing piece is the aggregate manifest and
consumer orchestration around it.
