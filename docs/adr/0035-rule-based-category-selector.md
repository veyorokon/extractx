# ADR-0035: Rule-Based Category Selector

**Status:** Accepted
**Date:** 2026-05-20

## Context

Extractx already supports document-level literal/category classification through
the ordinary selection seam: `LiteralSetCandidateStrategy` emits schema-owned
literal candidates, and a selector backend returns a canonical `Observation`.
The first supported soft-compute backends are LLM-backed selectors, but some
category decisions can be made deterministically from explicit document signals
without changing the field schema, candidate literals, observation contract, or
replay model.

The decision in front of us is whether deterministic classification should be a
new classification layer or another selector backend behind the existing
literal/category seam.

## Decision

Add a generic rule-based selector backend for `ValueKind.CATEGORY` fields.

The selector consumes the same `FieldSpec`, `CandidateSet`, and `ContextPack`
as other seam-D selectors and emits the same canonical `Observation`. It must
not introduce a separate classification product, domain-specific relevance
surface, or alternate truth object.

## Contract

### Same Selection Seam

The canonical path remains:

```
Literal/CATEGORY field
  -> LiteralSetCandidateStrategy
  -> selector backend
  -> Observation
```

Selector backend may be LLM-backed, batch LLM-backed, or rule-based. Callers can
compare selector backends without changing field definitions, candidate ids, or
downstream validation/adaptation contracts.

### Typed Rule Policy

Rule policy is typed data supplied through selector binding params. V1 policy is
regular-expression based, but the selector contract must not expose regex as
truth. Regexes are one implementation for producing typed signals.

The generic rule surface should include:

- target category literal / candidate literal;
- rule id;
- pattern or matcher config;
- polarity: `positive`, `negative`, or `ambiguous`;
- strength or priority;
- optional flags such as case sensitivity / multiline behavior.

Rules are consumer-owned policy. Extractx owns only the mechanics and contract.

### Replayable Signals

The rule-based selector emits structured signal diagnostics for every matched
rule:

```
CategorySignal:
  signal_id: str
  rule_id: str
  candidate_literal: str
  candidate_id: str | None
  polarity: Literal["positive", "negative", "ambiguous"]
  strength: str | float
  text: str
  source_span: SourceSpan
  metadata: Mapping[str, Any]
```

Signals are diagnostic evidence for why the deterministic selector chose,
abstained, or found a conflict. They are not separate extracted field truth.

### Fail-Open Semantics

The deterministic selector must be conservative:

- no sufficient signal -> abstain;
- conflicting sufficient signals -> abstain;
- malformed policy -> typed infrastructure/spec failure;
- selected category literal must map to a bounded candidate id;
- if the schema includes an explicit uncertainty literal, policy may select it;
  otherwise uncertainty is represented by `Observation(outcome="ABSTAINED")`.

Fail-open behavior keeps deterministic rules from silently becoming authority
when evidence is weak or conflicting.

### Cardinality

For single-label category fields, the rule selector selects at most one
candidate. For `Cardinality.MANY`, it may select every candidate whose rule
signals satisfy policy, preserving candidate order and deduplicating ids.

Empty multi-label selection is a successful empty set:
`outcome="SELECTED"`, `abstain=false`, `selected_candidate_ids=()`.

### Hybrid Compatibility

The selector itself must not hide an LLM fallback call. A hybrid flow such as
"run deterministic selector, then run an LLM selector when the deterministic
selector abstains" belongs to executor/runtime orchestration or a consumer
workflow. Keeping fallback orchestration outside the selector preserves replay
clarity and makes deterministic-only, LLM-only, and hybrid modes benchmarkable.

## Consequences

The existing category classification seam becomes backend-polymorphic without
adding a new canonical object. Deterministic category classification gains the
same candidate-id enforcement and downstream behavior as LLM classification.

The tradeoff is that rule policy, signal diagnostics, and replay shape must be
formalized rather than treated as ad hoc regex prefilter code. Consumers must
benchmark rule policies against their own labels and should treat false drops as
the primary safety metric.

## Implementation phases

- **Phase 1 — Core selector backend:** add typed rule/signal objects,
  `RuleBasedCategorySelector`, unit tests for single-label and multi-label
  CATEGORY fields, and selector diagnostics for matched signals.
- **Phase 2 — Replay integration:** persist rule signals through selector-call
  diagnostics or a typed sibling diagnostic surface with schema-version
  discipline.
- **Phase 3 — Orchestration compatibility:** add a small helper or example for
  deterministic-only / LLM-only / hybrid comparison without hiding fallback
  calls inside the selector implementation.

## Alternatives considered

- **Separate deterministic classification layer:** rejected because it creates a
  second category-classification contract parallel to seam D and risks producing
  routing facts that bypass `Observation`.
- **Candidate strategy emits deterministic verdict candidates:** rejected
  because literal/category candidates are already schema-owned labels. The
  deterministic work is choosing among those labels, not generating new labels.
- **Consumer-owned prefilter only:** useful for discovery, but insufficient as
  an extractx contract because replayable selector signals and observation
  parity would remain outside the selection seam.

## Related

- [`0014-document-level-literal-classification.md`](0014-document-level-literal-classification.md)
- [`0023-batch-selector-observations.md`](0023-batch-selector-observations.md)
- [`0031-selector-call-diagnostics-in-replay.md`](0031-selector-call-diagnostics-in-replay.md)
- [`0034-budgeted-document-classification.md`](0034-budgeted-document-classification.md)
