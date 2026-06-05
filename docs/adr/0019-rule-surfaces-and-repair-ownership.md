# ADR-0019: Separate Rule Surfaces And Repair Ownership

**Status:** Accepted
**Date:** 2026-05-02

## Context

extractx now has typed candidate filters, pydantic-backed field validation, and
planned retry feedback surfaces. The next repair/iteration work needs clear
vocabulary so candidate filtering, field normalization, cross-field checks, and
multi-instance resolution do not collapse into a generic rule engine or leak
orchestration policy into validators.

## Decision

Use four concrete rule surfaces, each owned by its seam:

- `CandidateFilter` — candidate-set transformer:
  `CandidateSet -> CandidateSet`.
- field validation — field transformer with failure channel:
  `ProposedField + FieldSpec + schema annotation -> ValidatedField | ValidationFailure`.
- object validation — object-level diagnostic rule:
  `ObjectState -> tuple[ObjectIssue, ...]`.
- `ResolutionPolicy` — multi-instance / resolution diagnostic policy:
  `ResolutionState -> tuple[ResolutionIssue, ...]`.

These surfaces are siblings in mental model, not in public base type. Each seam
keeps concrete input, output, issue, provenance, and replay contracts. Do not
introduce a generic public `Rule[TIn, TOut, TIssue]`, `Filter`, `Validator`, or
`Policy` abstraction in v1.

Validators and policies describe facts. Execution strategies decide what to do
with those facts: retry, repair, accept with warning, escalate, prune, merge, or
fail. The issue vocabulary uses `implicates`, not `depends_on`, for repair
metadata; implicated fields or instances identify suspect state after a rule
fails and do not imply extraction ordering or a validation DAG.

## Surface Contracts

### CandidateFilter

`CandidateFilter` is a transformer over one field's candidate set. It runs after
candidate generation and before selection. ADR-0018 owns this implemented
surface.

### Field Validation

Field validation is the seam-F field layer. It produces a typed
`ValidatedField` or a typed `ValidationFailure`. For pydantic-backed schemas,
the Python annotation and its `Annotated[...]` metadata define pre-coercion and
post-coercion parsing/validation. Class-level pydantic `field_validator`s are
post-coercion in extractx's pydantic-backed path.

### Object Validation

Object validation is cross-field validation within one extracted object. It is
schema-attached, but extractx-owned in execution: a decorator may register
metadata on a schema class, while extractx invokes the rule with an object state
that includes typed values and evidence.

Target shape:

```python
@extractx_object_validator(implicates=("start_date", "end_date"))
def dates_chronological(
    values: Mapping[str, object],
    evidence: Mapping[str, Evidence],
) -> ObjectIssue | None:
    ...
```

Object validators return structured issues rather than raising exceptions.
`ObjectIssue.severity` is load-bearing: strategies may treat `"error"` as
blocking and `"warning"` as acceptable diagnostic context.

### ResolutionPolicy

Resolution policies evaluate the resolved extraction state across instances,
candidate sets, and evidence. They are not pydantic validators and should not be
schema-method sugar on an individual object class. Semantic resolution policies
belong on `ExtractionSpec`; operational defaults and retry limits belong on
`ExecutorPolicy`.

Target shape:

```python
class ResolutionPolicy(Protocol):
    policy_id: str

    def evaluate(self, state: ResolutionState) -> tuple[ResolutionIssue, ...]:
        ...
```

Resolution issues use typed refs such as `InstanceRef` and `InstanceFieldRef`
so strategies can address the exact instance or field involved.

## Consequences

The shared kernel remains visible without becoming an over-generalized public
API. Contributors can reason about each surface as a typed envelope plus typed
output, while users see concrete names that identify the seam and contract.

Retry and repair can be added incrementally. A strategy may first retry
field-level failures, then object-level issues, and only later handle selected
resolution policies. The rules themselves do not need to change when strategy
behavior evolves.

Object validators, candidate filters, and field validation are implemented.
Resolution policies remain a design commitment, not a landed runtime surface.

## Alternatives considered

- **Generic `Rule[TIn, TOut, TIssue]` base type.** Rejected. The mathematical
  shape is shared, but the seam contracts differ enough that a generic public
  abstraction would hide the important input envelope, issue refs, replay
  semantics, and strategy behavior.
- **Generic `Transformer` / `Diagnostic` hierarchy.** Rejected. It captures the
  broad taxonomy, but field validation is a transformer with a failure channel,
  while object validation and resolution policies are diagnostics. A generic
  hierarchy would either erase that distinction or force users to reason about
  abstract type parameters before they understand the concrete seam.
- **Public `Filter`, `Validator`, and `Policy` names only.** Rejected. These
  names are too broad without the envelope (`Candidate`, `Field`, `Object`,
  `Resolution`) and would turn into public junk drawers.
- **Use `depends_on` for object-validator metadata.** Rejected. These validators
  run after extraction over already-populated state. The metadata identifies
  suspect fields after failure; it does not prescribe extraction order.
- **Pydantic `model_validator` as the object-validator API.** Rejected as the
  extractx repair surface. Pydantic validators can still be useful for ordinary
  schema checks, but they raise exceptions and do not naturally receive
  extractx evidence/provenance. Repair needs structured `ObjectIssue`s with
  severity and implicated refs, produced by extractx's validation phase.
- **Exception strings as retry feedback.** Rejected. Strategies need structured
  issue data to build bounded repair prompts and replay diagnostics. Parsing
  error strings would be fragile and would hide the fields or instances actually
  implicated by a failure.
- **Model resolution policies as schema class decorators.** Rejected. Resolution
  policies operate over multiple instances, candidate sets, evidence spans, and
  grouping state. That belongs to the resolution seam and spec/policy
  configuration, not an individual object class.

## Related

- [ADR-0018: Candidate Filter Seam](0018-candidate-filter-seam.md)
- [ADR-0013: Pydantic Structured Candidate Contracts](0013-pydantic-structured-candidate-contracts.md)
- [ADR-0010: Add an instance candidate strategy seam before instance proposal](0010-instance-candidate-strategy-seam.md)
- `docs/architecture.md` seam F and seam G
