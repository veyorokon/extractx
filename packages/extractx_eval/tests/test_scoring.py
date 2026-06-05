from __future__ import annotations

from extractx.core.anchors import SourceRef, SourceSpan
from extractx.core.objects import GroupingEvidence, InstanceGroupingKey
from extractx.core.outcomes import Evidence, Instance, ProposalProvenance

from extractx_eval import ExpectedField, ExpectedInstance, score_instances


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _span(start: int = 0, end: int = 1) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def _key(ordinal: int = 0) -> InstanceGroupingKey:
    return InstanceGroupingKey(
        group_id=f"group-{ordinal}",
        ordinal=ordinal,
        group_anchors=(_span(),),
    )


def _proposal(
    field_id: str,
    normalized_value: object,
    *,
    key: InstanceGroupingKey | None = None,
    offset: int = 0,
) -> Evidence:
    instance_key = key if key is not None else _key()
    return Evidence(
        field_id=field_id,
        instance_id=instance_key.group_id,
        instance_key=instance_key,
        raw_value=str(normalized_value),
        evidence_text=str(normalized_value),
        source_span=_span(offset, offset + 1),
        evidence_spans=(_span(offset, offset + 1),),
        normalized_value=normalized_value,
        proposal_provenance=ProposalProvenance(strategy_id="test"),
    )


def _instance(
    proposals: tuple[Evidence, ...] = (),
    *,
    ordinal: int = 0,
) -> Instance:
    key = _key(ordinal)
    return Instance(
        instance_id=key.group_id,
        instance_key=key,
        outcome="complete",
        evidence=proposals,
        grouping_evidence=GroupingEvidence(
            stage="resolved",
            anchor_spans=(_span(),),
            producer_version="test",
        ),
    )


def test_score_instances_returns_no_misses_for_exact_match() -> None:
    actual = (_instance((_proposal("phone", "555-1234"),)),)
    expected = (ExpectedInstance((ExpectedField("phone", "555-1234"),)),)

    misses = score_instances(
        case_id="exact",
        expected=expected,
        actual=actual,
        replay_artifact_ref="replay-1",
    )

    assert misses == ()


def test_score_instances_reports_missing_field() -> None:
    actual = (_instance(()),)
    expected = (
        ExpectedInstance(
            (ExpectedField("phone", "555-1234", source_text="555-1234"),),
        ),
    )

    misses = score_instances(case_id="missing", expected=expected, actual=actual)

    assert len(misses) == 1
    miss = misses[0]
    assert miss.kind == "missing_field"
    assert miss.case_id == "missing"
    assert miss.instance_index == 0
    assert miss.instance_id == "group-0"
    assert miss.field_id == "phone"
    assert miss.expected == "555-1234"
    assert miss.source_text == "555-1234"


def test_score_instances_reports_unexpected_field() -> None:
    actual = (_instance((_proposal("phone", "555-1234"),)),)
    expected = (ExpectedInstance(()),)

    misses = score_instances(case_id="unexpected", expected=expected, actual=actual)

    assert len(misses) == 1
    miss = misses[0]
    assert miss.kind == "unexpected_field"
    assert miss.instance_index == 0
    assert miss.instance_id == "group-0"
    assert miss.field_id == "phone"
    assert miss.actual == "555-1234"
    assert miss.source_text == "555-1234"
    assert miss.evidence_spans != ()


def test_score_instances_reports_value_mismatch() -> None:
    actual = (_instance((_proposal("phone", "555-1234"),)),)
    expected = (ExpectedInstance((ExpectedField("phone", "555-9999"),)),)

    misses = score_instances(
        case_id="value-mismatch",
        expected=expected,
        actual=actual,
        replay_artifact_ref="replay-1",
    )

    assert len(misses) == 1
    miss = misses[0]
    assert miss.kind == "value_mismatch"
    assert miss.replay_artifact_ref == "replay-1"
    assert miss.instance_id == "group-0"
    assert miss.expected == "555-9999"
    assert miss.actual == "555-1234"


def test_score_instances_reports_instance_count_mismatch_and_overlapping_diffs() -> None:
    actual = (_instance((_proposal("phone", "555-1234"),)),)
    expected = (
        ExpectedInstance((ExpectedField("phone", "555-9999"),)),
        ExpectedInstance((ExpectedField("phone", "555-5678"),)),
    )

    misses = score_instances(case_id="count", expected=expected, actual=actual)

    assert tuple(miss.kind for miss in misses) == (
        "instance_count_mismatch",
        "value_mismatch",
    )
    count_miss = misses[0]
    assert count_miss.kind == "instance_count_mismatch"
    assert count_miss.expected_count == 2
    assert count_miss.actual_count == 1
