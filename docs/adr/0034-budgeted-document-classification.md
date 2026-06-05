# ADR-0034: Budgeted Document Classification

**Status:** Accepted
**Date:** 2026-05-19

## Context

Document-level literal classification is a selector task over schema-owned
literal candidates. Unlike extraction fields, the candidates do not carry
document evidence. The classifier must see document context to decide which
literal applies.

Large documents can exceed `PromptPolicy.selector_prompt_max_chars` when rendered
as one full-document classification prompt. Failing before provider submission
is honest, but it makes document-level classifiers unusable for large documents
even when the task can be decomposed without changing semantics.

This is distinct from candidate sharding. Candidate sharding splits a large
candidate menu. Budgeted document classification splits the document context
while keeping the same literal candidate menu.

## Decision

Extractx supports budgeted document-level classification for literal/category
fields through selector prompt policy.

One logical document classification may expand into multiple physical selector
calls, each carrying one bounded document window and the same literal candidate
menu. The window observations are then reduced to one final `Observation`.

The policy lives on `SelectorPromptPolicy`, not on `PromptPolicy`, because this
is selector-rendering behavior and may be varied at runtime without changing
semantic schema identity.

## Invariants

### Logical Request vs Physical Calls

The semantic unit remains one field classification for one document. Window
calls are physical soft calls, not independent facts.

```
document + field + literal candidates
  -> window call 1
  -> window call 2
  -> ...
  -> reducer
  -> one Observation
```

### Same Sync and Deferred Semantics

Immediate and deferred execution use the same window planner and reducer.
Deferred execution only changes provider lifecycle:

- immediate mode renders window calls, calls the selector, and reduces now;
- deferred mode renders window calls into provider requests, collects later, and
  reduces then.

### Typed Reducer Policy

Extractx does not hard-code domain semantics such as "receipt beats
irrelevant." Consumers provide a typed reducer policy.

V1 supports two reducer families:

- `priority` for single-label category fields. The first selected literal in
  the declared priority order wins.
- `union` for `Cardinality.MANY` category fields. Selected candidate ids from
  all windows are deduplicated and returned in source candidate order.

Reducer policy must be compatible with field cardinality. `priority` is invalid
for `Cardinality.MANY`; `union` is invalid for single-label fields. Empty union
is a successful empty multi-label selection: `outcome="SELECTED"`,
`abstain=false`, and `selected_candidate_ids=()`.

### Request Identity

Deferred window calls carry document id, document content hash, field id, and
window index/count in `SoftCallRouting`. This makes provider request identity
unique while keeping prompt identity separate from transport identity.

### Replay Diagnostics

Selector-call diagnostics must preserve enough structure to reconstruct what
happened:

- window index/count;
- prompt hash/ref;
- presented candidate ids;
- selector response hashes/refs;
- final per-window observation;
- reduced final observation through ordinary extraction output.

## Out of Scope

Summarization is out of scope. Summaries are another soft-compute producer and
would change the evidence surface. If added later, summarization must be an
explicit producer seam with its own provenance and validation contract.

Candidate-generation and candidate-menu sharding are unchanged.

## Consequences

Large document-level classifiers can stay fail-loud for impossible budgets while
handling ordinary oversized documents deterministically. Consumers retain domain
control over reducer semantics, and provider batches may contain a mixture of
single-call document requests and window calls from larger logical requests.
