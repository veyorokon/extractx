from __future__ import annotations

import pytest

from extractx.core.anchors import SourceRef, SourceSpan
from extractx.core.cardinality import Cardinality
from extractx.core.objects import (
    BudgetSpec,
    Candidate,
    CandidateSet,
    DistanceMetric,
    ExtractionSpec,
    FieldSpec,
    GroupingPolicy,
    Observation,
    PromptPolicy,
    ValidationPolicy,
)
from extractx.core.value_kinds import ValueKind
from extractx.schema.summary import summarize_spec
from extractx.selection import (
    ExpectedObservation,
    SelectorExample,
    export_selector_examples_jsonl,
    load_selector_examples_jsonl,
    score_selector_observation,
)


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="doc", content_hash="hash"),
        text_anchor_space="normalized_text",
        byte_start=start,
        byte_end=end,
    )


def _field_spec() -> FieldSpec:
    return FieldSpec(
        field_id="total_due",
        description="total due",
        value_kind=ValueKind.CARDINAL,
        cardinality=Cardinality.ONE,
        python_type=str,
    )


def _field_summary():
    spec = ExtractionSpec(
        fields=(_field_spec(),),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )
    return summarize_spec(spec).field_summaries[0]


def _candidate_set() -> CandidateSet:
    return CandidateSet(
        field_id="total_due",
        document_id="document-1",
        strategy_id="regex:total",
        candidates=(
            Candidate(
                candidate_id="cand-total",
                text="$34.32",
                source_span=_span(0, 6),
                context="receipt total $34.32",
            ),
            Candidate(
                candidate_id="cand-subtotal",
                text="$30.00",
                source_span=_span(20, 25),
                context="receipt subtotal $30.00",
            ),
        ),
    )


def _observation(
    *,
    selected: tuple[str, ...],
    evidence_id: str | None = None,
    abstain: bool = False,
) -> Observation:
    return Observation(
        field_id="total_due",
        evidence_id=evidence_id,
        abstain=abstain,
        outcome="ABSTAINED" if abstain else "SELECTED",
        selected_candidate_ids=selected,
        reason=None,
        producer_version="test-selector",
    )


def _example() -> SelectorExample:
    return SelectorExample(
        document_id="document-1",
        field_id="total_due",
        field_summary=_field_summary(),
        candidate_set=_candidate_set(),
        document_context="receipt total $34.32; receipt subtotal $30.00",
        expected=ExpectedObservation(selected_candidate_ids=("cand-total",), abstain=False),
        original_observation=_observation(selected=("cand-subtotal",)),
        metadata={"wrong_candidate_class": "subtotal_amount"},
    )


def test_score_selector_observation_exact_match() -> None:
    expected = ExpectedObservation(selected_candidate_ids=("cand-total",), abstain=False)
    actual = _observation(selected=("cand-total",))

    score = score_selector_observation(expected, actual)

    assert score.correct is True
    assert score.abstain_match is True
    assert score.selected_candidate_ids_match is True
    assert score.evidence_id_match is True
    assert score.reason is None


def test_score_selector_observation_reports_wrong_candidate() -> None:
    expected = ExpectedObservation(selected_candidate_ids=("cand-total",), abstain=False)
    actual = _observation(selected=("cand-subtotal",))

    score = score_selector_observation(expected, actual)

    assert score.correct is False
    assert score.abstain_match is True
    assert score.selected_candidate_ids_match is False
    assert score.evidence_id_match is False
    assert score.reason == "selector_score.mismatch: selected_candidate_ids, evidence_id"
    assert score.metadata["expected_selected_candidate_ids"] == ("cand-total",)
    assert score.metadata["actual_selected_candidate_ids"] == ("cand-subtotal",)


def test_score_selector_observation_scores_abstain() -> None:
    expected = ExpectedObservation(selected_candidate_ids=(), abstain=True)
    actual = _observation(selected=(), abstain=True)

    score = score_selector_observation(expected, actual)

    assert score.correct is True
    assert score.abstain_match is True
    assert score.selected_candidate_ids_match is True
    assert score.evidence_id_match is True


def test_expected_observation_rejects_inconsistent_abstain() -> None:
    with pytest.raises(ValueError, match="abstain=True"):
        ExpectedObservation(selected_candidate_ids=("cand-total",), abstain=True)


def test_selector_example_rejects_candidate_set_field_mismatch() -> None:
    candidate_set = _candidate_set().model_copy(update={"field_id": "subtotal_amount"})

    with pytest.raises(ValueError, match="candidate_set.field_id"):
        SelectorExample(
            document_id="document-1",
            field_id="total_due",
            field_summary=_field_summary(),
            candidate_set=candidate_set,
            document_context="context",
            expected=ExpectedObservation(selected_candidate_ids=("cand-total",), abstain=False),
        )


def test_selector_examples_jsonl_round_trip(tmp_path) -> None:
    path = tmp_path / "selector_examples.jsonl"
    example = _example()

    export_selector_examples_jsonl((example,), path)
    loaded = load_selector_examples_jsonl(path)

    assert loaded == (example,)
    assert path.read_text(encoding="utf-8").count("\n") == 1
