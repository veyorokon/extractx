"""tests for the structured-candidate deterministic selection gate."""

from __future__ import annotations

from extractx.candidates import DeterministicSelectionGate
from extractx.core import (
    Candidate,
    CandidateSet,
    SetConstraint,
    SourceRef,
    SourceSpan,
    StructuralFailure,
    StructuralStatus,
)


def _span(start: int) -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="doc-1", content_hash="sha256:abc"),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=start + 1,
    )


def _text(candidate_id: str, start: int) -> Candidate:
    return Candidate(candidate_id=candidate_id, text="x", source_span=_span(start))


def _structured(candidate_id: str, start: int, *, passed: bool) -> Candidate:
    failures = ()
    if not passed:
        failures = (
            StructuralFailure(
                field="unit",
                actual="EUR",
                expected=SetConstraint(allowed=("USD",)),
            ),
        )
    return Candidate(
        candidate_id=candidate_id,
        text="x",
        source_kind="structured",
        source_id="structured-feed",
        source_span=_span(start),
        structured_payload={"unit": "USD"},
        structural_status=StructuralStatus(
            passed=passed,
            contract_id="tests.StructuredReceiptContract",
            failures=failures,
        ),
    )


def _set(candidates: tuple[Candidate, ...]) -> CandidateSet:
    return CandidateSet(
        field_id="total_due",
        document_id="doc-1",
        candidates=candidates,
        strategy_id="composite:test",
    )


def test_exactly_one_passing_structured_candidate_auto_selects() -> None:
    gate = DeterministicSelectionGate()

    auto = gate.evaluate(
        _set(
            (
                _structured("structured-source:1", 0, passed=True),
                _text("regex:1", 2),
            ),
        ),
    )

    assert auto is not None
    assert auto.candidate_id == "structured-source:1"


def test_no_passing_structured_candidate_defers_to_selector() -> None:
    gate = DeterministicSelectionGate()

    assert (
        gate.evaluate(
            _set((_structured("structured-source:1", 0, passed=False), _text("regex:1", 2)))
        )
        is None
    )


def test_multiple_passing_structured_candidates_do_not_select_by_order() -> None:
    gate = DeterministicSelectionGate()

    assert (
        gate.evaluate(
            _set(
                (
                    _structured("structured-source:1", 0, passed=True),
                    _structured("structured-source:2", 2, passed=True),
                ),
            ),
        )
        is None
    )


def test_require_corroboration_forces_selector_even_with_one_structured_match() -> None:
    gate = DeterministicSelectionGate()

    assert (
        gate.evaluate(
            _set((_structured("structured-source:1", 0, passed=True), _text("regex:1", 2))),
            require_corroboration=True,
        )
        is None
    )
