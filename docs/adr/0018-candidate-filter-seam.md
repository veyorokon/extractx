# ADR-0018: Candidate Filter Seam

Date: 2026-05-02

## Status

Accepted

## Context

Candidate strategies source possible evidence from a document. Selectors choose
from a bounded candidate set. Some useful policy belongs in neither place:

- remove numeric subspans that are contained inside date spans
- keep only candidates with a specific semantic label
- drop numeric candidates outside a plausible range
- require local context vocabulary before a candidate reaches selection

Putting this policy inside each strategy duplicates behavior across regex, NER,
structured records, and future structured sources. Putting it in selector prompts leaves
deterministic filtering to soft compute.

The seam is:

- `CandidateStrategy`: `DocumentView -> CandidateSet`
- `CandidateFilter`: `CandidateSet -> CandidateSet`
- `Selector`: `CandidateSet -> Observation`

## Decision

Add field-level `filter_binding` to `FieldSpec` and `extract_field(...)`.
Filters run after candidate generation and before deterministic selection or
LLM selection.

`FilterBinding` carries a typed, serializable predicate AST, not a callable and
not a free-form string DSL. The initial expression vocabulary is:

- `LabelIn`
- `LabelNotIn`
- `ContainedBy`
- `Contains`
- `NumericRange`
- `ContextContains`
- `And`
- `Or`
- `Not`

Filter expressions are frozen pydantic models and participate in spec version
hashing and spec summaries.

The filter expression is evaluated against the generated `CandidateSet`. Use
`And` / `Or` / `Not` to compose predicates inside the single binding; v1 does
not expose an ordered filter pipeline.

Scalar filters evaluate `Candidate.normalized_hint` first when it is present.
When a strategy emits no hint, scalar filters use the shared candidate-level
coercion helper. That helper handles common numeric money forms, including
scale words and suffixes such as `$42.1 million` / `$42.1M`, and rejects
ambiguous multi-number text rather than guessing. This is candidate-level
admission logic only; seam F remains the owner of final field normalization and
validation.

## Consequences

Strategies remain broad source adapters. Filters own reusable deterministic
refinement. Selectors receive the bounded post-filter `CandidateSet`.

We explicitly reject for v1:

- callable filters in `StrategyBinding.params`
- a string query language / parser
- an external generic expression library
- source-specific filtering hidden inside the NER strategy

Dynamic extension is deferred. New operators should be added by widening the
typed AST through a reviewed PR, with tests and documentation. That preserves
replay stability and avoids a runtime plugin registry at this seam.
