# ADR-0036: Classification Context Strategy

**Status:** Accepted
**Date:** 2026-05-20

## Context

ADR-0014 added document-level literal classification through
`ValueKind.CATEGORY`: `LiteralSetCandidateStrategy` emits schema-owned label
candidates, and a selector returns a canonical `Observation` selecting one or
more bounded label candidate ids.

That path preserves the extraction contract, but it leaves an asymmetry with
ordinary value extraction. For value fields, grounded candidates usually carry
the document evidence the selector should inspect. For category fields, the
label candidates are synthetic labels such as `receipt`, `review`, or
`irrelevant`; they answer "what may be selected" but not "what document evidence
should be inspected."

ADR-0034 added budgeted document windows as a prompt-budget mechanism for large
document-level classification. That solves oversized full-document prompts, but
it is still a coarse context policy. It does not provide a generic way for a
schema or runtime policy to say: "for this classification field, retrieve and
render these relevant document spans/signals before asking the selector to
choose among labels."

The missing abstraction is not more category candidates. Category label
candidates already have a precise meaning. The missing abstraction is
classification context: grounded document evidence shown to the selector while
the selector chooses among label candidates.

## Decision

Add a first-class classification context/evidence retrieval seam for
`ValueKind.CATEGORY` fields.

The canonical category classification shape becomes:

```
CATEGORY field
  -> LiteralSetCandidateStrategy emits label candidates
  -> ClassificationContextStrategy emits grounded context packets
  -> selector prompt renders label candidates + context packets
  -> selector returns Observation selecting label candidate ids
```

`ClassificationContextStrategy` is an extractx-owned generic mechanism.
Consumers own the policy instances: regex pivots, signal definitions, window
sizes, limits, ranking, and benchmark thresholds.

The strategy is selector-input preparation, not semantic field definition. Its
configuration belongs with selector prompt policy / runtime selector policy, not
with `FieldSpec.description` and not as another candidate strategy for label
candidates.

## Contract

### Two Inputs to CATEGORY Selection

Category classification has two distinct inputs:

1. **Label candidates** — the bounded labels the selector may choose. These are
   ordinary `Candidate` objects from `LiteralSetCandidateStrategy`.
2. **Classification context** — grounded document windows/signals the selector
   should inspect when deciding among label candidates.

The selector output remains only a canonical `Observation` over label candidate
ids. Classification context is evidence for the selector call; it is not a
selected value and does not create new selectable ids.

### Strategy Interface

V1 should introduce a generic strategy interface with an output shape like:

```python
class ClassificationContextWindow(BaseModel):
    window_id: str
    field_id: str
    text: str
    source_span: SourceSpan
    matched_terms: tuple[str, ...] = ()
    strategy_id: str
    rank: int
    metadata: Mapping[str, Any] = {}
```

The exact model name may change during implementation, but the contract must
preserve:

- stable ids for prompt/replay references;
- `SourceSpan` provenance;
- bounded rendered text;
- strategy identity;
- enough metadata to diagnose why a window was included;
- deterministic ordering.

### Structural Parity With CandidateSet

`ClassificationContextSet` is a sibling selector-input envelope to
`CandidateSet`. It should intentionally mirror `CandidateSet` where that
supports operational parity:

- `field_id`;
- `document_id`;
- `strategy_id`;
- stable per-item ids (`window_id`, analogous to `candidate_id`);
- `text`;
- `source_kind`;
- `source_id`;
- `SourceSpan`;
- deterministic item order;
- overflow / budget metadata.

This is structural parity, not type reuse. `ClassificationContextWindow` must
not subclass or reuse `Candidate`, and `ClassificationContextSet` must not reuse
`CandidateSet`, because context windows are not selectable. They must never
enter:

- candidate filters;
- deterministic auto-selection;
- `Observation.selected_candidate_ids`;
- seam E observation adaptation;
- seam F field validation.

Implementations may share lower-level span finding, expansion, deduplication,
ranking, and budget helpers with regex/NER candidate strategies. The top-level
contracts remain separate:

```
CandidateStrategy -> CandidateSet                  # selectable
ClassificationContextStrategy -> ClassificationContextSet  # non-selectable
```

### Prompt Rendering

LLM-backed selectors render both inputs:

- the label candidate menu;
- the classification context packet.

The prompt must keep those sections distinct. A context window must not be
rendered as a label candidate, and a label candidate must not be treated as
document evidence.

### Budget Behavior

Context retrieval must be budget-aware. A strategy or planner may rank, trim, or
limit context windows according to explicit policy. If the field still cannot
fit, extractx should fail loudly with typed diagnostics rather than silently
dropping required context.

Budgeted document windows from ADR-0034 remain valid. Classification context
retrieval is a more selective evidence source; it does not replace the generic
full-document or budgeted-window modes until a policy opts into it.

### Sync and Deferred Parity

Immediate and deferred selector calls must render the same classification
context packet for the same document, field, spec, runtime policy, and budget.
Deferred execution may change provider lifecycle only; it must not change which
context windows are shown.

### Replay Diagnostics

Replay artifacts must preserve enough structure to audit selector input:

- context strategy id / producer version;
- selected context window ids;
- source spans and hashes/refs for rendered window text;
- prompt hash/ref that includes resolved context;
- field id and presented label candidate ids;
- shard/window/reducer metadata when combined with ADR-0034;
- final `Observation`.

Consumers may project these diagnostics into their own stores, but
`ReplayArtifact` is the canonical extractx record of what the selector saw.

## Ownership

Extractx owns:

- the strategy protocol;
- typed context window output;
- prompt rendering contract;
- replay diagnostics;
- budget and sync/deferred parity semantics.

Consumers own:

- domain-specific retrieval rules and patterns;
- whether regex, NER, static hints, or other deterministic mechanisms are used;
- benchmark labels and acceptance thresholds;
- promotion decisions for policy changes.

## Out of Scope

This ADR does not add:

- a new classification truth object;
- a new kind of category label candidate;
- domain-specific relevance or routing policy;
- summarization as a hidden fallback;
- an automatic LLM fallback behind context retrieval.

Summarization would be a separate soft-compute producer seam with its own
provenance and validation contract.

## Consequences

Category classification becomes symmetric with value extraction without
blurring candidate vocabulary. Value extraction selects grounded value
candidates; category classification selects label candidates while inspecting
grounded classification context.

The tradeoff is one more explicit seam. That is preferable to hiding retrieval
policy inside prompt templates, consumer prefilters, or oversized full-document
classification prompts.

## Implementation phases

- **Phase 1 — Types and policy shape:** add classification context window
  models, strategy protocol, and selector prompt policy binding.
- **Phase 2 — Reference deterministic strategy:** add a regex-window strategy
  that emits bounded context windows with `SourceSpan` provenance and stable ids.
- **Phase 3 — Prompt integration:** render label candidates and context windows
  as distinct prompt sections for immediate and batch selectors.
- **Phase 4 — Replay integration:** persist context diagnostics and include
  resolved context in prompt identity.
- **Phase 5 — Parity tests:** prove immediate/deferred parity, budget behavior,
  and replay round-trip behavior.

## Alternatives considered

- **Call context windows category candidates.** Rejected. Label candidates are
  the selectable outputs; context windows are evidence for selection.
- **Keep using full-document / budgeted-window context only.** Rejected as
  insufficient for production classifiers that need focused evidence retrieval
  and predictable token cost.
- **Implement policy in consumers only.** Useful for prototyping, but rejected
  as the durable shape because selector prompts and replay diagnostics would
  depend on untyped external preprocessing.
- **Make classification context a candidate strategy.** Rejected because
  candidate strategies produce selectable candidates. Classification context is
  not selectable.

## Related

- [`0014-document-level-literal-classification.md`](0014-document-level-literal-classification.md)
- [`0030-selector-example-fixtures.md`](0030-selector-example-fixtures.md)
- [`0031-selector-call-diagnostics-in-replay.md`](0031-selector-call-diagnostics-in-replay.md)
- [`0034-budgeted-document-classification.md`](0034-budgeted-document-classification.md)
- [`0035-rule-based-category-selector.md`](0035-rule-based-category-selector.md)
