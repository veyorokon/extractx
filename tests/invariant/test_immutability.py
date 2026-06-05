"""invariant tests: core lifecycle objects cannot be mutated after construction.

proof target: immutable/frozen core lifecycle objects cannot be mutated
after construction (see docs/architecture.md §15 anti-pattern
`Lifecycle-Object Conflation` and §6 separation rule "mutate `ProposedField`,
`ValidatedField`, or `Evidence` after construction").
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from extractx.core import (
    Candidate,
    Evidence,
    ExecutionTrace,
    Extraction,
    GroupingEvidence,
    Instance,
    InstanceGroupingKey,
    InstanceState,
    Observation,
    ProposalProvenance,
    ProposedField,
    SourceRef,
    SourceSpan,
    UsageEvent,
    ValidatedField,
)


def _span() -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="d", content_hash="h"),
        text_anchor_space="source_bytes",
        byte_start=0,
        byte_end=1,
    )


def _proposed() -> ProposedField:
    return ProposedField(
        field_id="total",
        raw_value="42",
        evidence_text="forty-two",
        source_span=_span(),
        strategy_id="regex:v1",
    )


def _validated() -> ValidatedField:
    return ValidatedField(
        proposed=_proposed(),
        normalized_value=42,
        field_validation_version="code:v1",
    )


def _resolved() -> Evidence:
    return Evidence(
        field_id="total",
        instance_key=InstanceGroupingKey(group_id="g", ordinal=0, group_anchors=(_span(),)),
        raw_value="42",
        evidence_text="forty-two",
        source_span=_span(),
        normalized_value=42,
        proposal_provenance=ProposalProvenance(strategy_id="regex:v1"),
    )


class TestLifecycleObjectsFrozen:
    @pytest.mark.parametrize(
        "obj_fn,attr",
        [
            (_proposed, "raw_value"),
            (_validated, "normalized_value"),
            (_resolved, "raw_value"),
        ],
        ids=["ProposedField", "ValidatedField", "Evidence"],
    )
    def test_mutation_rejected(self, obj_fn: object, attr: str) -> None:
        obj = obj_fn()  # type: ignore[operator]
        with pytest.raises(ValidationError):
            setattr(obj, attr, "mutated")


class TestCanonicalObjectsFrozen:
    def test_source_span_frozen(self) -> None:
        span = _span()
        with pytest.raises(ValidationError):
            span.byte_start = 999  # type: ignore[misc]

    def test_candidate_frozen(self) -> None:
        c = Candidate(candidate_id="1", text="t", source_span=_span())
        with pytest.raises(ValidationError):
            c.text = "u"  # type: ignore[misc]

    def test_selection_frozen(self) -> None:
        sel = Observation(
            outcome="SELECTED",
            selected_candidate_ids=("1",),
            producer_version="code:v1",
        )
        with pytest.raises(ValidationError):
            sel.outcome = "AMBIGUOUS"  # type: ignore[misc]

    def test_usage_event_frozen(self) -> None:
        event = UsageEvent(producer_version="code:v1", timestamp_ns=1)
        with pytest.raises(ValidationError):
            event.model_id = "mutated"  # type: ignore[misc]

    def test_instance_state_frozen(self) -> None:
        key = InstanceGroupingKey(group_id="g", ordinal=0, group_anchors=(_span(),))
        state = InstanceState(instance_key=key, version=0)
        with pytest.raises(ValidationError):
            state.version = 1  # type: ignore[misc]

    def test_instance_state_version_must_be_nonneg(self) -> None:
        key = InstanceGroupingKey(group_id="g", ordinal=0, group_anchors=(_span(),))
        with pytest.raises(ValidationError):
            InstanceState(instance_key=key, version=-1)

    def test_instance_proposal_set_frozen(self) -> None:
        key = InstanceGroupingKey(group_id="g", ordinal=0, group_anchors=(_span(),))
        inst = Instance(
            instance_key=key,
            outcome="complete",
            grouping_evidence=GroupingEvidence(
                stage="resolved",
                anchor_spans=(_span(),),
                producer_version="code:abc",
            ),
        )
        with pytest.raises(ValidationError):
            inst.outcome = "partial"  # type: ignore[misc]

    def test_extraction_result_frozen(self) -> None:
        key = InstanceGroupingKey(group_id="g", ordinal=0, group_anchors=(_span(),))
        inst = Instance(
            instance_key=key,
            outcome="complete",
            grouping_evidence=GroupingEvidence(
                stage="resolved",
                anchor_spans=(_span(),),
                producer_version="code:abc",
            ),
        )
        result = Extraction(
            document_id="doc-1",
            spec_version="v1",
            outcome="complete",
            strategy="independent",
            instances=(inst,),
            trace=ExecutionTrace(trace_id="t1"),
            replay_artifact_ref="artifact://1",
        )
        with pytest.raises(ValidationError):
            result.outcome = "failed"  # type: ignore[misc]
