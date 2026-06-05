"""contract tests for `NegativeOutcome`, `ValidationFailure`, and
`Extraction` derived projections.

proof targets:
- `NegativeOutcome` / `ValidationFailure` shapes match docs/architecture.md §9.
- `Extraction.evidence()` and `.negatives()` flatten canonically.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from extractx.core import (
    Evidence,
    ExecutionTrace,
    Extraction,
    GroupingEvidence,
    Instance,
    InstanceGroupingKey,
    NegativeOutcome,
    ProposalProvenance,
    ProposedField,
    SourceRef,
    SourceSpan,
    ValidationFailure,
)


def _span() -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="d", content_hash="h"),
        text_anchor_space="source_bytes",
        byte_start=0,
        byte_end=1,
    )


def _instance_key(group_id: str = "g1", ordinal: int = 0) -> InstanceGroupingKey:
    return InstanceGroupingKey(group_id=group_id, ordinal=ordinal, group_anchors=(_span(),))


def _resolved(field_id: str, key: InstanceGroupingKey) -> Evidence:
    return Evidence(
        field_id=field_id,
        instance_key=key,
        raw_value="42",
        evidence_text="forty-two",
        source_span=_span(),
        normalized_value=42,
        proposal_provenance=ProposalProvenance(strategy_id="regex:v1"),
    )


def _negative(category: str, code: str, key: InstanceGroupingKey) -> NegativeOutcome:
    return NegativeOutcome(
        category=category,  # type: ignore[arg-type]
        code=code,
        instance_key=key,
        reason="test",
    )


def _instance(
    key: InstanceGroupingKey,
    *,
    proposals: tuple[Evidence, ...] = (),
    negatives: tuple[NegativeOutcome, ...] = (),
) -> Instance:
    return Instance(
        instance_key=key,
        outcome="complete",
        evidence=proposals,
        negative_outcomes=negatives,
        grouping_evidence=GroupingEvidence(
            stage="resolved",
            anchor_spans=(_span(),),
            producer_version="code:abc",
        ),
    )


class TestNegativeOutcomeShape:
    def test_valid_categories(self) -> None:
        for cat in ("selection", "validation", "budget", "resolution", "adaptation", "planning"):
            NegativeOutcome(category=cat, code="x", reason="r")  # type: ignore[arg-type]

    def test_invalid_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NegativeOutcome(category="mystery", code="x", reason="r")  # type: ignore[arg-type]

    def test_ambiguous_grouping_shape_adr0003(self) -> None:
        """ADR-0003: ambiguous grouping negatives use `resolution` +
        `ambiguous_grouping`.
        """

        key = _instance_key()
        n = NegativeOutcome(
            category="resolution",
            code="ambiguous_grouping",
            field_id="total",
            instance_key=key,
            reason="candidates span two blocks",
        )
        assert n.category == "resolution"
        assert n.code == "ambiguous_grouping"
        assert n.field_id == "total"
        assert n.instance_key == key


class TestValidationFailureShape:
    def test_valid_layers(self) -> None:
        for layer in ("candidate", "field", "instance"):
            ValidationFailure(layer=layer, field_id="f", reason="r")  # type: ignore[arg-type]

    def test_invalid_layer_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ValidationFailure(layer="other", field_id="f", reason="r")  # type: ignore[arg-type]


class TestExtractionProjections:
    def test_proposals_flattens_in_declaration_order(self) -> None:
        k1 = _instance_key("g1", 0)
        k2 = _instance_key("g2", 1)
        r1 = _resolved("total", k1)
        r2 = _resolved("vendor", k1)
        r3 = _resolved("total", k2)

        result = Extraction(
            document_id="doc-1",
            spec_version="v1",
            outcome="complete",
            strategy="independent",
            instances=(
                _instance(k1, proposals=(r1, r2)),
                _instance(k2, proposals=(r3,)),
            ),
            trace=ExecutionTrace(trace_id="t1"),
            replay_artifact_ref="artifact://1",
        )
        assert result.evidence() == (r1, r2, r3)

    def test_negatives_flattens_in_declaration_order(self) -> None:
        k1 = _instance_key("g1", 0)
        k2 = _instance_key("g2", 1)
        n1 = _negative("selection", "abstained", k1)
        n2 = _negative("validation", "cardinality.one_expected_many_selected", k2)

        result = Extraction(
            document_id="doc-1",
            spec_version="v1",
            outcome="partial",
            strategy="iterative",
            instances=(
                _instance(k1, negatives=(n1,)),
                _instance(k2, negatives=(n2,)),
            ),
            trace=ExecutionTrace(trace_id="t1"),
            replay_artifact_ref="artifact://1",
        )
        assert result.negatives() == (n1, n2)

    def test_instances_is_canonical_proposals_is_derived(self) -> None:
        """changing `proposals()` output requires changing `instances`;
        the reverse is not true."""

        k1 = _instance_key()
        result = Extraction(
            document_id="doc-1",
            spec_version="v1",
            outcome="complete",
            strategy="independent",
            instances=(_instance(k1),),
            trace=ExecutionTrace(trace_id="t1"),
            replay_artifact_ref="artifact://1",
        )
        assert result.evidence() == ()
        # instances holds one (empty) instance set, confirming canonical
        # vs derived: empty proposals come from having one instance with
        # no evidence, not from having zero instances.
        assert len(result.instances) == 1


class TestProposedFieldShape:
    def test_instance_key_is_tentative(self) -> None:
        proposed = ProposedField(
            field_id="total",
            tentative_instance_key=None,
            raw_value="42",
            evidence_text="42",
            source_span=_span(),
            strategy_id="regex:v1",
        )
        assert proposed.tentative_instance_key is None
        # normalized_value is not a field on ProposedField by design —
        # normalization happens at seam F layer 2.
        assert not hasattr(proposed, "normalized_value")
