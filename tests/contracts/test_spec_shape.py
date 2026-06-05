"""contract tests for `ExtractionSpec` / `FieldSpec` typed shape.

proof targets:
- `ExtractionSpec` / `FieldSpec` typed shapes include ADR-0005 additions
  (`PromptPolicy.candidate_overflow_policy`, `PromptPolicy.candidate_count_bound`,
  `FieldSpec.sorter_binding`, `ContextPack.candidate_overflow`).
- `InterviewTranscript` remains field-scoped (`field_id` non-optional per
  ADR-0004).
- typed containers reject unknown fields.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from extractx.core import (
    BudgetSpec,
    Candidate,
    CandidateOverflowMetadata,
    Cardinality,
    ContextBudget,
    ContextPack,
    DistanceMetric,
    ExtractionSpec,
    FieldSpec,
    GroupingPolicy,
    InstanceCandidate,
    InstanceCandidateSet,
    InstanceProposerBinding,
    InterviewTranscript,
    PromptPolicy,
    SorterBinding,
    StrategyBinding,
    ValidationBinding,
    ValidationPolicy,
    ValueKind,
)


class _FakeStrategy:
    """stand-in for a CandidateStrategy class referenced by StrategyBinding."""


class _FakeSorter:
    """stand-in for a CandidateSorter class referenced by SorterBinding."""


class _FakeInstanceProposer:
    """stand-in for an InstanceProposer class referenced by InstanceProposerBinding."""


def _field_spec(
    *,
    field_id: str = "total",
    depends_on: tuple[str, ...] = (),
    sorter: SorterBinding | None = None,
) -> FieldSpec:
    return FieldSpec(
        field_id=field_id,
        description="test field",
        value_kind=ValueKind.register("MONEY"),
        cardinality=Cardinality.ONE,
        priority=0,
        depends_on=depends_on,
        python_type=str,
        strategy_bindings=(StrategyBinding(cls=_FakeStrategy, kind="candidate"),),
        validation_binding=ValidationBinding(),
        sorter_binding=sorter,
    )


def _spec(prompt_policy: PromptPolicy | None = None) -> ExtractionSpec:
    return ExtractionSpec(
        fields=(_field_spec(),),
        prompt_policy=prompt_policy or PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="v1",
    )


class TestPromptPolicyAdr0005:
    def test_defaults(self) -> None:
        p = PromptPolicy()
        assert p.candidate_overflow_policy == "fail"
        assert p.candidate_count_bound is None
        assert p.selector_prompt_max_chars is None

    def test_truncate_sorted_policy(self) -> None:
        p = PromptPolicy(
            candidate_overflow_policy="truncate_sorted",
            candidate_count_bound=64,
            selector_prompt_max_chars=120_000,
        )
        assert p.candidate_overflow_policy == "truncate_sorted"
        assert p.candidate_count_bound == 64
        assert p.selector_prompt_max_chars == 120_000

    def test_rejects_unknown_policy(self) -> None:
        with pytest.raises(ValidationError):
            PromptPolicy(candidate_overflow_policy="drop_random")  # type: ignore[arg-type]

    def test_rejects_non_positive_selector_prompt_max_chars(self) -> None:
        with pytest.raises(ValidationError):
            PromptPolicy(selector_prompt_max_chars=0)


class TestFieldSpecSorterBindingAdr0005:
    def test_sorter_binding_optional_default_none(self) -> None:
        f = _field_spec()
        assert f.sorter_binding is None

    def test_sorter_binding_accepts_binding(self) -> None:
        sorter = SorterBinding(cls=_FakeSorter)
        f = _field_spec(sorter=sorter)
        assert f.sorter_binding is sorter


class TestContextPackOverflowSignalAdr0005:
    def test_default_overflow_is_none(self) -> None:
        pack = ContextPack(schema_description="s", document_summary="d")
        assert pack.candidate_overflow is None
        assert pack.bounds == ContextBudget()

    def test_overflow_metadata_carried_through(self) -> None:
        meta = CandidateOverflowMetadata(
            source_candidate_count=100,
            presented_candidate_count=20,
            sorter_id="code:abc",
            overflow_policy="truncate_sorted",
        )
        pack = ContextPack(
            schema_description="s",
            document_summary="d",
            candidate_overflow=meta,
        )
        assert pack.candidate_overflow == meta


class TestInterviewTranscriptFieldScopedAdr0004:
    def test_field_id_required(self) -> None:
        with pytest.raises(ValidationError):
            # omit field_id to prove it has no default. we deliberately
            # pass the wrong shape to catch the pydantic-level error.
            InterviewTranscript(  # type: ignore[call-arg]
                attempt_index=0,
                producer_version="code:abc",
                message_history_json="[]",
                timestamp_ns=1,
            )

    def test_field_id_cannot_be_none(self) -> None:
        with pytest.raises(ValidationError):
            InterviewTranscript(
                field_id=None,  # type: ignore[arg-type]
                attempt_index=0,
                producer_version="code:abc",
                message_history_json="[]",
                timestamp_ns=1,
            )

    def test_valid_construction(self) -> None:
        t = InterviewTranscript(
            field_id="total",
            attempt_index=0,
            producer_version="code:abc",
            message_history_json="[]",
            timestamp_ns=1_700_000_000_000_000_000,
        )
        assert t.field_id == "total"
        assert t.instance_key is None


class TestExtractionSpecTyped:
    def test_spec_constructs_with_bindings(self) -> None:
        spec = _spec()
        assert spec.fields[0].field_id == "total"
        assert spec.instance_type == "ExtractionInstance"
        assert spec.instance_cardinality is Cardinality.ONE
        assert spec.instance_proposer_binding is None
        assert spec.prompt_policy.candidate_overflow_policy == "fail"

    def test_many_requires_instance_proposer_binding(self) -> None:
        with pytest.raises(ValidationError):
            ExtractionSpec(
                fields=(_field_spec(),),
                instance_cardinality=Cardinality.MANY,
                prompt_policy=PromptPolicy(),
                validation_policy=ValidationPolicy(),
                grouping_policy=GroupingPolicy(
                    default_distance_metric=DistanceMetric(name="default"),
                ),
                budget=BudgetSpec(),
                version="v1",
            )

    def test_one_rejects_instance_proposer_binding(self) -> None:
        with pytest.raises(ValidationError):
            ExtractionSpec(
                fields=(_field_spec(),),
                instance_cardinality=Cardinality.ONE,
                instance_proposer_binding=InstanceProposerBinding(
                    cls=_FakeInstanceProposer,
                ),
                prompt_policy=PromptPolicy(),
                validation_policy=ValidationPolicy(),
                grouping_policy=GroupingPolicy(
                    default_distance_metric=DistanceMetric(name="default"),
                ),
                budget=BudgetSpec(),
                version="v1",
            )

    def test_instance_candidate_set_requires_resolved_instance_type(self) -> None:
        with pytest.raises(ValidationError):
            InstanceCandidate(instance_id="inst_0")  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            InstanceCandidateSet(  # type: ignore[call-arg]
                document_id="doc-1",
                candidates=(),
            )

    def test_spec_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            ExtractionSpec(  # type: ignore[call-arg]
                fields=(),
                prompt_policy=PromptPolicy(),
                validation_policy=ValidationPolicy(),
                grouping_policy=GroupingPolicy(
                    default_distance_metric=DistanceMetric(name="default"),
                ),
                budget=BudgetSpec(),
                version="v1",
                unknown_knob="nope",
            )


class TestCandidateShape:
    def test_candidate_default_evidence_spans_empty(self) -> None:
        from extractx.core import SourceRef, SourceSpan

        span = SourceSpan(
            source_ref=SourceRef(source_id="d", content_hash="h"),
            text_anchor_space="source_bytes",
            byte_start=0,
            byte_end=1,
        )
        c = Candidate(candidate_id="1", text="t", source_span=span)
        assert c.evidence_spans == ()
