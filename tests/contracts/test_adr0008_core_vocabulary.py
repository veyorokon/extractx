"""ADR-0008 core vocabulary contract tests."""

from __future__ import annotations

import extractx
from extractx.core import (
    Evidence,
    ExecutionTrace,
    Extraction,
    GroupingEvidence,
    Instance,
    InstanceGroupingKey,
    Observation,
    ProposalProvenance,
    SourceRef,
    SourceSpan,
)

OLD_PUBLIC_NAMES = {
    "Selection",
    "ResolvedFieldProposal",
    "InstanceResult",
    "ExtractionResult",
    "InstanceKey",
}


def _span() -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="doc", content_hash="hash"),
        text_anchor_space="source_bytes",
        byte_start=0,
        byte_end=3,
    )


def _grouping_key() -> InstanceGroupingKey:
    return InstanceGroupingKey(group_id="inst-1", ordinal=0, group_anchors=(_span(),))


def test_old_names_are_not_public_root_exports() -> None:
    for name in OLD_PUBLIC_NAMES:
        assert name not in extractx.__all__
        assert not hasattr(extractx, name)


def test_observation_sets_evidence_id_from_single_selected_candidate() -> None:
    observation = Observation(
        field_id="amount",
        outcome="SELECTED",
        selected_candidate_ids=("cand-1",),
        producer_version="code:selector",
    )

    assert observation.evidence_id == "cand-1"
    assert observation.abstain is False


def test_evidence_accepts_legacy_instance_key_but_exposes_instance_id() -> None:
    key = _grouping_key()

    evidence = Evidence(
        field_id="amount",
        instance_key=key,
        raw_value="100",
        evidence_text="100",
        source_span=_span(),
        normalized_value=100,
        proposal_provenance=ProposalProvenance(strategy_id="regex"),
    )

    assert evidence.instance_id == "inst-1"
    assert evidence.instance_key == key


def test_instance_evidence_is_canonical_with_legacy_projection() -> None:
    key = _grouping_key()
    item = Evidence(
        field_id="amount",
        instance_id=key.group_id,
        instance_key=key,
        raw_value="100",
        evidence_text="100",
        source_span=_span(),
        normalized_value=100,
        proposal_provenance=ProposalProvenance(strategy_id="regex"),
    )

    instance = Instance(
        instance_id=key.group_id,
        instance_key=key,
        outcome="complete",
        evidence=(item,),
        grouping_evidence=GroupingEvidence(
            stage="resolved",
            anchor_spans=(_span(),),
            producer_version="code:resolver",
        ),
    )
    extraction = Extraction(
        document_id="doc",
        spec_version="spec",
        outcome="complete",
        strategy="independent",
        instances=(instance,),
        trace=ExecutionTrace(trace_id="trace"),
        replay_artifact_ref="",
    )

    assert instance.evidence == (item,)
    assert extraction.evidence() == (item,)
