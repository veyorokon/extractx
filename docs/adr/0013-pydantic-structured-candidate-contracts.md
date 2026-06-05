# ADR-0013: Pydantic Structured Candidate Contracts

## Status

Accepted.

## Context

extractx is adding deterministic structured candidate sources such as structured records.
These sources differ from text candidates. A text candidate is found by
interpreting natural language and must always go through selector arbitration.
A structured candidate is emitted by a source format that can carry its own
self-describing identity: concept/key, unit, context/scope, precision, and a
schema-like payload.

The architectural question is when a selector is strictly unnecessary for a
field. Priority numbers, confidence tiers, and opaque auto-accept policy enums
all hide semantic policy in labels. The honest rule is narrower:

- text candidates never auto-select
- structured candidates can auto-select only when their structural contract
  proves exactly one candidate is eligible

The contract language should not become a new extractx DSL. Pydantic already
has the constraint machinery extractx needs: `Literal` / enum membership,
`Field(ge=..., le=...)` ranges, and `Annotated[..., AfterValidator(...)]` or
validator methods for named predicates.

## Decision

Structured sources author ordinary pydantic models as their contracts.

Contract evaluation is:

```python
contract_class.model_validate(candidate_payload)
```

If validation succeeds, the candidate receives:

```python
StructuralStatus(passed=True, contract_id="<module>.<qualname>")
```

If validation fails with semantic contract errors, extractx adapts pydantic's
typed field metadata into:

```python
StructuralFailure:
  field: str
  actual: ConstraintValue
  expected: SetConstraint | RangeConstraint | PredicateConstraint
```

No separate failure-code namespace exists. Audit and UI consumers dispatch on
`field` and `expected.kind`.

Predicate validators must expose a stable name. Class-based validators get a
stable name from their class name; callable validators may implement the
`NamedPredicate` protocol with a `name: str`.

Hard malformed structured facts are not candidates. They should surface as
parse/source diagnostics before `Candidate` construction. The structural
contract layer handles semantic contract failures on typed payloads, not raw
format repair.

`Candidate` gains:

```python
source_kind: Literal["structured", "text"] = "text"
source_id: str
structural_status: StructuralStatus | None
```

Invariants:

- `source_kind="text"` requires `structural_status is None`
- `source_kind="structured"` requires `structural_status is not None`
- `StructuralStatus(passed=True)` requires no failures
- `StructuralStatus(passed=False)` requires at least one failure
- failed structured candidates remain selector-visible with typed failures

The deterministic selection gate is independent of candidate generation:

```python
eligible = [
    c for c in candidate_set.candidates
    if c.source_kind == "structured"
    and c.structural_status.passed
]

if len(eligible) == 1 and not require_corroboration:
    auto-select eligible[0]
else:
    invoke selector over the bounded CandidateSet
```

Source declaration order has no authority semantics. It may affect stable
output formatting and candidate id determinism, but never selection authority.

## Consequences

Structured candidate sources such as structured records can become deterministic wins
without forking the extraction pipeline. They emit normal `Candidate`s with
structured status; the same selector contract handles the ambiguous cases.

Text sources such as regex, NER, and prose-grounded LLM candidates remain
bounded inputs to the selector and never earn auto-accept by label.

The first implementation ships the reusable contract kernel and deterministic
gate. Candidate-source composition and concrete structured records parsing are follow-up
work.

## Alternatives Rejected

- **Priority numbers or confidence tiers.** These smuggle authority through
  labels and make `50` versus `25` meaningless without hidden policy.
- **Per-source failure enums.** They fragment audit vocabulary and push
  source-specific detail through the public contract.
- **A custom constraint DSL.** Pydantic already expresses the required
  constraints idiomatically and is already the validation shape used by
  extractx and downstream consumers.
- **Dropping failed structured candidates.** The selector and auditor need to
  see failed-but-present structured candidates and their typed failure reasons.
