# ADR-0021: Make Candidate Strategy Composition First-Class

**Status:** Accepted
**Date:** 2026-05-04

## Context

`FieldSpec.strategy_binding` is singular today: one field names one
`CandidateStrategy`, and the executor calls that strategy to produce one
`CandidateSet`. That shape kept the early seam C contract small, but real
schemas now need strategy composition: for example, broad NER candidates plus
targeted regex candidates for layout or phrasing cases a single source misses.

Two implementation shapes are available. A `HybridCandidateStrategy` could hide
composition inside one strategy class while preserving the singular field
contract. Or `FieldSpec` can carry plural strategy bindings and make the
executor own composition, attribution, and deduplication.

## Decision

Adopt plural candidate strategy bindings as the target contract:

```python
strategy_bindings: tuple[StrategyBinding, ...]
```

The executor owns candidate-strategy composition for a field. It runs each
binding deterministically, merges the resulting candidates into one canonical
`CandidateSet`, applies the field `filter_binding` once after the merge, and
then hands the bounded post-filter set to selection.

The existing singular `strategy_binding` field is removed rather than kept as a
compatibility alias. One strategy is represented as a one-element
`strategy_bindings` tuple. The migration fixes existing schemas, tests, docs,
and consumers in the same thread.

## Consequences

Composition becomes visible in the spec instead of hidden in a special wrapper
strategy. Attribution is also cleaner: each child candidate keeps its producing
strategy identity, and benchmark reports can explain which strategy found the
gold evidence.

The merge/deduplication policy becomes core-owned and tested once. That avoids
multiple hybrid wrappers inventing slightly different candidate-union
semantics.

This is a public contract migration. It touches pydantic metadata,
`FieldSpec`, spec hashing, summaries, executor validation, replay assumptions,
benchmark scoring, docs, and tests. Because extractx has a small active
consumer set, the migration should gut the singular path and fix the breaks
rather than preserve two overlapping authoring surfaces.

The executor still emits one `CandidateSet` per field after composition. This
preserves the downstream C -> D seam: selectors continue to see one bounded
candidate set for one field, not separate strategy streams.

## Implementation phases

- **Phase 1 — contract replacement:** replace `strategy_binding` with
  `strategy_bindings` in field metadata, `FieldSpec`, schema compile,
  summaries, and hashing. Fix existing schemas/tests to use one-element tuples
  for single-strategy fields. Complete when no runtime code reads the singular
  attribute.
- **Phase 2 — executor composition:** update executor validation and candidate
  generation to run all field strategy bindings, merge candidates
  deterministically, dedupe exact duplicates, and apply `filter_binding` after
  merge; prove one-binding behavior is unchanged.
- **Phase 3 — attribution and replay:** preserve per-candidate producer
  strategy identity through merged candidate sets, replay artifacts, and
  benchmark reports; prove `score_candidates(...)` can report which strategy
  produced a matched candidate.
- **Phase 4 — docs and examples:** update docs/examples to author
  `strategy_bindings`; complete when searches for `strategy_binding` only find
  historical ADR prose or explicit rejected-alternative text.

## Alternatives considered

- **HybridCandidateStrategy:** rejected as the target shape. It preserves the
  singular contract, but composition becomes hidden inside a strategy that takes
  other strategies as params. That makes attribution and deduplication easy to
  lose, and it puts canonical merge policy in a strategy implementation rather
  than the executor seam that owns composition.
- **Keep singular `strategy_binding` as a compatibility alias:** rejected. It
  would create a duplicate overlapping path at a core spec seam. Early
  contract migration is cheaper now than carrying two authoring surfaces and
  later discovering drift between them.
- **Do nothing until a confirmed grounding gap requires it:** rejected as an
  architectural decision, accepted as implementation sequencing. The direction
  should be recorded now; implementation can still wait for a concrete
  grounding gap that one strategy cannot cover.

## Related

- [ADR-0017: spaCy NER Candidate Strategy](0017-spacy-ner-candidate-strategy.md)
- [ADR-0018: Candidate Filter Seam](0018-candidate-filter-seam.md)
- [ADR-0020: Ship Benchmark Primitives, Not A Benchmark Product](0020-benchmark-primitives-over-benchmark-product.md)
- [Architecture: seam C and C.filter](../architecture.md)
