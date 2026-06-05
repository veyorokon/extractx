"""behavioral tests for the pure boundary-defining pre-plan helpers.

proof targets (from
docs/tasks/seam-g-planner-phase-1-structural-instance-planner.md,
"Focused proof"):

- `order_boundary_defining_fields` sorts by priority descending, then
  declaration order, and excludes fields whose `grouping_binding` is
  `None` or whose role is not `"boundary_defining"`.
- `collect_advisory_anchors`:
    - includes `SELECTED` ids' spans;
    - includes `AMBIGUOUS` ids' spans in selection order;
    - ignores `ABSTAINED` / `NO_CANDIDATES`;
    - preserves duplicates (planner owns dedup);
    - raises `BoundaryHelperContractError` on structural seam
      violations (e.g. an id in `selection.selected_candidate_ids`
      that is not present in the paired `CandidateSet`).
- no seam E/F behavior is smuggled into the helpers.
"""

from __future__ import annotations

import pytest

from extractx.core import (
    Candidate,
    CandidateSet,
    Cardinality,
    DistanceMetric,
    ExtractionSpec,
    FieldSpec,
    GroupingBinding,
    GroupingPolicy,
    Observation,
    PromptPolicy,
    SourceRef,
    SourceSpan,
    ValidationPolicy,
    ValueKind,
)
from extractx.core.objects import BudgetSpec
from extractx.instances import (
    BoundaryHelperContractError,
    collect_advisory_anchors,
    order_boundary_defining_fields,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="normalized_text",
        byte_start=start,
        byte_end=end,
    )


def _distance_metric() -> DistanceMetric:
    return DistanceMetric(name="noop", params={})


def _binding(role: str) -> GroupingBinding:
    # the literal-checker prefers the role values defined on
    # `GroupingBinding`; we use the string forms the test exercises.
    if role == "boundary_defining":
        return GroupingBinding(role="boundary_defining", distance_metric=_distance_metric())
    if role == "boundary_consuming":
        return GroupingBinding(role="boundary_consuming", distance_metric=_distance_metric())
    return GroupingBinding(role="neutral", distance_metric=_distance_metric())


def _field(
    *,
    field_id: str,
    priority: int = 0,
    role: str | None = "boundary_defining",
) -> FieldSpec:
    return FieldSpec(
        field_id=field_id,
        description="test field",
        value_kind=ValueKind.register("TEXT"),
        cardinality=Cardinality.ONE,
        priority=priority,
        python_type=str,
        grouping_binding=_binding(role) if role is not None else None,
    )


def _spec(*fields: FieldSpec) -> ExtractionSpec:
    return ExtractionSpec(
        fields=fields,
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=_distance_metric()),
        budget=BudgetSpec(),
        version="spec-version-1",
    )


def _candidate(candidate_id: str, start: int, end: int) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        text="x" * (end - start),
        source_span=_span(start, end),
    )


def _candidate_set(*candidates: Candidate, field_id: str = "f") -> CandidateSet:
    return CandidateSet(
        field_id=field_id,
        document_id="doc-1",
        candidates=candidates,
        strategy_id="regex:test",
    )


def _selection(
    outcome: str,
    selected_ids: tuple[str, ...] = (),
) -> Observation:
    # validated by the `Observation` literal for `outcome`. phase-1
    # selector emits `reason=None` for SELECTED / NO_CANDIDATES and a
    # static label for AMBIGUOUS; for these tests we leave reason None
    # except where it is load-bearing.
    return Observation(
        outcome=outcome,  # pyright: ignore[reportArgumentType]
        selected_candidate_ids=selected_ids,
        reason=None,
        producer_version="code:test",
    )


# ---------------------------------------------------------------------------
# order_boundary_defining_fields
# ---------------------------------------------------------------------------


class TestOrderBoundaryDefiningFields:
    def test_priority_descending_with_declaration_order_tiebreak(self) -> None:
        f_low = _field(field_id="low", priority=1)
        f_high_a = _field(field_id="high_a", priority=10)
        f_high_b = _field(field_id="high_b", priority=10)
        f_mid = _field(field_id="mid", priority=5)
        spec = _spec(f_low, f_high_a, f_high_b, f_mid)

        ordered = order_boundary_defining_fields(spec)

        assert [f.field_id for f in ordered] == [
            "high_a",  # priority 10, earlier declaration
            "high_b",  # priority 10, later declaration
            "mid",  # priority 5
            "low",  # priority 1
        ]

    def test_excludes_fields_without_grouping_binding(self) -> None:
        f_no_binding = _field(field_id="no_binding", role=None)
        f_boundary = _field(field_id="boundary", priority=0)
        spec = _spec(f_no_binding, f_boundary)

        ordered = order_boundary_defining_fields(spec)

        assert [f.field_id for f in ordered] == ["boundary"]

    def test_excludes_non_boundary_defining_roles(self) -> None:
        f_consuming = _field(field_id="c", role="boundary_consuming")
        f_neutral = _field(field_id="n", role="neutral")
        f_boundary = _field(field_id="b", role="boundary_defining")
        spec = _spec(f_consuming, f_neutral, f_boundary)

        ordered = order_boundary_defining_fields(spec)

        assert [f.field_id for f in ordered] == ["b"]

    def test_empty_spec_returns_empty_tuple(self) -> None:
        spec = _spec()
        assert order_boundary_defining_fields(spec) == ()

    def test_spec_with_no_boundary_defining_fields_returns_empty_tuple(self) -> None:
        f_consuming = _field(field_id="c", role="boundary_consuming")
        spec = _spec(f_consuming)
        assert order_boundary_defining_fields(spec) == ()


# ---------------------------------------------------------------------------
# collect_advisory_anchors
# ---------------------------------------------------------------------------


class TestCollectAdvisoryAnchors:
    def test_selected_contributes_selected_candidate_spans(self) -> None:
        cs = _candidate_set(
            _candidate("a", 0, 5),
            _candidate("b", 6, 11),
            _candidate("c", 12, 17),
        )
        sel = _selection("SELECTED", selected_ids=("b",))

        anchors = collect_advisory_anchors([(cs, sel)])

        assert anchors == (_span(6, 11),)

    def test_ambiguous_contributes_all_returned_ids_in_selection_order(self) -> None:
        cs = _candidate_set(
            _candidate("a", 0, 5),
            _candidate("b", 6, 11),
            _candidate("c", 12, 17),
        )
        # selection order intentionally differs from candidate_set
        # order so we verify that the helper walks
        # `selection.selected_candidate_ids` rather than
        # `candidate_set.candidates`.
        sel = _selection("AMBIGUOUS", selected_ids=("c", "a", "b"))

        anchors = collect_advisory_anchors([(cs, sel)])

        assert anchors == (_span(12, 17), _span(0, 5), _span(6, 11))

    def test_abstained_contributes_no_anchors(self) -> None:
        cs = _candidate_set(_candidate("a", 0, 5))
        sel = _selection("ABSTAINED", selected_ids=())

        anchors = collect_advisory_anchors([(cs, sel)])

        assert anchors == ()

    def test_no_candidates_contributes_no_anchors(self) -> None:
        cs = _candidate_set()
        sel = _selection("NO_CANDIDATES", selected_ids=())

        anchors = collect_advisory_anchors([(cs, sel)])

        assert anchors == ()

    def test_multiple_pairs_are_concatenated_in_order(self) -> None:
        cs1 = _candidate_set(_candidate("a", 0, 5))
        cs2 = _candidate_set(_candidate("b", 6, 11), _candidate("c", 12, 17))
        sel1 = _selection("SELECTED", selected_ids=("a",))
        sel2 = _selection("AMBIGUOUS", selected_ids=("c", "b"))

        anchors = collect_advisory_anchors([(cs1, sel1), (cs2, sel2)])

        assert anchors == (
            _span(0, 5),
            _span(12, 17),
            _span(6, 11),
        )

    def test_duplicates_are_preserved(self) -> None:
        # planner owns dedup policy; this helper must not silently
        # drop duplicates.
        cs = _candidate_set(_candidate("a", 0, 5))
        sel_a = _selection("SELECTED", selected_ids=("a",))
        sel_b = _selection("SELECTED", selected_ids=("a",))

        anchors = collect_advisory_anchors([(cs, sel_a), (cs, sel_b)])

        assert anchors == (_span(0, 5), _span(0, 5))

    def test_empty_pairs_returns_empty_tuple(self) -> None:
        assert collect_advisory_anchors([]) == ()

    def test_structural_violation_raises_local_error(self) -> None:
        cs = _candidate_set(_candidate("a", 0, 5))
        # "b" is not in the paired CandidateSet: structural seam
        # violation. this is an implementation defect, not a typed
        # negative.
        sel = _selection("SELECTED", selected_ids=("b",))

        with pytest.raises(BoundaryHelperContractError):
            collect_advisory_anchors([(cs, sel)])

    def test_local_error_is_a_value_error_subtype(self) -> None:
        # mirrors SelectionAdapterContractError / ProposalValidator
        # ContractError: a local `ValueError` subtype, not a widened
        # public exception surface. this lets callers catch
        # `ValueError` generically when they care about programmer-
        # error signals.
        assert issubclass(BoundaryHelperContractError, ValueError)
