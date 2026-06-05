# ADR-0026: spaCy Span-Group Candidate Strategy

**Status:** Accepted
**Date:** 2026-05-06

## Context

ADR-0017 added `NerCandidateStrategy`, an explicit spaCy-backed candidate
strategy that reads `doc.ents`. That strategy also supports typed
`EntityRuler` configuration, but the output surface remains classic spaCy
entities.

`doc.ents` is useful for traditional NER labels such as `MONEY`, `DATE`, and
`PERCENT`, but it is not the right abstraction for all extraction candidates.
It cannot naturally represent overlapping or nested domain spans. Receipt
documents frequently contain overlapping concepts:

- `$47.50` as a generic money value;
- `$47.50 subtotal` as a subtotal phrase;
- `$52.10 total due` as a total-due phrase;
- larger vendor, line-item, tax, and payment sections.

spaCy exposes a separate span-group surface through `doc.spans[...]`. Multiple
components can write to that surface:

- `SpanRuler` for deterministic rule-based spans;
- trained `SpanCategorizer` / `spancat` for labeled arbitrary spans;
- `SpanFinder + SpanCategorizer` for learned span proposal plus labeling;
- `spacy-llm` tasks such as `llm_spancat`;
- custom pipeline components.

extractx should have one seam-C strategy for consuming that span-group surface
instead of one strategy per spaCy producer.

## Decision

Add a generic spaCy span-group candidate strategy:

```python
StrategyBinding(
    cls=SpacySpanCandidateStrategy,
    kind="candidate",
    params={
        "model_id": "receipt_spans_v1",
        "spans_key": "sc",
        "label_filter": ("TOTAL_DUE",),
        "context_window_bytes": 360,
    },
)
```

The strategy loads/runs a spaCy pipeline, reads `doc.spans[spans_key]`, filters
spans by label when configured, and emits ordinary extractx `CandidateSet`
objects.

This is a sibling to `NerCandidateStrategy`, not a replacement.

## Contract

`SpacySpanCandidateStrategy` is a seam-C candidate producer. It must preserve
the same downstream contract as other candidate strategies:

- output is a canonical `CandidateSet`;
- each candidate carries a stable `candidate_id`, `source_span`, `context`,
  `entity_type`, and strategy-owned `source_id`;
- `entity_type` is the span label (`span.label_`);
- overlapping spans are allowed because they arrive as separate `Candidate`
  objects with distinct source spans and/or labels;
- selector, cardinality adapter, validator, replay, and scoring behavior are
  unchanged;
- the model never authors values or evidence ids as part of this strategy.

`doc.spans[spans_key]` is treated as the producer-owned span group. If the key
is absent, the strategy returns an empty `CandidateSet` rather than falling back
to `doc.ents`.

## Rulers

extractx already supports `EntityRuler` through `NerCandidateStrategy` because
`EntityRuler` writes to `doc.ents`.

This ADR adds the span-group counterpart:

- `EntityRuler` → `doc.ents` → `NerCandidateStrategy`;
- `SpanRuler` → `doc.spans[spans_key]` → `SpacySpanCandidateStrategy`.

Regex and literal-set strategies are already rule-based extractx-native
candidate strategies. They do not need spaCy rulers.

## Why Not SpanCat-Specific

A `SpanCatCandidateStrategy` would overfit the extractx seam to one spaCy
producer. The canonical handoff extractx needs is not "a SpanCategorizer
model"; it is "a labeled span group in `doc.spans[...]`".

Keeping the strategy generic lets schema authors start with deterministic
`SpanRuler` patterns, later swap in a trained `spancat` model, or experiment
with `llm_spancat` without changing the extractx strategy contract.

## Out of Scope

This ADR does not:

- train a span model;
- add `spacy-llm` as a runtime dependency;
- make LLM-produced spans authoritative;
- replace existing NER, regex, or literal candidate strategies;
- define domain labels for receipts;
- define candidate ranking or selector prompt changes.

`llm_spancat` may be useful for annotation bootstrapping or experiments, but
the production extraction contract remains bounded-candidate selection followed
by deterministic validation.

## Consequences

Positive:

- gives extractx a clean landing zone for overlapping domain spans;
- lets deterministic `SpanRuler` experiments and trained `spancat` models share
  one candidate strategy;
- keeps downstream seams unchanged;
- avoids duplicating spaCy span consumption logic across consumers.

Tradeoffs:

- the strategy depends on users configuring a spaCy pipeline that actually
  populates `doc.spans[spans_key]`;
- absent span groups can look like recall misses unless scoring/reporting makes
  the strategy id visible;
- trained span models still require reviewed labels and should not be inferred
  from eval fixtures alone.

## Implementation Notes

Expected typed params:

```python
class SpacySpanStrategyParams(BaseModel):
    model_id: str = "en"
    spans_key: str
    span_rulers: tuple[SpacySpanRulerConfig, ...] = ()
    filter_components: tuple[str, ...] = ()
    label_filter: tuple[str, ...] | None = None
    context_window_bytes: int = DEFAULT_CONTEXT_WINDOW_BYTES
```

`span_rulers` should be JSON-safe typed config mirroring the existing
`NerEntityRulerConfig` pattern. The implementation should reuse the existing
anchor translation, context-window, source-span validation, strategy hashing,
and candidate-id helpers from `NerCandidateStrategy`.

The first implementation should include tests with `spacy.blank("en")` plus a
configured `SpanRuler`, so CI does not require downloading a trained spaCy
model.
