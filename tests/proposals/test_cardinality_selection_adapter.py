"""behavioral tests for the phase-1 `CardinalitySelectionAdapter`.

proof targets (from docs/tasks/seam-e-cardinality-selection-adapter-phase-1.md,
"Focused proof"):

- same `(selection, candidate_set, field_spec)` → byte-identical output
  across repeated calls (purity).
- non-`SELECTED` outcomes map to one `NegativeOutcome` with
  `category="selection"` and `code=selection.outcome.lower()`.
- `Cardinality.ONE / OPTIONAL / MANY / PER_INSTANCE` rows × k=0 / k=1 /
  k>1 columns follow the architecture §7 seam E table exactly.
- emitted `ProposedField`s copy every documented field from the selected
  `Candidate`, `CandidateSet`, and `Observation` — no synthesis, no
  normalization, no inspection of `Candidate.context`.
- selected-id order is preserved exactly in the output tuple.
- structural seam violations (field-id mismatch, missing id, duplicate
  ids) fail loudly as `SelectionAdapterContractError`, not as typed
  `NegativeOutcome`s.
- every emitted `NegativeOutcome` carries
  `candidate_count = len(candidate_set.candidates)`.
- cardinality-mismatch and `empty_selection` negatives emitted from the
  `SELECTED` path carry `reason=code`.
"""

from __future__ import annotations

import pytest

from extractx.core import (
    Candidate,
    CandidateSet,
    Cardinality,
    FieldSpec,
    InstanceGroupingKey,
    NegativeOutcome,
    Observation,
    ProposedField,
    SourceRef,
    SourceSpan,
    ValueKind,
)
from extractx.proposals import (
    CardinalitySelectionAdapter,
    SelectionAdapterContractError,
)

# ---------------------------------------------------------------------------
# fixtures — small helpers kept local so each test's dependencies are
# legible at the call site (same convention as tests/selection/).
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


def _candidate(
    candidate_id: str,
    text: str,
    start: int,
    *,
    context: str = "",
    normalized_hint: object = None,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        text=text,
        source_span=_span(start, start + len(text)),
        evidence_spans=(_span(start, start + len(text)),),
        context=context,
        normalized_hint=normalized_hint,
    )


def _candidate_set(
    candidates: tuple[Candidate, ...],
    *,
    field_id: str = "total",
    strategy_id: str = "regex:abc",
    instance_hint: InstanceGroupingKey | None = None,
) -> CandidateSet:
    return CandidateSet(
        field_id=field_id,
        document_id="doc-1",
        candidates=candidates,
        strategy_id=strategy_id,
        instance_hint=instance_hint,
    )


def _field_spec(
    *,
    field_id: str = "total",
    cardinality: Cardinality = Cardinality.ONE,
) -> FieldSpec:
    return FieldSpec(
        field_id=field_id,
        description="test field",
        value_kind=ValueKind.register("TEXT"),
        cardinality=cardinality,
        python_type=str,
    )


def _selection(
    outcome: str,
    selected_ids: tuple[str, ...] = (),
    *,
    reason: str | None = None,
    producer_version: str = "code:selector-version",
) -> Observation:
    return Observation(
        outcome=outcome,  # pyright: ignore[reportArgumentType]
        selected_candidate_ids=selected_ids,
        reason=reason,
        producer_version=producer_version,
    )


def _instance_key(group_id: str = "grp-1", ordinal: int = 0) -> InstanceGroupingKey:
    return InstanceGroupingKey(group_id=group_id, ordinal=ordinal, group_anchors=())


# ---------------------------------------------------------------------------
# purity
# ---------------------------------------------------------------------------


class TestPurity:
    def test_repeated_calls_yield_byte_identical_output(self) -> None:
        # canonical proof target from the brief: same input triple →
        # byte-identical output across repeated calls. applies whether
        # the output is a tuple of proposals or a NegativeOutcome.
        adapter = CardinalitySelectionAdapter()
        cand = _candidate("c-1", "42.00", 0)
        cset = _candidate_set((cand,))
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("SELECTED", ("c-1",))

        first = adapter.adapt(sel, cset, fs)
        second = adapter.adapt(sel, cset, fs)
        assert first == second
        # structural equality on pydantic frozen models is structural;
        # also assert serialized equality for defense in depth.
        assert isinstance(first, tuple)
        assert isinstance(second, tuple)
        assert [pf.model_dump_json() for pf in first] == [pf.model_dump_json() for pf in second]

    def test_two_adapter_instances_are_equivalent(self) -> None:
        # the adapter holds no configurable state; two fresh instances
        # must produce identical outputs for the same inputs.
        a = CardinalitySelectionAdapter()
        b = CardinalitySelectionAdapter()
        cset = _candidate_set((_candidate("c-1", "x", 0),))
        fs = _field_spec()
        sel = _selection("SELECTED", ("c-1",))
        assert a.adapt(sel, cset, fs) == b.adapt(sel, cset, fs)


# ---------------------------------------------------------------------------
# non-SELECTED path
# ---------------------------------------------------------------------------


class TestNonSelectedPath:
    @pytest.mark.parametrize(
        "outcome",
        ["NO_CANDIDATES", "AMBIGUOUS", "ABSTAINED"],
    )
    def test_non_selected_outcomes_map_to_selection_category_with_lowercased_code(
        self, outcome: str
    ) -> None:
        adapter = CardinalitySelectionAdapter()
        cands = (_candidate("c-1", "x", 0), _candidate("c-2", "y", 5))
        # NO_CANDIDATES only runs honestly against an empty candidate set,
        # but the seam-E adapter does not verify seam-D's self-consistency;
        # a non-empty set paired with NO_CANDIDATES is still a valid
        # adapter input for category/code mapping.
        cset = _candidate_set(() if outcome == "NO_CANDIDATES" else cands)
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection(outcome)

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        assert result.category == "selection"
        assert result.code == outcome.lower()
        assert result.field_id == fs.field_id
        assert result.candidate_count == len(cset.candidates)

    def test_non_selected_uses_selection_reason_when_present(self) -> None:
        adapter = CardinalitySelectionAdapter()
        cset = _candidate_set((_candidate("c-1", "x", 0), _candidate("c-2", "y", 5)))
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("AMBIGUOUS", reason="multiple grounded values")

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        assert result.reason == "multiple grounded values"

    def test_non_selected_falls_back_to_code_when_reason_missing(self) -> None:
        adapter = CardinalitySelectionAdapter()
        cset = _candidate_set(())
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("NO_CANDIDATES")

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        # reason falls back to the code when seam D emits no prose.
        assert result.reason == "no_candidates"

    def test_non_selected_carries_instance_hint_as_instance_key(self) -> None:
        adapter = CardinalitySelectionAdapter()
        hint = _instance_key()
        cset = _candidate_set(
            (_candidate("c-1", "x", 0),),
            instance_hint=hint,
        )
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("ABSTAINED")

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        assert result.instance_key == hint


# ---------------------------------------------------------------------------
# Cardinality.ONE — the three selected columns
# ---------------------------------------------------------------------------


class TestCardinalityOne:
    def test_k0_emits_adaptation_empty_selection(self) -> None:
        adapter = CardinalitySelectionAdapter()
        # non-empty candidate set, SELECTED with empty ids is unusual
        # (seam-D enforcement rejects this shape in practice), but the
        # adapter must still cover it honestly per the table.
        cset = _candidate_set((_candidate("c-1", "x", 0),))
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("SELECTED", ())

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        assert result.category == "adaptation"
        assert result.code == "empty_selection"
        assert result.field_id == fs.field_id
        # candidate_count is the full candidate set size, not k.
        assert result.candidate_count == 1
        # reason = code on SELECTED-path cardinality negatives (brief
        # clarification): no prose from candidate content, no dynamic k.
        assert result.reason == "empty_selection"

    def test_k1_emits_one_proposed_field(self) -> None:
        adapter = CardinalitySelectionAdapter()
        cand = _candidate("c-1", "42.00", 0)
        cset = _candidate_set((cand,))
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("SELECTED", ("c-1",))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple)
        assert len(result) == 1
        assert isinstance(result[0], ProposedField)
        assert result[0].field_id == fs.field_id

    def test_k_gt_1_emits_validation_cardinality_one_expected_many_selected(
        self,
    ) -> None:
        adapter = CardinalitySelectionAdapter()
        cands = (_candidate("c-1", "x", 0), _candidate("c-2", "y", 5))
        cset = _candidate_set(cands)
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("SELECTED", ("c-1", "c-2"))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        assert result.category == "validation"
        assert result.code == "cardinality.one_expected_many_selected"
        assert result.reason == "cardinality.one_expected_many_selected"
        # brief-standardized: candidate_count is len(candidate_set.candidates),
        # not len(selected_candidate_ids). verify the full-set size here.
        assert result.candidate_count == 2


# ---------------------------------------------------------------------------
# Cardinality.OPTIONAL — the three selected columns
# ---------------------------------------------------------------------------


class TestCardinalityOptional:
    def test_k0_emits_selection_abstained(self) -> None:
        adapter = CardinalitySelectionAdapter()
        cset = _candidate_set((_candidate("c-1", "x", 0),))
        fs = _field_spec(cardinality=Cardinality.OPTIONAL)
        sel = _selection("SELECTED", ())

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        assert result.category == "selection"
        assert result.code == "abstained"
        assert result.reason == "abstained"
        assert result.candidate_count == 1

    def test_k1_emits_one_proposed_field(self) -> None:
        adapter = CardinalitySelectionAdapter()
        cand = _candidate("c-1", "42.00", 0)
        cset = _candidate_set((cand,))
        fs = _field_spec(cardinality=Cardinality.OPTIONAL)
        sel = _selection("SELECTED", ("c-1",))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple)
        assert len(result) == 1

    def test_k_gt_1_emits_validation_cardinality_optional_expected_many_selected(
        self,
    ) -> None:
        adapter = CardinalitySelectionAdapter()
        cands = (_candidate("c-1", "x", 0), _candidate("c-2", "y", 5))
        cset = _candidate_set(cands)
        fs = _field_spec(cardinality=Cardinality.OPTIONAL)
        sel = _selection("SELECTED", ("c-1", "c-2"))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        assert result.category == "validation"
        assert result.code == "cardinality.optional_expected_many_selected"
        assert result.reason == "cardinality.optional_expected_many_selected"
        assert result.candidate_count == 2


# ---------------------------------------------------------------------------
# Cardinality.MANY — the three selected columns
# ---------------------------------------------------------------------------


class TestCardinalityMany:
    def test_k0_emits_empty_tuple(self) -> None:
        adapter = CardinalitySelectionAdapter()
        # for MANY, an empty selection is a valid (not-negative) outcome.
        cset = _candidate_set((_candidate("c-1", "x", 0),))
        fs = _field_spec(cardinality=Cardinality.MANY)
        sel = _selection("SELECTED", ())

        result = adapter.adapt(sel, cset, fs)

        assert result == ()

    def test_k1_emits_one_proposed_field_in_tuple(self) -> None:
        adapter = CardinalitySelectionAdapter()
        cand = _candidate("c-1", "x", 0)
        cset = _candidate_set((cand,))
        fs = _field_spec(cardinality=Cardinality.MANY)
        sel = _selection("SELECTED", ("c-1",))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple)
        assert len(result) == 1

    def test_k_gt_1_emits_k_proposed_fields_in_tuple(self) -> None:
        adapter = CardinalitySelectionAdapter()
        cands = (
            _candidate("c-1", "a", 0),
            _candidate("c-2", "b", 5),
            _candidate("c-3", "c", 10),
        )
        cset = _candidate_set(cands)
        fs = _field_spec(cardinality=Cardinality.MANY)
        sel = _selection("SELECTED", ("c-1", "c-2", "c-3"))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple)
        assert len(result) == 3
        assert all(isinstance(pf, ProposedField) for pf in result)


# ---------------------------------------------------------------------------
# Cardinality.PER_INSTANCE — treated as ONE within instance_hint
# ---------------------------------------------------------------------------


class TestCardinalityPerInstance:
    def test_k0_emits_adaptation_empty_selection(self) -> None:
        # PER_INSTANCE acts as ONE within the provided instance_hint —
        # per-instance iteration is the iterative strategy's concern,
        # not seam E's.
        adapter = CardinalitySelectionAdapter()
        hint = _instance_key()
        cset = _candidate_set(
            (_candidate("c-1", "x", 0),),
            instance_hint=hint,
        )
        fs = _field_spec(cardinality=Cardinality.PER_INSTANCE)
        sel = _selection("SELECTED", ())

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        assert result.category == "adaptation"
        assert result.code == "empty_selection"
        assert result.instance_key == hint

    def test_k1_emits_one_proposed_field_with_tentative_instance_key(self) -> None:
        adapter = CardinalitySelectionAdapter()
        hint = _instance_key()
        cand = _candidate("c-1", "42.00", 0)
        cset = _candidate_set((cand,), instance_hint=hint)
        fs = _field_spec(cardinality=Cardinality.PER_INSTANCE)
        sel = _selection("SELECTED", ("c-1",))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple)
        assert len(result) == 1
        assert result[0].tentative_instance_key == hint

    def test_k_gt_1_emits_cardinality_one_expected_many_selected(self) -> None:
        # the ONE-row validation code is reused, since PER_INSTANCE
        # collapses into ONE at this seam.
        adapter = CardinalitySelectionAdapter()
        cands = (_candidate("c-1", "a", 0), _candidate("c-2", "b", 5))
        cset = _candidate_set(cands, instance_hint=_instance_key())
        fs = _field_spec(cardinality=Cardinality.PER_INSTANCE)
        sel = _selection("SELECTED", ("c-1", "c-2"))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "cardinality.one_expected_many_selected"


# ---------------------------------------------------------------------------
# honest `ProposedField` projection
# ---------------------------------------------------------------------------


class TestProposedFieldProjection:
    def test_projects_every_documented_field_from_candidate_and_selection(self) -> None:
        adapter = CardinalitySelectionAdapter()
        hint = _instance_key()
        cand = _candidate(
            "c-1",
            "42.00",
            10,
            # the brief: seam E deliberately does NOT copy candidate.context
            # into evidence_text. if this line ever leaks into evidence_text,
            # the `test_evidence_text_is_candidate_text_not_context` case
            # below will fail.
            context="the grand total was 42.00 dollars",
            normalized_hint={"decimal": "42.00"},
        )
        cset = _candidate_set(
            (cand,),
            field_id="total",
            strategy_id="regex:total-v1",
            instance_hint=hint,
        )
        fs = _field_spec(field_id="total", cardinality=Cardinality.ONE)
        sel = _selection(
            "SELECTED",
            ("c-1",),
            producer_version="code:selector-v1",
        )

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple)
        (proposal,) = result
        assert proposal.field_id == "total"
        assert proposal.tentative_instance_key == hint
        assert proposal.raw_value == cand.text
        assert proposal.evidence_text == cand.text
        assert proposal.source_span == cand.source_span
        assert proposal.evidence_spans == cand.evidence_spans
        assert proposal.normalized_hint == cand.normalized_hint
        assert proposal.candidate_id_refs == ("c-1",)
        assert proposal.strategy_id == "regex:total-v1"
        assert proposal.selector_producer_version == "code:selector-v1"
        # grounded_producer_version is always None at seam E — the seam
        # has no seam-C.alt grounded proposal surface to carry through.
        assert proposal.grounded_producer_version is None

    def test_evidence_text_is_candidate_text_not_context(self) -> None:
        # explicit guard against the "copy context into evidence_text"
        # drift. the brief is deliberate about this: seam E does not
        # inspect or copy candidate.context.
        adapter = CardinalitySelectionAdapter()
        cand = _candidate(
            "c-1",
            "42.00",
            10,
            context="surrounding prose that should never appear in evidence_text",
        )
        cset = _candidate_set((cand,))
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("SELECTED", ("c-1",))

        result = adapter.adapt(sel, cset, fs)
        assert isinstance(result, tuple)
        (proposal,) = result
        assert proposal.evidence_text == "42.00"
        assert "surrounding prose" not in proposal.evidence_text

    def test_normalized_hint_carries_through_unchanged(self) -> None:
        # seam E does not normalize; normalized_hint from seam C flows
        # through verbatim so seam F layer 2 can consume it.
        adapter = CardinalitySelectionAdapter()
        hint_value = {"currency": "USD", "amount": "42.00"}
        cand = _candidate("c-1", "42.00", 0, normalized_hint=hint_value)
        cset = _candidate_set((cand,))
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("SELECTED", ("c-1",))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple)
        (proposal,) = result
        assert proposal.normalized_hint == hint_value
        # identity is not required (pydantic may deep-copy frozen models
        # under some configs); value equality is the contract.

    def test_selected_id_order_is_preserved(self) -> None:
        # canonical proof target: MANY-cardinality output preserves the
        # selected-id order exactly, even when ids differ from the
        # CandidateSet.candidates order.
        adapter = CardinalitySelectionAdapter()
        cands = (
            _candidate("alpha", "a", 0),
            _candidate("beta", "b", 5),
            _candidate("gamma", "g", 10),
        )
        cset = _candidate_set(cands)
        fs = _field_spec(cardinality=Cardinality.MANY)
        sel = _selection("SELECTED", ("gamma", "alpha", "beta"))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple)
        assert tuple(p.candidate_id_refs[0] for p in result) == (
            "gamma",
            "alpha",
            "beta",
        )
        # each `ProposedField.candidate_id_refs` is a 1-tuple — no
        # bundling of selected ids into a single proposal.
        assert all(len(p.candidate_id_refs) == 1 for p in result)


# ---------------------------------------------------------------------------
# structural seam violations — loud, not typed `NegativeOutcome`
# ---------------------------------------------------------------------------


class TestStructuralSeamViolations:
    def test_field_id_mismatch_fails_loudly(self) -> None:
        adapter = CardinalitySelectionAdapter()
        cset = _candidate_set((_candidate("c-1", "x", 0),), field_id="total")
        fs = _field_spec(field_id="subtotal", cardinality=Cardinality.ONE)
        sel = _selection("SELECTED", ("c-1",))

        with pytest.raises(SelectionAdapterContractError):
            adapter.adapt(sel, cset, fs)

    def test_missing_selected_id_fails_loudly(self) -> None:
        adapter = CardinalitySelectionAdapter()
        cset = _candidate_set((_candidate("c-1", "x", 0),))
        fs = _field_spec(cardinality=Cardinality.ONE)
        # "c-2" is not in the candidate set.
        sel = _selection("SELECTED", ("c-2",))

        with pytest.raises(SelectionAdapterContractError):
            adapter.adapt(sel, cset, fs)

    def test_duplicate_selected_id_fails_loudly(self) -> None:
        # seam-D contract violation: seam E does not silently dedup.
        adapter = CardinalitySelectionAdapter()
        cset = _candidate_set((_candidate("c-1", "x", 0),))
        fs = _field_spec(cardinality=Cardinality.MANY)
        sel = _selection("SELECTED", ("c-1", "c-1"))

        with pytest.raises(SelectionAdapterContractError):
            adapter.adapt(sel, cset, fs)

    def test_structural_violation_is_a_value_error_subtype(self) -> None:
        # the exception is a ValueError subtype, not a widened public
        # exception surface. mirrors the seam-D SelectorContractError
        # pattern.
        assert issubclass(SelectionAdapterContractError, ValueError)


# ---------------------------------------------------------------------------
# seam-E / seam-F boundary — no normalization or validation smuggled in
# ---------------------------------------------------------------------------


class TestSeamBoundary:
    def test_adapter_never_emits_validated_field(self) -> None:
        # seam E produces `ProposedField`; `ValidatedField` is seam F's
        # output type. an adapter that ever returns one is leaking.
        # this check is shape-level — the return-type union on
        # `SelectionAdapter.adapt` forbids it statically; the test
        # guards the runtime shape.
        adapter = CardinalitySelectionAdapter()
        cset = _candidate_set((_candidate("c-1", "x", 0),))
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("SELECTED", ("c-1",))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple | NegativeOutcome)
        if isinstance(result, tuple):
            assert all(isinstance(pf, ProposedField) for pf in result)

    def test_adapter_does_not_mutate_candidate_normalized_hint(self) -> None:
        # seam E must not normalize. if the adapter ever reshapes
        # `normalized_hint`, the value stored on the emitted
        # `ProposedField` diverges from the input candidate's value.
        adapter = CardinalitySelectionAdapter()
        original_hint = {"raw": "42.00"}
        cand = _candidate("c-1", "42.00", 0, normalized_hint=original_hint)
        cset = _candidate_set((cand,))
        fs = _field_spec(cardinality=Cardinality.ONE)
        sel = _selection("SELECTED", ("c-1",))

        result = adapter.adapt(sel, cset, fs)

        assert isinstance(result, tuple)
        (proposal,) = result
        assert proposal.normalized_hint == original_hint
