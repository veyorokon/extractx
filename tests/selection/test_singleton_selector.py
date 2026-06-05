"""behavioral tests for the phase-1 `SingletonSelector`.

proof targets (from docs/tasks/seam-d-algorithmic-selector-phase-1.md,
"Focused proof"):

- empty `CandidateSet`              -> `NO_CANDIDATES` with empty ids.
- singleton `CandidateSet`          -> `SELECTED` with the sole id.
- multi-candidate `CandidateSet`    -> `AMBIGUOUS` with all ids, in
                                       `CandidateSet.candidates` order.
- emitted `selected_candidate_ids` are always a subset of input ids.
- same `(field_spec, candidate_set, context_pack, instance_state)`
  yields byte-identical `Observation` across repeated calls (purity).
- no seam-E / cardinality behavior is smuggled into the selector.
- `ContextPack` and `InstanceState` can be passed without changing the
  deterministic output.
- `ABSTAINED` is not emitted by this phase-1 selector.

id-only enforcement (shared selector-boundary) lives in
`test_selector_enforcement.py`.
"""

from __future__ import annotations

from extractx.core import (
    Candidate,
    CandidateOverflowMetadata,
    CandidateSet,
    Cardinality,
    ContextPack,
    FieldSpec,
    InstanceGroupingKey,
    InstanceState,
    SourceRef,
    SourceSpan,
    ValueKind,
)
from extractx.selection import (
    AMBIGUOUS_REASON_LABEL,
    SingletonSelector,
)

# ---------------------------------------------------------------------------
# fixtures — small helpers, not shared conftest, so the dependencies each
# test exercises remain legible at the call site.
# ---------------------------------------------------------------------------


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def _candidate(candidate_id: str, text: str, start: int) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        text=text,
        source_span=_span(start, start + len(text)),
    )


def _candidate_set(
    candidates: tuple[Candidate, ...],
    *,
    field_id: str = "total",
    strategy_id: str = "regex:abc",
) -> CandidateSet:
    return CandidateSet(
        field_id=field_id,
        document_id="doc-1",
        candidates=candidates,
        strategy_id=strategy_id,
    )


def _field_spec(field_id: str = "total") -> FieldSpec:
    return FieldSpec(
        field_id=field_id,
        description="test field",
        value_kind=ValueKind.register("TEXT"),
        cardinality=Cardinality.ONE,
        python_type=str,
    )


def _context_pack() -> ContextPack:
    return ContextPack(schema_description="s", document_summary="d")


def _instance_state() -> InstanceState:
    return InstanceState(
        instance_key=InstanceGroupingKey(group_id="grp-1", ordinal=0, group_anchors=()),
        version=0,
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestEmptyCandidateSet:
    def test_emits_no_candidates_outcome(self) -> None:
        selector = SingletonSelector()
        cset = _candidate_set(())
        selection = selector.select(_field_spec(), cset, _context_pack())
        assert selection.outcome == "NO_CANDIDATES"
        assert selection.selected_candidate_ids == ()

    def test_no_candidates_carries_no_reason(self) -> None:
        selector = SingletonSelector()
        cset = _candidate_set(())
        selection = selector.select(_field_spec(), cset, _context_pack())
        # phase-1 policy: reason is None for NO_CANDIDATES (self-evident
        # from the outcome; no prose needed).
        assert selection.reason is None

    def test_no_candidates_carries_producer_version(self) -> None:
        selector = SingletonSelector()
        cset = _candidate_set(())
        selection = selector.select(_field_spec(), cset, _context_pack())
        assert selection.producer_version == selector.producer_version
        assert selection.producer_version.startswith("code:")


class TestSingletonCandidateSet:
    def test_emits_selected_with_sole_id(self) -> None:
        selector = SingletonSelector()
        c = _candidate("cand-1", "42.00", 0)
        selection = selector.select(_field_spec(), _candidate_set((c,)), _context_pack())
        assert selection.outcome == "SELECTED"
        assert selection.selected_candidate_ids == ("cand-1",)

    def test_selected_carries_no_reason(self) -> None:
        selector = SingletonSelector()
        c = _candidate("cand-1", "42.00", 0)
        selection = selector.select(_field_spec(), _candidate_set((c,)), _context_pack())
        assert selection.reason is None


class TestAmbiguousCandidateSet:
    def test_emits_ambiguous_with_all_ids_in_input_order(self) -> None:
        selector = SingletonSelector()
        # deliberately use ids whose lexical order differs from the
        # `CandidateSet.candidates` order so we catch a hidden
        # alphabetical re-sort if one ever creeps in.
        cands = (
            _candidate("z-1", "alpha", 0),
            _candidate("a-2", "beta", 10),
            _candidate("m-3", "gamma", 20),
        )
        cset = _candidate_set(cands)
        selection = selector.select(_field_spec(), cset, _context_pack())
        assert selection.outcome == "AMBIGUOUS"
        # preserve the CandidateSet.candidates order exactly.
        assert selection.selected_candidate_ids == ("z-1", "a-2", "m-3")

    def test_ambiguous_reason_is_the_static_label(self) -> None:
        selector = SingletonSelector()
        cands = (
            _candidate("c-1", "a", 0),
            _candidate("c-2", "b", 2),
        )
        selection = selector.select(_field_spec(), _candidate_set(cands), _context_pack())
        # phase-1 policy: static label, no prose derived from candidate
        # content. if this drifts into producing candidate text, seam D
        # has started fabricating.
        assert selection.reason == AMBIGUOUS_REASON_LABEL

    def test_ambiguous_does_not_collapse_into_selected(self) -> None:
        # guard against a "first candidate wins" or "select all" silent
        # short-circuit.
        selector = SingletonSelector()
        cands = tuple(_candidate(f"c-{i}", f"t-{i}", i * 10) for i in range(5))
        selection = selector.select(_field_spec(), _candidate_set(cands), _context_pack())
        assert selection.outcome == "AMBIGUOUS"
        assert len(selection.selected_candidate_ids) == 5


class TestIdOnlySubset:
    def test_selected_ids_are_subset_of_input_ids(self) -> None:
        selector = SingletonSelector()
        for cands in (
            (),
            (_candidate("only", "x", 0),),
            (_candidate("a", "x", 0), _candidate("b", "y", 5)),
            tuple(_candidate(f"k-{i}", f"t-{i}", i) for i in range(4)),
        ):
            cset = _candidate_set(cands)
            selection = selector.select(_field_spec(), cset, _context_pack())
            input_ids = {c.candidate_id for c in cset.candidates}
            assert set(selection.selected_candidate_ids).issubset(input_ids)


class TestPurity:
    def test_repeated_calls_yield_byte_identical_selection(self) -> None:
        # canonical proof target from the brief: same
        # `(field_spec, candidate_set, context_pack, instance_state)`
        # -> byte-identical `Observation` across repeated calls.
        selector = SingletonSelector()
        cands = (
            _candidate("a", "x", 0),
            _candidate("b", "y", 5),
        )
        fs = _field_spec()
        cset = _candidate_set(cands)
        pack = _context_pack()
        state = _instance_state()

        first = selector.select(fs, cset, pack, state)
        second = selector.select(fs, cset, pack, state)
        # equality of frozen pydantic models is structural; assert both
        # the model-level equality and the json-serialized bytes for
        # defense in depth.
        assert first == second
        assert first.model_dump_json() == second.model_dump_json()

    def test_two_instances_yield_identical_selection_for_identical_input(self) -> None:
        # `SingletonSelector` has no configurable state; two fresh
        # instances must produce the same `Observation` for the same
        # inputs, including identical `producer_version`.
        a = SingletonSelector()
        b = SingletonSelector()
        cset = _candidate_set((_candidate("only", "x", 0),))
        fs = _field_spec()
        pack = _context_pack()
        assert a.select(fs, cset, pack) == b.select(fs, cset, pack)


class TestContextAndInstanceStateAreInertForPhaseOne:
    def test_changing_context_pack_does_not_change_output(self) -> None:
        # phase-1 intentionally does not condition on `ContextPack`;
        # prove it by swapping a rich pack in against a minimal one and
        # asserting the `Observation` stays byte-identical.
        selector = SingletonSelector()
        cset = _candidate_set((_candidate("only", "x", 0),))
        fs = _field_spec()
        bare = ContextPack(schema_description="s", document_summary="d")
        rich = ContextPack(
            schema_description="s",
            document_summary="d",
            field_context={"total": "a human hint"},
            retry_feedback=("prior validator said the field was invalid",),
            candidate_overflow=CandidateOverflowMetadata(
                source_candidate_count=123,
                presented_candidate_count=1,
                sorter_id="code:xyz",
                overflow_policy="truncate_sorted",
            ),
        )
        assert selector.select(fs, cset, bare) == selector.select(fs, cset, rich)

    def test_changing_instance_state_does_not_change_output(self) -> None:
        selector = SingletonSelector()
        cset = _candidate_set((_candidate("only", "x", 0),))
        fs = _field_spec()
        pack = _context_pack()
        assert selector.select(fs, cset, pack, None) == selector.select(
            fs, cset, pack, _instance_state()
        )

    def test_changing_field_spec_description_does_not_change_output(self) -> None:
        # phase-1 does not condition on FieldSpec content; only the
        # CandidateSet shape drives outcome and ids.
        selector = SingletonSelector()
        cset = _candidate_set((_candidate("only", "x", 0),))
        a = _field_spec()
        b = FieldSpec(
            field_id="total",
            description="an entirely different description",
            value_kind=ValueKind.register("TEXT"),
            cardinality=Cardinality.MANY,  # deliberately different cardinality
            python_type=str,
        )
        pack = _context_pack()
        # Observation equality only depends on selected_candidate_ids and
        # producer_version here — the FieldSpec change should not flow.
        assert selector.select(a, cset, pack) == selector.select(b, cset, pack)


class TestNoSeamEBehavior:
    def test_many_cardinality_spec_still_emits_ambiguous_not_selected(self) -> None:
        # seam E cardinality mapping must stay out of seam D. a MANY
        # spec with multiple candidates is a seam-E concern; seam D
        # still emits AMBIGUOUS (not SELECTED, not a pre-bundled tuple).
        selector = SingletonSelector()
        fs = FieldSpec(
            field_id="totals",
            description="test field",
            value_kind=ValueKind.register("TEXT"),
            cardinality=Cardinality.MANY,
            python_type=str,
        )
        cands = (
            _candidate("a", "x", 0),
            _candidate("b", "y", 5),
        )
        selection = selector.select(fs, _candidate_set(cands), _context_pack())
        assert selection.outcome == "AMBIGUOUS"

    def test_abstained_is_never_emitted_by_phase_one_selector(self) -> None:
        # phase-1 does not carry an abstention heuristic. for any input
        # shape (empty / singleton / multi), ABSTAINED must not appear.
        selector = SingletonSelector()
        cases: tuple[tuple[Candidate, ...], ...] = (
            (),
            (_candidate("only", "x", 0),),
            (_candidate("a", "x", 0), _candidate("b", "y", 5)),
        )
        for cands in cases:
            selection = selector.select(_field_spec(), _candidate_set(cands), _context_pack())
            assert selection.outcome != "ABSTAINED"
