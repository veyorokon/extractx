"""id-only enforcement tests for the shared selector-boundary wrapper.

proof targets (from docs/tasks/seam-d-algorithmic-selector-phase-1.md,
"Scope §3"):

- `enforce_selection_contract` catches fabrication
  (`selected_candidate_ids ⊄ input candidate_ids`) at the selector
  boundary, not downstream.
- the wrapper also catches disallowed outcome shapes
  (`NO_CANDIDATES` with non-empty ids, `AMBIGUOUS` with
  empty ids, `ABSTAINED` with non-empty ids, `NO_CANDIDATES` on a
  non-empty input set).
- the wrapper is generic over `Observation`: it does not depend on the
  impl being algorithmic or llm-backed. this is the proof that the
  future llm-backed selector can reuse the same path.
- well-formed `Observation`s pass through unchanged.

these tests construct raw `Observation` objects directly so they exercise
the enforcement boundary independently of the phase-1 algorithmic
policy.
"""

from __future__ import annotations

import pytest

from extractx.core import (
    Candidate,
    CandidateSet,
    Observation,
    SourceRef,
    SourceSpan,
)
from extractx.selection import (
    SelectorContractError,
    enforce_batch_observation_contract,
    enforce_selection_contract,
)


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def _candidate(candidate_id: str, start: int = 0, end: int = 1) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        text="x",
        source_span=_span(start, end),
    )


def _candidate_set(candidate_ids: tuple[str, ...]) -> CandidateSet:
    return CandidateSet(
        field_id="total",
        document_id="doc-1",
        candidates=tuple(
            _candidate(cid, start=i, end=i + 1) for i, cid in enumerate(candidate_ids)
        ),
        strategy_id="regex:abc",
    )


def _candidate_set_for_field(field_id: str, candidate_ids: tuple[str, ...]) -> CandidateSet:
    return _candidate_set(candidate_ids).model_copy(update={"field_id": field_id})


def _selection(
    outcome: str,
    selected_candidate_ids: tuple[str, ...] = (),
    producer_version: str = "code:abc",
) -> Observation:
    # cast the outcome via the Literal at construction — Observation's
    # model accepts it as str at runtime; pyright is only strict on
    # `src/`, not `tests/`.
    return Observation(
        outcome=outcome,  # type: ignore[arg-type]
        selected_candidate_ids=selected_candidate_ids,
        field_id="total",
        instance_id="inst_0",
        reason=None,
        producer_version=producer_version,
    )


class TestAcceptsWellFormedSelections:
    def test_no_candidates_passes(self) -> None:
        cset = _candidate_set(())
        sel = _selection("NO_CANDIDATES", ())
        assert enforce_selection_contract(sel, cset) is sel

    def test_selected_passes(self) -> None:
        cset = _candidate_set(("a",))
        sel = _selection("SELECTED", ("a",))
        assert enforce_selection_contract(sel, cset) is sel

    def test_selected_empty_passes_for_cardinality_agnostic_seam_d(self) -> None:
        cset = _candidate_set(("a",))
        sel = _selection("SELECTED", ())
        assert enforce_selection_contract(sel, cset) is sel

    def test_ambiguous_passes(self) -> None:
        cset = _candidate_set(("a", "b", "c"))
        sel = _selection("AMBIGUOUS", ("a", "b", "c"))
        assert enforce_selection_contract(sel, cset) is sel

    def test_ambiguous_with_subset_of_ids_passes(self) -> None:
        # architecture §7 seam D: the selector returns a subset of
        # input ids; cardinality is enforced at seam E. AMBIGUOUS with
        # only some ids is legal at seam D.
        cset = _candidate_set(("a", "b", "c"))
        sel = _selection("AMBIGUOUS", ("a", "c"))
        assert enforce_selection_contract(sel, cset) is sel

    def test_abstained_with_empty_ids_passes(self) -> None:
        cset = _candidate_set(("a", "b"))
        sel = _selection("ABSTAINED", ())
        assert enforce_selection_contract(sel, cset) is sel


class TestRejectsFabrication:
    def test_selected_with_stray_id_raises(self) -> None:
        cset = _candidate_set(("a",))
        sel = _selection("SELECTED", ("not-in-set",))
        with pytest.raises(SelectorContractError):
            enforce_selection_contract(sel, cset)

    def test_ambiguous_with_mixed_real_and_stray_ids_raises(self) -> None:
        cset = _candidate_set(("a", "b"))
        sel = _selection("AMBIGUOUS", ("a", "ghost"))
        with pytest.raises(SelectorContractError):
            enforce_selection_contract(sel, cset)

    def test_selected_on_empty_input_raises(self) -> None:
        # a selector that fabricated an id against an empty input set
        # must fail loudly at the seam, not propagate downstream.
        cset = _candidate_set(())
        sel = _selection("SELECTED", ("fabricated",))
        with pytest.raises(SelectorContractError):
            enforce_selection_contract(sel, cset)


class TestRejectsMalformedOutcomeShapes:
    def test_no_candidates_with_non_empty_ids_raises(self) -> None:
        # impossible by definition — if the id is in the set the
        # outcome should be SELECTED or AMBIGUOUS; if not, it's
        # fabrication. either way, NO_CANDIDATES + ids is incoherent.
        cset = _candidate_set(("a",))
        sel = _selection("NO_CANDIDATES", ("a",))
        with pytest.raises(SelectorContractError):
            enforce_selection_contract(sel, cset)

    def test_no_candidates_on_non_empty_input_raises(self) -> None:
        # NO_CANDIDATES semantics per §7 seam D: input set was empty.
        # an impl that emits NO_CANDIDATES against a non-empty input
        # should have emitted ABSTAINED instead.
        cset = _candidate_set(("a", "b"))
        sel = _selection("NO_CANDIDATES", ())
        with pytest.raises(SelectorContractError):
            enforce_selection_contract(sel, cset)

    def test_abstained_with_ids_raises(self) -> None:
        cset = _candidate_set(("a", "b"))
        sel = _selection("ABSTAINED", ("a",))
        with pytest.raises(SelectorContractError):
            enforce_selection_contract(sel, cset)

    def test_ambiguous_with_empty_ids_raises(self) -> None:
        cset = _candidate_set(("a", "b"))
        sel = _selection("AMBIGUOUS", ())
        with pytest.raises(SelectorContractError):
            enforce_selection_contract(sel, cset)


class TestWrapperIsGenericOverProducer:
    def test_accepts_selection_with_arbitrary_producer_version(self) -> None:
        # the wrapper must not peek at `producer_version`. it is the
        # seam's id-only contract enforcement, not a producer-kind
        # dispatcher. an llm-backed selector whose producer_version is
        # `"{model}|{template_hash}|{code_hash}"` must pass through on
        # the same path an algorithmic producer does.
        cset = _candidate_set(("a",))
        sel = _selection(
            "SELECTED",
            ("a",),
            producer_version="gpt-4.1-mini|tmpl:v1|code:abc",
        )
        assert enforce_selection_contract(sel, cset) is sel


class TestBatchObservationContract:
    def test_orders_observations_by_candidate_set_order(self) -> None:
        csets = (
            _candidate_set_for_field("first", ("a",)),
            _candidate_set_for_field("second", ("b",)),
        )
        observations = (
            _selection("SELECTED", ("b",)).model_copy(update={"field_id": "second"}),
            _selection("SELECTED", ("a",)).model_copy(update={"field_id": "first"}),
        )

        ordered = enforce_batch_observation_contract(observations, csets)

        assert tuple(obs.field_id for obs in ordered) == ("first", "second")

    def test_rejects_unknown_field(self) -> None:
        csets = (_candidate_set_for_field("first", ("a",)),)
        observations = (
            _selection("SELECTED", ("a",)).model_copy(update={"field_id": "ghost"}),
        )

        with pytest.raises(SelectorContractError, match="unknown field_id"):
            enforce_batch_observation_contract(observations, csets)

    def test_rejects_missing_observation(self) -> None:
        csets = (
            _candidate_set_for_field("first", ("a",)),
            _candidate_set_for_field("second", ("b",)),
        )
        observations = (
            _selection("SELECTED", ("a",)).model_copy(update={"field_id": "first"}),
        )

        with pytest.raises(SelectorContractError, match="omitted"):
            enforce_batch_observation_contract(observations, csets)

    def test_rejects_duplicate_field_instance(self) -> None:
        csets = (_candidate_set_for_field("first", ("a",)),)
        observations = (
            _selection("SELECTED", ("a",)).model_copy(update={"field_id": "first"}),
            _selection("SELECTED", ("a",)).model_copy(update={"field_id": "first"}),
        )

        with pytest.raises(SelectorContractError, match="duplicate"):
            enforce_batch_observation_contract(observations, csets)

    def test_reuses_single_observation_id_only_enforcement(self) -> None:
        csets = (_candidate_set_for_field("first", ("a",)),)
        observations = (
            _selection("SELECTED", ("ghost",)).model_copy(update={"field_id": "first"}),
        )

        with pytest.raises(SelectorContractError, match="id-only"):
            enforce_batch_observation_contract(observations, csets)
