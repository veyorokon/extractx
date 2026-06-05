"""pure boundary-defining pre-plan helpers per docs/architecture.md §7 seam G.planner.

the `boundary_defining` field mechanic is flagged tentative in the
architecture doc (§7 seam G.planner, "revisit after first real multi-
instance extraction"). phase-1 lands only the pure, mechanical helpers
the iterative pre-plan C->D loop will later call; the orchestration
itself (seam C invocation, seam D invocation, Reporter emission, Budget
recording, overflow policy handling) is owned by the execution
substrate task, not by this module.

two helpers:

- `order_boundary_defining_fields(spec)` — deterministic ordering of
  the fields whose `GroupingBinding.role == "boundary_defining"`:
  priority descending, declaration-order tie-break.
- `collect_advisory_anchors(pairs)` — advisory anchor extraction from
  already-produced `(CandidateSet, Observation)` pairs, per §7 seam
  G.planner bullet "pre-plan orchestration outcomes emit trace events
  only":

    - `SELECTED`      -> selected candidates' `source_span`s
    - `AMBIGUOUS`     -> all returned ids' `source_span`s in
                         selection order
    - `ABSTAINED`     -> no anchors
    - `NO_CANDIDATES` -> no anchors

helpers are pure and mechanical. they do not:

- call `CandidateStrategy` / `Selector`
- record `UsageEvent`s
- emit `Reporter` events
- invoke seam E / seam F
- produce canonical `NegativeOutcome`s (the iterative pre-plan flow
  records these outcomes via `Reporter` for diagnostic access only;
  canonical outcomes for boundary_defining fields come from their
  per-instance run)
- deduplicate anchor spans — the planner owns dedup policy at its own
  seam

structural seam violations surface as `BoundaryHelperContractError`, a
local `ValueError` subtype mirroring `SelectionAdapterContractError`
and `ProposalValidatorContractError`. these are implementation defects
(e.g. a `Observation` whose `selected_candidate_ids` reference ids that
are not present in the paired `CandidateSet`), not typed negatives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from extractx.core.anchors import SourceSpan
    from extractx.core.objects import (
        CandidateSet,
        ExtractionSpec,
        FieldSpec,
        Observation,
    )

__all__ = [
    "BoundaryHelperContractError",
    "collect_advisory_anchors",
    "order_boundary_defining_fields",
]


class BoundaryHelperContractError(ValueError):
    """raised when a boundary-helper input violates a structural invariant.

    this is an implementation-defect failure, not a typed
    `NegativeOutcome`. it fires when:

    - a `candidate_id` in `selection.selected_candidate_ids` is not
      present in the paired `candidate_set.candidates` — seam D
      fabricated an id, or the caller handed a mismatched
      `(candidate_set, selection)` pair.

    mirrors the `SelectionAdapterContractError` /
    `ProposalValidatorContractError` shape: a local `ValueError`
    subtype, not a widened public exception surface. callers inside the
    execution substrate wrap these into diagnostics; direct callers
    should treat this as a programmer error.
    """


def order_boundary_defining_fields(spec: ExtractionSpec) -> tuple[FieldSpec, ...]:
    """return the `boundary_defining` fields of `spec` in deterministic order.

    ordering rule (architecture §7 seam G.planner "multiple
    boundary_defining fields" and §11 pre-plan pseudocode):

    - primary: `FieldSpec.priority` descending (higher priority first)
    - secondary: declaration order inside `spec.fields` (stable tie-break)

    fields whose `grouping_binding` is `None`, or whose
    `grouping_binding.role != "boundary_defining"`, are excluded. the
    helper does not mutate `spec`; the returned tuple is a new tuple.

    this helper is pure and mechanical: no reporter, no budget, no
    seam invocation.
    """

    boundary_fields: list[tuple[int, int, FieldSpec]] = []
    for declaration_index, field in enumerate(spec.fields):
        binding = field.grouping_binding
        if binding is None:
            continue
        if binding.role != "boundary_defining":
            continue
        # primary sort key: -priority so higher priority comes first;
        # secondary: declaration_index so ties preserve spec.fields order.
        boundary_fields.append((-field.priority, declaration_index, field))

    boundary_fields.sort(key=lambda triple: (triple[0], triple[1]))
    return tuple(field for _neg_priority, _idx, field in boundary_fields)


def collect_advisory_anchors(
    pairs: Sequence[tuple[CandidateSet, Observation]],
) -> tuple[SourceSpan, ...]:
    """extract advisory anchors from already-produced `(CandidateSet, Observation)` pairs.

    per §7 seam G.planner bullet "pre-plan orchestration outcomes emit
    trace events only", each `Observation` contributes to the advisory
    anchor stream as follows:

    - `SELECTED`      -> the selected candidates' `source_span`s, in
                         `selection.selected_candidate_ids` order
    - `AMBIGUOUS`     -> all returned ids' `source_span`s, in
                         `selection.selected_candidate_ids` order
    - `ABSTAINED`     -> no anchors contributed
    - `NO_CANDIDATES` -> no anchors contributed

    anchors are returned in the order pairs appear in `pairs`, and
    within each pair in the order dictated by the selection outcome
    above. duplicate anchor spans are preserved here; planner dedup
    policy lives in the planner implementation.

    raises `BoundaryHelperContractError` when a pair's `selection`
    references a `candidate_id` that is not present in its paired
    `candidate_set` — a structural seam violation.

    this helper is pure and mechanical: no reporter, no budget, no
    seam invocation.
    """

    anchors: list[SourceSpan] = []
    for candidate_set, observation in pairs:
        if observation.outcome in ("ABSTAINED", "NO_CANDIDATES"):
            continue
        if observation.outcome not in ("SELECTED", "AMBIGUOUS"):
            # defensive: the outcome literal set is closed at the
            # `Observation` type level, so any other value indicates the
            # caller constructed a `Observation` outside the documented
            # literal. surface loudly rather than silently drop.
            raise BoundaryHelperContractError(
                f"collect_advisory_anchors: unknown Observation.outcome {observation.outcome!r}",
            )
        # build an id -> span lookup once per pair. SELECTED and AMBIGUOUS
        # both walk `selection.selected_candidate_ids` to preserve the
        # order the selector declared (architecture §7 seam G.planner:
        # "AMBIGUOUS contributes all returned ids as advisory anchors in
        # this phase").
        id_to_span = {c.candidate_id: c.source_span for c in candidate_set.candidates}
        for candidate_id in observation.selected_candidate_ids:
            span = id_to_span.get(candidate_id)
            if span is None:
                raise BoundaryHelperContractError(
                    "collect_advisory_anchors: selection references "
                    f"candidate_id {candidate_id!r} that is not present in "
                    f"the paired CandidateSet (field_id="
                    f"{candidate_set.field_id!r})",
                )
            anchors.append(span)
    return tuple(anchors)
