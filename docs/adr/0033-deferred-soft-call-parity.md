# ADR-0033: Deferred Soft-Call Parity

**Status:** Accepted
**Date:** 2026-05-19

## Context

Deferred execution lets extractx render soft-compute requests now, submit them
to a provider-backed lifecycle, and collect typed results later. That lifecycle
must not change the extraction task itself.

A consumer deferred document-classification smoke exposed two contract gaps:

- document-level literal classification prompts in deferred batch mode omitted
  document context, so the model could choose only among literal candidates
  without seeing the document;
- aggregate request ids collided across documents when schema, prompt shape, and
  routing identity were otherwise identical.

Both failures violate the intended deferred contract. Deferred execution should
be the same selector task with a later provider response, not a separate prompt
surface with different semantics.

## Decision

Deferred soft calls are lifecycle wrappers over the same logical soft-call task
used by immediate execution.

For a given selector task:

- prompt rendering semantics are the same in immediate and deferred execution;
- provider submission and collection are the only lifecycle differences;
- deferred collect adapts provider output through the same response translation
  and observation-contract enforcement as immediate execution.

## Invariants

### Same Render Contract

Immediate and deferred paths must use the same prompt-rendering contract for the
same selector task. Deferred code may wrap requests for provider transport, but
it must not omit semantic context that immediate execution requires.

Document-level literal classification requires document context. Literal
candidates are schema-owned choices, not document evidence. The classifier must
see the document text or bounded document context in both immediate and deferred
batch paths.

### Identity Separation

Prompt identity and deferred request identity are different concepts.

- prompt identity answers: "what selector task did the model see?"
- request identity answers: "which durable provider request does this response
  belong to?"

`rendered_prompt_hash` and related prompt metadata remain prompt identity.
Deferred `request_id` is transport/correlation identity and must include enough
route identity to be unique inside aggregate submissions.

### Document-Scoped Request Routing

Document-scoped deferred requests must carry document route identity. Aggregate
submissions must not allow two different documents to produce the same
`request_id` merely because their schema, selector prompt shape, and output
model are identical.

At minimum, deferred request identity must include document id or document
content hash through `SoftCallRouting` or an equivalent typed route surface.

### Same Collect Contract

Deferred collection must parse, translate prompt-local ids, and enforce
`Observation` contracts through the same selector-owned code path used by
immediate execution.

## Consequences

Consumers can treat deferred execution as an operational lifecycle choice rather
than a semantic mode. A document category classifier, terms selector, or future
soft-call producer should not need consumer-specific deferred prompt logic.

The deferred surface carries more explicit routing metadata, but that metadata
is transport identity, not prompt meaning. This keeps replay, diagnostics, and
provider correlation inspectable without smearing request ids into semantic
prompt identity.
