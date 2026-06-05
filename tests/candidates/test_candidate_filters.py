"""CandidateFilter contract tests."""

from __future__ import annotations

from extractx.candidates.filters import apply_filter_binding
from extractx.core import (
    And,
    Candidate,
    CandidateSet,
    ContainedBy,
    ContextContains,
    FilterBinding,
    LabelIn,
    Not,
    NumericRange,
    SourceRef,
    SourceSpan,
)

SOURCE_REF = SourceRef(source_id="doc", content_hash="hash")


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=SOURCE_REF,
        text_anchor_space="normalized_text",
        byte_start=start,
        byte_end=end,
    )


def _candidate(
    candidate_id: str,
    *,
    text: str,
    start: int,
    end: int,
    entity_type: str | None,
    normalized_hint: object | None = None,
    context: str = "",
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        text=text,
        source_span=_span(start, end),
        entity_type=entity_type,
        normalized_hint=normalized_hint,
        context=context,
    )


def _candidate_set(*candidates: Candidate) -> CandidateSet:
    return CandidateSet(
        field_id="field",
        document_id="doc",
        candidates=candidates,
        strategy_id="test",
    )


def test_composed_filter_can_use_sibling_spans() -> None:
    candidate_set = _candidate_set(
        _candidate("date", text="October 5, 2028", start=0, end=15, entity_type="DATE"),
        _candidate("day", text="5", start=8, end=9, entity_type="CARDINAL"),
        _candidate("count", text="20", start=30, end=32, entity_type="CARDINAL"),
    )

    filtered = apply_filter_binding(
        candidate_set,
        FilterBinding(
            expr=And(
                exprs=(
                    LabelIn(labels=("CARDINAL",)),
                    Not(expr=ContainedBy(label="DATE")),
                ),
            ),
        ),
    )

    assert [c.candidate_id for c in filtered.candidates] == ["count"]


def test_numeric_range_uses_normalized_hint_when_present() -> None:
    candidate_set = _candidate_set(
        _candidate("small", text="$12", start=0, end=3, entity_type="MONEY", normalized_hint="12"),
        _candidate("large", text="$99", start=4, end=7, entity_type="MONEY", normalized_hint="99"),
    )

    filtered = apply_filter_binding(
        candidate_set,
        FilterBinding(expr=NumericRange(lo="50", hi="100")),
    )

    assert [c.candidate_id for c in filtered.candidates] == ["large"]


def test_numeric_range_keeps_unambiguous_phrasal_money_candidate() -> None:
    candidate_set = _candidate_set(
        _candidate(
            "total_due",
            text="approximately $116.18",
            start=0,
            end=21,
            entity_type="MONEY",
        ),
        _candidate(
            "ambiguous_phrase",
            text="8.6073 units per $1,000",
            start=22,
            end=46,
            entity_type="MONEY",
        ),
        _candidate("par", text="$1,000", start=47, end=53, entity_type="MONEY"),
    )

    filtered = apply_filter_binding(
        candidate_set,
        FilterBinding(expr=NumericRange(lo="0.5", hi="5000")),
    )

    assert [c.candidate_id for c in filtered.candidates] == ["total_due", "par"]


def test_numeric_range_uses_magnitude_normalization_for_subtotal_amounts() -> None:
    candidate_set = _candidate_set(
        _candidate("exchange", text="$258M", start=0, end=5, entity_type="MONEY"),
        _candidate("cash", text="about $42.1 million", start=6, end=25, entity_type="MONEY"),
        _candidate(
            "combined_phrase",
            text="about $258M and about $42.1M",
            start=26,
            end=54,
            entity_type="MONEY",
        ),
    )

    filtered = apply_filter_binding(
        candidate_set,
        FilterBinding(expr=NumericRange(lo="1", hi="500000000")),
    )

    assert [c.candidate_id for c in filtered.candidates] == ["exchange", "cash"]


def test_context_contains_can_match_any_or_all_terms_case_insensitively() -> None:
    candidate_set = _candidate_set(
        _candidate(
            "a",
            text="12",
            start=0,
            end=2,
            entity_type="CARDINAL",
            context="Shipment includes twelve units",
        ),
        _candidate(
            "b",
            text="7",
            start=3,
            end=4,
            entity_type="CARDINAL",
            context="meeting moved to seven",
        ),
    )

    filtered = apply_filter_binding(
        candidate_set,
        FilterBinding(expr=ContextContains(all_of=("shipment", "units"))),
    )

    assert [c.candidate_id for c in filtered.candidates] == ["a"]


def test_filter_binding_is_json_round_trip_safe() -> None:
    binding = FilterBinding(
        expr=And(
            exprs=(
                LabelIn(labels=("CARDINAL",)),
                Not(expr=ContainedBy(label="DATE")),
            ),
        ),
    )

    blob = binding.model_dump_json()

    assert FilterBinding.model_validate_json(blob) == binding
