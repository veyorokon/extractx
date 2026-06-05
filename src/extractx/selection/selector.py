"""`Selector` base / shared machinery per docs/architecture.md §7 seam D.

this module holds the selector-boundary enforcement wrapper that every
`Selector` impl — algorithmic or llm-backed — should route its observation
through. the wrapper enforces the id-only contract
(`selected_candidate_ids ⊆ input candidate_ids`) and a minimal shape check
on `Observation.outcome` so fabrication is caught at the seam rather than
leaking downstream.

the default llm-backed `Selector` ships in `extras/pydantic_ai/` per
ADR-0002; a phase-1 algorithmic impl lives under `selection.algorithmic`.
both funnel their raw `Observation` through `enforce_observation_contract` so
the id-only guarantee is one code path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from extractx.core import CandidateSet, Observation

__all__ = [
    "SelectorContractError",
    "enforce_batch_observation_contract",
    "enforce_observation_contract",
    "enforce_selection_contract",
]


class SelectorContractError(ValueError):
    """raised when a selector impl violates the seam-D id-only contract.

    this is an internal failure mode: the impl emitted a `Observation` whose
    `selected_candidate_ids` are not a subset of the input candidate ids,
    or whose outcome shape disagrees with the id set in a way the seam
    contract does not permit.

    it is deliberately a `ValueError` subtype (not a typed `NegativeOutcome`
    or a public `SpecError`) because it names an implementation defect in
    the selector, not a data defect in the document or a spec defect. the
    executor wraps selector calls and converts these to the appropriate
    typed diagnostics; callers of the enforcement helper directly should
    treat this as a programmer error.
    """


def enforce_observation_contract(
    observation: Observation,
    candidate_set: CandidateSet,
) -> Observation:
    """validate a raw `Observation` against the seam-D id-only contract.

    returns the same `Observation` unchanged when valid. raises
    `SelectorContractError` when the impl fabricated ids or chose an
    outcome shape that disagrees with its id set in a way the contract
    forbids.

    the checks are deliberately minimal:

    - `selected_candidate_ids` must be a subset of
      `candidate_set.candidates`' ids (no fabrication).
    - `NO_CANDIDATES` must carry an empty id tuple, and must only be
      emitted when the input set was itself empty.
    - `AMBIGUOUS` must carry at least one id.
    - `SELECTED` may carry an empty id tuple. seam D is cardinality-agnostic;
      seam E interprets empty selected sets by field cardinality.
    - `ABSTAINED` must carry an empty id tuple.

    cardinality mapping (seam E) is not enforced here. the selector is
    permitted to return any subset of the input ids; the cardinality table
    at seam E consumes that subset.
    """

    input_ids = tuple(c.candidate_id for c in candidate_set.candidates)
    input_id_set = set(input_ids)
    selected_ids = observation.selected_candidate_ids

    # no fabrication — every returned id must be present in the input set.
    stray = [cid for cid in selected_ids if cid not in input_id_set]
    if stray:
        raise SelectorContractError(
            "selector violated id-only contract: returned candidate_ids "
            f"{stray!r} are not present in the input CandidateSet "
            f"(field_id={candidate_set.field_id!r}, "
            f"document_id={candidate_set.document_id!r})",
        )

    # outcome-shape sanity. these rules are drawn directly from
    # docs/architecture.md §7 seam D and §9 `Observation`.
    outcome = observation.outcome
    if outcome == "NO_CANDIDATES":
        if selected_ids:
            raise SelectorContractError(
                "selector emitted NO_CANDIDATES with non-empty "
                f"selected_candidate_ids={selected_ids!r}",
            )
        if input_ids:
            raise SelectorContractError(
                "selector emitted NO_CANDIDATES for a non-empty input "
                f"CandidateSet (len={len(input_ids)}); use AMBIGUOUS or "
                "ABSTAINED instead",
            )
    elif outcome == "ABSTAINED":
        if selected_ids:
            raise SelectorContractError(
                "selector emitted ABSTAINED with non-empty "
                f"selected_candidate_ids={selected_ids!r}",
            )
    elif outcome == "AMBIGUOUS":
        if not selected_ids:
            raise SelectorContractError(
                f"selector emitted {outcome} with empty "
                "selected_candidate_ids; use NO_CANDIDATES or ABSTAINED",
            )

    return observation


def enforce_batch_observation_contract(
    observations: tuple[Observation, ...],
    candidate_sets: tuple[CandidateSet, ...],
    *,
    require_all: bool = True,
) -> tuple[Observation, ...]:
    """validate and canonically order batch selector observations.

    ADR-0023 keeps batch selection on the same id-only contract as
    single-field seam D, then adds batch-level shape checks:

    - no observation for an unknown field;
    - no duplicate `(field_id, instance_id)` observation;
    - every input field receives an observation when `require_all=True`;
    - returned order follows input `candidate_sets` field order.
    """

    by_field = {candidate_set.field_id: candidate_set for candidate_set in candidate_sets}
    if len(by_field) != len(candidate_sets):
        duplicate_fields: list[str] = []
        seen: set[str] = set()
        for candidate_set in candidate_sets:
            if candidate_set.field_id in seen:
                duplicate_fields.append(candidate_set.field_id)
            seen.add(candidate_set.field_id)
        raise SelectorContractError(
            "batch selector input has duplicate CandidateSet field_id values: "
            f"{duplicate_fields!r}",
        )

    seen_keys: set[tuple[str | None, str | None]] = set()
    by_observation_field: dict[str, Observation] = {}
    for observation in observations:
        field_id = observation.field_id
        if field_id not in by_field:
            raise SelectorContractError(
                "batch selector emitted observation for unknown field_id "
                f"{field_id!r}; allowed={sorted(by_field)!r}",
            )
        key = (field_id, observation.instance_id)
        if key in seen_keys:
            raise SelectorContractError(
                "batch selector emitted duplicate observation for "
                f"(field_id, instance_id)={key!r}",
            )
        seen_keys.add(key)
        by_observation_field[field_id] = enforce_observation_contract(
            observation,
            by_field[field_id],
        )

    missing = [field_id for field_id in by_field if field_id not in by_observation_field]
    if require_all and missing:
        raise SelectorContractError(
            "batch selector omitted observations for field_id values "
            f"{missing!r}",
        )

    return tuple(
        by_observation_field[candidate_set.field_id]
        for candidate_set in candidate_sets
        if candidate_set.field_id in by_observation_field
    )


# Temporary compatibility wrapper for pre-ADR-0008 callers.
def enforce_selection_contract(
    selection: Observation,
    candidate_set: CandidateSet,
) -> Observation:
    return enforce_observation_contract(selection, candidate_set)
