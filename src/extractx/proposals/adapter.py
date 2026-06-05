"""`ObservationAdapter` (cardinality-aware) per docs/architecture.md §7 seam E.

phase 1: one deterministic `CardinalitySelectionAdapter` that consumes a
real `Observation + CandidateSet + FieldSpec` and returns either a tuple of
`ProposedField`s or exactly one typed `NegativeOutcome`, per the seam-E
cardinality table.

seam-E lifecycle boundary — enforced here:

- this seam produces `ProposedField`. it does **not** normalize, invoke
  pydantic or extractx validators, call an llm, emit a `UsageEvent`, or
  resolve instance keys. normalization happens exactly once, later, at
  seam F layer 2.
- emitted `ProposedField.normalized_hint` carries `Candidate.normalized_hint`
  through unchanged — it is a hint from seam C, not a seam-E output.
- `evidence_text` is deliberately the selected `Candidate.text`; seam E
  does not inspect or copy `Candidate.context`.

structural seam violations (field-id disagreement between `candidate_set`
and `field_spec`, a selected id that is not in the input set, a duplicate
selected id emitted by seam D) fail loudly as
`SelectionAdapterContractError` — a local `ValueError` subtype. these are
implementation defects, not typed negatives.

cardinality-mismatch and `empty_selection` negatives emitted from the
`SELECTED` path carry `reason=code`. no prose from candidate content, no
dynamic `k` interpolation. every seam-E `NegativeOutcome` carries
`candidate_count=len(candidate_set.candidates)` so diagnostics answer
"how many candidates existed at the seam" rather than "how many ids the
selector returned."
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from extractx.core.cardinality import Cardinality
from extractx.core.objects import InstanceGroupingKey
from extractx.core.outcomes import NegativeOutcome, ProposedField

if TYPE_CHECKING:
    from extractx.core.objects import Candidate, CandidateSet, FieldSpec, Observation

__all__ = [
    "CardinalitySelectionAdapter",
    "SelectionAdapterContractError",
]


class SelectionAdapterContractError(ValueError):
    """raised when a seam-E input triple violates a structural invariant.

    this is an implementation-defect failure, not a typed `NegativeOutcome`.
    it fires when:

    - `candidate_set.field_id != field_spec.field_id` — the adapter was
      handed a `CandidateSet` and a `FieldSpec` that disagree on the
      field being adapted;
    - a `candidate_id` in `selection.selected_candidate_ids` is not present
      in `candidate_set.candidates` — seam D fabricated an id or the
      adapter was handed a mismatched `(selection, candidate_set)` pair;
    - `selection.selected_candidate_ids` contains a duplicate id — a seam-D
      contract violation (seam E does not silently deduplicate).

    mirrors the seam-D `SelectorContractError` shape: a local `ValueError`
    subtype, not a widened public exception surface. the executor wraps
    adapter calls and converts these into the appropriate diagnostic;
    direct callers should treat this as a programmer error.
    """


class CardinalitySelectionAdapter:
    """phase-1 deterministic `SelectionAdapter` per docs/architecture.md §7
    seam E.

    dispatch is structured as:

    1. reject structural seam violations loudly
       (`SelectionAdapterContractError`);
    2. if `selection.outcome != "SELECTED"`, emit one `NegativeOutcome`
       with `category="selection"` and `code=selection.outcome.lower()`;
    3. otherwise, dispatch the `SELECTED` path by `FieldSpec.cardinality`
       and `k = len(selection.selected_candidate_ids)`.

    `Cardinality.PER_INSTANCE` is treated as `Cardinality.ONE` within the
    provided `candidate_set.instance_hint` — per-instance iteration is a
    strategy concern owned by the iterative strategy task, not seam E.

    the adapter holds no configurable state; two instances produce
    byte-identical output for the same input.
    """

    def adapt(
        self,
        observation: Observation | None = None,
        candidate_set: CandidateSet | None = None,
        field_spec: FieldSpec | None = None,
        *,
        selection: Observation | None = None,
    ) -> tuple[ProposedField, ...] | NegativeOutcome:
        if observation is None:
            observation = selection
        if observation is None or candidate_set is None or field_spec is None:
            raise SelectionAdapterContractError(
                "CardinalitySelectionAdapter.adapt requires observation, "
                "candidate_set, and field_spec",
            )
        _enforce_structural_invariants(observation, candidate_set, field_spec)

        if observation.outcome != "SELECTED":
            return _non_selected_negative(observation, candidate_set, field_spec)

        k = len(observation.selected_candidate_ids)
        cardinality = field_spec.cardinality

        # `per_instance` → treat as `one` within `candidate_set.instance_hint`.
        # per-instance iteration is a strategy concern, not seam E.
        if cardinality is Cardinality.PER_INSTANCE or cardinality is Cardinality.ONE:
            return _dispatch_one(k, observation, candidate_set, field_spec)
        if cardinality is Cardinality.OPTIONAL:
            return _dispatch_optional(k, observation, candidate_set, field_spec)
        if cardinality is Cardinality.MANY:
            return _dispatch_many(observation, candidate_set, field_spec)

        # StrEnum is closed; this is unreachable under the current
        # `Cardinality` surface. fail loudly rather than silently return
        # an empty tuple if a new variant is added without updating this
        # dispatch.
        raise SelectionAdapterContractError(
            f"CardinalitySelectionAdapter: unhandled cardinality {cardinality!r} "
            f"for field_id={field_spec.field_id!r}",
        )


# ---------------------------------------------------------------------------
# structural invariants — loud, not typed `NegativeOutcome`
# ---------------------------------------------------------------------------


def _enforce_structural_invariants(
    observation: Observation,
    candidate_set: CandidateSet,
    field_spec: FieldSpec,
) -> None:
    """fail loudly on seam-E structural violations. see
    `SelectionAdapterContractError` for the rules enforced.
    """

    if candidate_set.field_id != field_spec.field_id:
        raise SelectionAdapterContractError(
            "CardinalitySelectionAdapter: candidate_set.field_id "
            f"{candidate_set.field_id!r} does not match field_spec.field_id "
            f"{field_spec.field_id!r}",
        )

    # duplicate-id check runs independent of resolvability: a duplicated
    # id is a seam-D contract violation regardless of whether the id
    # itself resolves.
    seen: set[str] = set()
    duplicates: list[str] = []
    for cid in observation.selected_candidate_ids:
        if cid in seen:
            duplicates.append(cid)
        else:
            seen.add(cid)
    if duplicates:
        raise SelectionAdapterContractError(
            "CardinalitySelectionAdapter: selection.selected_candidate_ids "
            f"contains duplicate ids {duplicates!r} (seam-D contract violation; "
            "seam E does not deduplicate)",
        )

    # resolvability — every selected id must appear in candidate_set.candidates.
    available_ids = {c.candidate_id for c in candidate_set.candidates}
    missing = [cid for cid in observation.selected_candidate_ids if cid not in available_ids]
    if missing:
        raise SelectionAdapterContractError(
            "CardinalitySelectionAdapter: selection.selected_candidate_ids "
            f"contains ids {missing!r} that are not present in "
            f"candidate_set.candidates for field_id={field_spec.field_id!r}",
        )


# ---------------------------------------------------------------------------
# non-SELECTED path
# ---------------------------------------------------------------------------


def _non_selected_negative(
    observation: Observation,
    candidate_set: CandidateSet,
    field_spec: FieldSpec,
) -> NegativeOutcome:
    """map a non-`SELECTED` outcome into one `NegativeOutcome`.

    - `category="selection"` — seam-E phase 1 maps every non-`SELECTED`
      seam-D outcome into the `selection` domain bucket. the architecture
      prose "category from outcome" resolves to a stable category literal
      at this seam.
    - `code=selection.outcome.lower()` — the seam-D outcome literal
      (`NO_CANDIDATES` / `AMBIGUOUS` / `ABSTAINED`) becomes the code.
    - `reason=selection.reason or selection.outcome.lower()` — fall back
      to the code when seam D emitted no prose.
    """

    code = observation.outcome.lower()
    return NegativeOutcome(
        category="selection",
        code=code,
        field_id=field_spec.field_id,
        instance_key=candidate_set.instance_hint,
        reason=observation.reason or code,
        candidate_count=len(candidate_set.candidates),
    )


# ---------------------------------------------------------------------------
# SELECTED path — per-cardinality dispatchers
# ---------------------------------------------------------------------------


def _dispatch_one(
    k: int,
    observation: Observation,
    candidate_set: CandidateSet,
    field_spec: FieldSpec,
) -> tuple[ProposedField, ...] | NegativeOutcome:
    if k == 0:
        return _cardinality_negative(
            category="adaptation",
            code="empty_selection",
            candidate_set=candidate_set,
            field_spec=field_spec,
        )
    if k == 1:
        return tuple(_build_proposed_fields(observation, candidate_set, field_spec))
    return _cardinality_negative(
        category="validation",
        code="cardinality.one_expected_many_selected",
        candidate_set=candidate_set,
        field_spec=field_spec,
    )


def _dispatch_optional(
    k: int,
    observation: Observation,
    candidate_set: CandidateSet,
    field_spec: FieldSpec,
) -> tuple[ProposedField, ...] | NegativeOutcome:
    if k == 0:
        return _cardinality_negative(
            category="selection",
            code="abstained",
            candidate_set=candidate_set,
            field_spec=field_spec,
        )
    if k == 1:
        return tuple(_build_proposed_fields(observation, candidate_set, field_spec))
    return _cardinality_negative(
        category="validation",
        code="cardinality.optional_expected_many_selected",
        candidate_set=candidate_set,
        field_spec=field_spec,
    )


def _dispatch_many(
    observation: Observation,
    candidate_set: CandidateSet,
    field_spec: FieldSpec,
) -> tuple[ProposedField, ...]:
    # `k = 0` → empty tuple is a valid MANY outcome; no negative emitted.
    # `k >= 1` → one `ProposedField` per selected id in selected-id order.
    return tuple(_build_proposed_fields(observation, candidate_set, field_spec))


# ---------------------------------------------------------------------------
# projection and helpers
# ---------------------------------------------------------------------------


def _cardinality_negative(
    *,
    category: str,
    code: str,
    candidate_set: CandidateSet,
    field_spec: FieldSpec,
) -> NegativeOutcome:
    """emit a `NegativeOutcome` from the `SELECTED` path where the
    cardinality table forbids translating the selection into
    `ProposedField`s.

    per the brief: `reason=code` (no prose from candidate content, no
    dynamic `k` interpolation). `candidate_count` answers "how many
    candidates existed at the seam" — the full `candidate_set` size, not
    `k`.
    """

    # mypy/pyright: `NegativeOutcome.category` is a closed Literal; narrow
    # via a static check. `type: ignore` is avoided by asserting at call
    # sites that the category literal is one of the documented members.
    assert category in ("adaptation", "validation", "selection"), (
        f"_cardinality_negative: unsupported category={category!r}"
    )
    return NegativeOutcome(
        category=category,  # pyright: ignore[reportArgumentType]
        code=code,
        field_id=field_spec.field_id,
        instance_key=candidate_set.instance_hint,
        reason=code,
        candidate_count=len(candidate_set.candidates),
    )


def _build_proposed_fields(
    observation: Observation,
    candidate_set: CandidateSet,
    field_spec: FieldSpec,
) -> Iterable[ProposedField]:
    """project each selected `Candidate` into a `ProposedField`.

    selected-id order is preserved exactly. field projection is direct —
    no synthesis, no reslicing of document text, no inspection of
    `Candidate.context`, no normalization.
    """

    by_id: dict[str, Candidate] = {c.candidate_id: c for c in candidate_set.candidates}
    for cid in observation.selected_candidate_ids:
        candidate = by_id[cid]
        yield ProposedField(
            field_id=field_spec.field_id,
            tentative_instance_key=_tentative_instance_key(
                observation=observation,
                candidate_set=candidate_set,
                candidate=candidate,
            ),
            raw_value=candidate.text,
            evidence_text=candidate.text,
            source_span=candidate.source_span,
            evidence_spans=candidate.evidence_spans,
            normalized_hint=candidate.normalized_hint,
            candidate_id_refs=(candidate.candidate_id,),
            strategy_id=candidate_set.strategy_id,
            selector_producer_version=observation.producer_version,
            grounded_producer_version=None,
        )


def _tentative_instance_key(
    *,
    observation: Observation,
    candidate_set: CandidateSet,
    candidate: Candidate,
) -> InstanceGroupingKey | None:
    if observation.instance_id is None:
        return candidate_set.instance_hint
    if candidate_set.instance_hint is not None and (
        candidate_set.instance_hint.group_id == observation.instance_id
    ):
        return candidate_set.instance_hint
    return InstanceGroupingKey(
        group_id=observation.instance_id,
        ordinal=0,
        group_anchors=(candidate.source_span,),
    )
