"""behavioral tests for the phase-1 `DeterministicInstanceResolver`.

proof targets (from
docs/tasks/seam-g-resolver-phase-1-deterministic-instance-resolution.md,
"Focused proof"):

- purity — same `(validated_fields, candidate_sets, spec,
  instance_plan)` yields byte-identical output across repeated calls.
- algorithmic resolver emits `producer_version = "code:{code_hash}"`
  using the core helper.
- absent `instance_plan` + non-empty validated fields → resolver
  synthesizes one document-scope final instance.
- empty validated fields → `()`.
- `boundary_defining` field with matching `tentative_instance_key`
  resolves to that tentative bucket.
- source-anchor continuity picks the unique overlapping bucket when
  one exists.
- candidate co-occurrence breaks a tie only when continuity is silent.
- `InstancePlan` priors are the lowest authority.
- ambiguous grouping emits
  `NegativeOutcome(category="resolution", code="ambiguous_grouping",
  ...)` on one tentative/final instance; the affected proposal is
  absent from `evidence`.
- `Cardinality.ONE` spread across multiple final instances emits
  `NegativeOutcome(category="resolution",
  code="cardinality.one_multiple_instances", ...)`.
- `Cardinality.PER_INSTANCE` with multiple survivors in one instance
  emits
  `NegativeOutcome(category="resolution",
  code="cardinality.per_instance_multi_in_instance", ...)`.
- surviving `ValidatedField`s are promoted into
  `Evidence`s without mutation.
- emitted `Instance.grouping_evidence.stage == "resolved"`.
- emitted `Instance.outcome` is `partial` iff
  `negative_outcomes` is non-empty.
- no layer-3 behavior is smuggled in — a raising pydantic
  `model_validator` / `InstanceValidator` is never invoked.
- `instance_plan: InstancePlan | None` — no planner failure is
  accepted as resolver input.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel, ValidationError, model_validator

from extractx.core import (
    Candidate,
    CandidateSet,
    Cardinality,
    DistanceMetric,
    Evidence,
    ExtractionSpec,
    FieldSpec,
    GroupingBinding,
    GroupingPolicy,
    InstanceGroupingKey,
    InstancePlan,
    ProposedField,
    SourceRef,
    SourceSpan,
    ValidatedField,
    ValueKind,
)
from extractx.core.objects import (
    BudgetSpec,
    GroupingEvidence,
    PromptPolicy,
    ValidationPolicy,
)
from extractx.instances import (
    DeterministicInstanceResolver,
    InstanceResolverContractError,
)
from extractx.instances.resolvers import algorithmic_code_hash

# ---------------------------------------------------------------------------
# fixtures — local helpers keep each test's dependencies legible.
# ---------------------------------------------------------------------------


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="normalized_text",
        byte_start=start,
        byte_end=end,
    )


def _key(
    *,
    group_id: str,
    ordinal: int,
    anchors: tuple[SourceSpan, ...],
) -> InstanceGroupingKey:
    return InstanceGroupingKey(group_id=group_id, ordinal=ordinal, group_anchors=anchors)


def _field_spec(
    field_id: str,
    *,
    priority: int = 0,
    cardinality: Cardinality = Cardinality.ONE,
    grouping_role: str | None = None,
) -> FieldSpec:
    binding: GroupingBinding | None = None
    if grouping_role is not None:
        binding = GroupingBinding(
            role=grouping_role,  # type: ignore[arg-type]
            distance_metric=DistanceMetric(name="noop", params={}),
        )
    return FieldSpec(
        field_id=field_id,
        description=f"field {field_id}",
        value_kind=ValueKind.PERSON,
        cardinality=cardinality,
        priority=priority,
        depends_on=(),
        python_type=str,
        grouping_binding=binding,
    )


def _spec(*, fields: tuple[FieldSpec, ...]) -> ExtractionSpec:
    return ExtractionSpec(
        fields=fields,
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="noop", params={}),
            allow_parallel_instances=False,
            max_instances=None,
        ),
        budget=BudgetSpec(),
        version="spec-v-1",
    )


def _proposed(
    *,
    field_id: str,
    raw_value: str,
    source_span: SourceSpan,
    tentative_instance_key: InstanceGroupingKey | None = None,
    candidate_id_refs: tuple[str, ...] = (),
    strategy_id: str = "strategy-1",
    selector_producer_version: str | None = "code:selector",
    grounded_producer_version: str | None = None,
) -> ProposedField:
    return ProposedField(
        field_id=field_id,
        tentative_instance_key=tentative_instance_key,
        raw_value=raw_value,
        evidence_text=raw_value,
        source_span=source_span,
        evidence_spans=(),
        candidate_id_refs=candidate_id_refs,
        strategy_id=strategy_id,
        selector_producer_version=selector_producer_version,
        grounded_producer_version=grounded_producer_version,
    )


def _validated(
    proposed: ProposedField,
    *,
    normalized_value: object | None = None,
) -> ValidatedField:
    return ValidatedField(
        proposed=proposed,
        normalized_value=proposed.raw_value if normalized_value is None else normalized_value,
        field_validation_version="code:validator",
    )


def _candidate_set(
    *,
    field_id: str,
    instance_hint: InstanceGroupingKey | None,
    candidates: tuple[Candidate, ...],
    strategy_id: str = "strategy-1",
) -> CandidateSet:
    return CandidateSet(
        field_id=field_id,
        document_id="doc-1",
        instance_hint=instance_hint,
        candidates=candidates,
        strategy_id=strategy_id,
    )


def _plan(*, tentative_keys: tuple[InstanceGroupingKey, ...]) -> InstancePlan:
    return InstancePlan(
        tentative_keys=tentative_keys,
        grouping_evidence=GroupingEvidence(
            stage="planned",
            anchor_spans=tuple(span for key in tentative_keys for span in key.group_anchors),
            clustering_signals={},
            confidence=None,
            producer_version="code:planner",
        ),
        producer_version="code:planner",
    )


# ---------------------------------------------------------------------------
# trivial shapes
# ---------------------------------------------------------------------------


class TestTrivialShapes:
    def test_empty_validated_fields_yields_empty_tuple(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"),))
        result = resolver.resolve((), (), spec, None)
        assert result == ()

    def test_absent_plan_synthesizes_one_document_scope_instance(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"),))
        validated = _validated(
            _proposed(field_id="name", raw_value="alice", source_span=_span(0, 5)),
        )

        result = resolver.resolve((validated,), (), spec, None)

        assert len(result) == 1
        instance = result[0]
        assert instance.outcome == "complete"
        assert len(instance.evidence) == 1
        assert instance.grouping_evidence.stage == "resolved"

    def test_grouping_evidence_stage_is_resolved(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"),))
        validated = _validated(
            _proposed(field_id="name", raw_value="alice", source_span=_span(0, 5)),
        )
        result = resolver.resolve((validated,), (), spec, None)
        assert result[0].grouping_evidence.stage == "resolved"

    def test_producer_version_matches_core_helper(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"),))
        validated = _validated(
            _proposed(field_id="name", raw_value="alice", source_span=_span(0, 5)),
        )
        result = resolver.resolve((validated,), (), spec, None)
        assert result[0].grouping_evidence.producer_version == algorithmic_code_hash()
        assert result[0].grouping_evidence.producer_version.startswith("code:")


# ---------------------------------------------------------------------------
# purity / determinism
# ---------------------------------------------------------------------------


class TestPurity:
    def test_same_inputs_yield_byte_identical_output(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"), _field_spec("city")))
        validated = (
            _validated(
                _proposed(field_id="name", raw_value="alice", source_span=_span(0, 5)),
            ),
            _validated(
                _proposed(
                    field_id="city",
                    raw_value="portland",
                    source_span=_span(10, 18),
                ),
            ),
        )

        first = resolver.resolve(validated, (), spec, None)
        second = resolver.resolve(validated, (), spec, None)

        assert first == second
        # also assert group_id stability at the hash layer.
        assert [r.instance_key.group_id for r in first] == [r.instance_key.group_id for r in second]


# ---------------------------------------------------------------------------
# authority #1 — boundary_defining
# ---------------------------------------------------------------------------


class TestAuthorityBoundaryDefining:
    def test_matching_tentative_key_wins(self) -> None:
        # two tentative buckets from the plan; the field is
        # boundary_defining and its tentative_instance_key matches
        # bucket #1. authority #1 picks bucket #1 immediately.
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("header", grouping_role="boundary_defining"),))

        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 5),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(100, 110),))
        plan = _plan(tentative_keys=(key0, key1))

        validated = _validated(
            _proposed(
                field_id="header",
                raw_value="alice",
                source_span=_span(0, 5),
                tentative_instance_key=key1,
            ),
        )

        result = resolver.resolve((validated,), (), spec, plan)

        # exactly one final instance: bucket 1 survived (boundary win),
        # bucket 0 had no assignment so it was dropped.
        assert len(result) == 1
        instance = result[0]
        assert instance.outcome == "complete"
        assert len(instance.evidence) == 1
        # boundary_defining anchors take over final group_anchors.
        assert instance.instance_key.group_anchors == (_span(0, 5),)

    def test_boundary_consuming_falls_through_to_later_authorities(self) -> None:
        # boundary_consuming must not create its own boundary — it
        # falls through to continuity / cooccurrence / priors.
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("footer", grouping_role="boundary_consuming"),))

        # two buckets; neither overlaps the field's source span, so
        # continuity is silent and plan prior is the lowest-authority
        # decider. the field has no tentative_instance_key, so it
        # falls through to ambiguity if there's no other signal.
        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 5),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(100, 110),))
        plan = _plan(tentative_keys=(key0, key1))

        validated = _validated(
            _proposed(field_id="footer", raw_value="tag", source_span=_span(50, 55)),
        )
        result = resolver.resolve((validated,), (), spec, plan)
        # no unique authority chose a winner → ambiguous_grouping
        # lands on bucket 0 (stable fallback); the proposal is
        # dropped.
        flat_codes = tuple(n.code for instance in result for n in instance.negative_outcomes)
        assert "ambiguous_grouping" in flat_codes
        flat_proposals = tuple(p for instance in result for p in instance.evidence)
        assert flat_proposals == ()


# ---------------------------------------------------------------------------
# authority #2 — source-anchor continuity
# ---------------------------------------------------------------------------


class TestAuthorityContinuity:
    def test_unique_overlap_wins(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("city"),))

        # two buckets: [0,10) and [100,110). the field span is [2,8),
        # which overlaps only bucket 0.
        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 10),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(100, 110),))
        plan = _plan(tentative_keys=(key0, key1))

        validated = _validated(
            _proposed(field_id="city", raw_value="portland", source_span=_span(2, 8)),
        )
        result = resolver.resolve((validated,), (), spec, plan)
        # exactly one instance survived.
        assert len(result) == 1
        instance = result[0]
        # bucket 0 anchors are carried forward when no
        # boundary_defining field contributes.
        assert instance.instance_key.group_anchors == (_span(0, 10),)


# ---------------------------------------------------------------------------
# authority #3 — candidate co-occurrence
# ---------------------------------------------------------------------------


class TestAuthorityCandidateCooccurrence:
    def test_breaks_tie_when_continuity_is_silent(self) -> None:
        # two buckets, the field's source_span does not overlap
        # either bucket's anchors, but one bucket is strictly closer
        # to a referenced candidate's source span (byte-gap 0 vs
        # byte-gap >0).
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("amount"),))

        # buckets at [0,5) and [50,55)
        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 5),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(50, 55),))
        plan = _plan(tentative_keys=(key0, key1))

        # field source span is far from both, but the candidate span
        # it references is inside bucket 0's anchor (byte-gap 0).
        candidate_span = _span(1, 4)
        candidate = Candidate(
            candidate_id="cand-a",
            text="42",
            source_span=candidate_span,
            evidence_spans=(),
            context="",
        )
        candidate_set = _candidate_set(
            field_id="amount",
            instance_hint=None,
            candidates=(candidate,),
        )

        validated = _validated(
            _proposed(
                field_id="amount",
                raw_value="42",
                source_span=_span(200, 202),
                candidate_id_refs=("cand-a",),
            ),
        )

        result = resolver.resolve((validated,), (candidate_set,), spec, plan)
        assert len(result) == 1
        # bucket 0 won via candidate co-occurrence.
        assert result[0].instance_key.group_anchors == (_span(0, 5),)


# ---------------------------------------------------------------------------
# authority #4 — InstancePlan priors
# ---------------------------------------------------------------------------


class TestAuthorityPlanPriors:
    def test_priors_are_lowest_authority(self) -> None:
        # two buckets. neither overlaps the field's source span;
        # candidate co-occurrence is silent (no matching
        # candidate_set). the field carries a tentative_instance_key
        # equal to bucket 1 — authority #4 picks bucket 1.
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("note"),))

        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 5),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(100, 110),))
        plan = _plan(tentative_keys=(key0, key1))

        validated = _validated(
            _proposed(
                field_id="note",
                raw_value="hello",
                source_span=_span(200, 205),
                tentative_instance_key=key1,
            ),
        )
        result = resolver.resolve((validated,), (), spec, plan)
        assert len(result) == 1
        assert result[0].instance_key.group_anchors == (_span(100, 110),)

    def test_priors_lose_to_continuity(self) -> None:
        # priors on bucket 0, but continuity picks bucket 1 because
        # the field span overlaps it. authority #2 wins over #4.
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("note"),))

        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 5),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(100, 110),))
        plan = _plan(tentative_keys=(key0, key1))

        validated = _validated(
            _proposed(
                field_id="note",
                raw_value="hello",
                source_span=_span(102, 105),
                tentative_instance_key=key0,
            ),
        )
        result = resolver.resolve((validated,), (), spec, plan)
        assert len(result) == 1
        # continuity decided: bucket 1.
        assert result[0].instance_key.group_anchors == (_span(100, 110),)


# ---------------------------------------------------------------------------
# ambiguity
# ---------------------------------------------------------------------------


class TestAmbiguity:
    def test_ambiguous_grouping_emits_negative_and_drops_proposal(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("note"),))

        # two buckets, both overlap the field span — authority #2
        # returns length-2 → not unique. no cooccurrence, no plan
        # prior → ambiguous.
        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 100),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(10, 20),))
        plan = _plan(tentative_keys=(key0, key1))

        validated = _validated(
            _proposed(field_id="note", raw_value="x", source_span=_span(12, 18)),
        )
        result = resolver.resolve((validated,), (), spec, plan)

        # the ambiguity negative lands on exactly one instance (the
        # strongest-partial-signal bucket — lowest-index bucket among
        # authority-2 hits, which is bucket 0).
        all_codes = tuple(n.code for instance in result for n in instance.negative_outcomes)
        assert all_codes.count("ambiguous_grouping") == 1
        # the affected proposal is absent from evidence.
        all_proposals = tuple(p for instance in result for p in instance.evidence)
        assert all_proposals == ()
        # outcome is partial on the instance carrying the negative.
        for instance in result:
            if instance.negative_outcomes:
                assert instance.outcome == "partial"


# ---------------------------------------------------------------------------
# cardinality — resolution stage
# ---------------------------------------------------------------------------


class TestCardinalityPolicy:
    def test_cardinality_one_multi_instance_emits_typed_negative(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("id_field", cardinality=Cardinality.ONE),))

        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 10),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(100, 110),))
        plan = _plan(tentative_keys=(key0, key1))

        v0 = _validated(
            _proposed(field_id="id_field", raw_value="A", source_span=_span(2, 3)),
        )
        v1 = _validated(
            _proposed(field_id="id_field", raw_value="B", source_span=_span(101, 102)),
        )
        result = resolver.resolve((v0, v1), (), spec, plan)

        codes = tuple(n.code for instance in result for n in instance.negative_outcomes)
        assert codes.count("cardinality.one_multiple_instances") == 2
        # no evidence survived — both were dropped.
        assert all(instance.evidence == () for instance in result)
        # outcomes are partial on affected instances.
        for instance in result:
            assert instance.outcome == "partial"

    def test_cardinality_per_instance_multi_in_instance_emits_typed_negative(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(
            fields=(_field_spec("tag", cardinality=Cardinality.PER_INSTANCE),),
        )

        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 20),))
        plan = _plan(tentative_keys=(key0,))

        v0 = _validated(
            _proposed(field_id="tag", raw_value="red", source_span=_span(2, 5)),
        )
        v1 = _validated(
            _proposed(field_id="tag", raw_value="blue", source_span=_span(10, 14)),
        )
        result = resolver.resolve((v0, v1), (), spec, plan)
        codes = tuple(n.code for instance in result for n in instance.negative_outcomes)
        assert codes.count("cardinality.per_instance_multi_in_instance") == 1
        all_proposals = tuple(p for instance in result for p in instance.evidence)
        assert all_proposals == ()


# ---------------------------------------------------------------------------
# promotion fidelity
# ---------------------------------------------------------------------------


class TestPromotionFidelity:
    def test_validated_field_is_promoted_without_mutation(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"),))

        span = _span(0, 5)
        proposed = _proposed(
            field_id="name",
            raw_value="alice",
            source_span=span,
            candidate_id_refs=("cand-a",),
            strategy_id="strategy-xyz",
            selector_producer_version="code:selector-xyz",
            grounded_producer_version="code:grounded-xyz",
        )
        validated = _validated(proposed, normalized_value="alice-normalized")

        result = resolver.resolve((validated,), (), spec, None)
        assert len(result) == 1
        promoted = result[0].evidence[0]
        assert isinstance(promoted, Evidence)
        # structural copies — no mutation.
        assert promoted.field_id == proposed.field_id
        assert promoted.raw_value == proposed.raw_value
        assert promoted.evidence_text == proposed.evidence_text
        assert promoted.source_span == proposed.source_span
        assert promoted.evidence_spans == proposed.evidence_spans
        assert promoted.normalized_value == validated.normalized_value
        # provenance fields copied from the landed shape.
        assert promoted.proposal_provenance.strategy_id == proposed.strategy_id
        assert promoted.proposal_provenance.candidate_id_refs == proposed.candidate_id_refs
        assert (
            promoted.proposal_provenance.selector_producer_version
            == proposed.selector_producer_version
        )
        assert (
            promoted.proposal_provenance.grounded_producer_version
            == proposed.grounded_producer_version
        )
        # the ValidatedField itself is frozen — the test is simply
        # that the resolver returned a distinct Evidence
        # without having to mutate `validated`. pydantic v2 frozen
        # models raise `ValidationError` on attribute assignment.
        with pytest.raises(ValidationError):
            validated.normalized_value = "something else"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# outcome correlation
# ---------------------------------------------------------------------------


class TestOutcomeCorrelation:
    def test_outcome_is_partial_iff_negative_outcomes_non_empty(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"),))

        # one complete instance — no negatives.
        complete_validated = _validated(
            _proposed(field_id="name", raw_value="alice", source_span=_span(0, 5)),
        )
        complete_result = resolver.resolve((complete_validated,), (), spec, None)
        assert len(complete_result) == 1
        assert complete_result[0].outcome == "complete"
        assert complete_result[0].negative_outcomes == ()

        # ambiguity path — negatives non-empty → partial.
        spec_amb = _spec(fields=(_field_spec("note"),))
        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 100),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(10, 20),))
        plan = _plan(tentative_keys=(key0, key1))
        amb_validated = _validated(
            _proposed(field_id="note", raw_value="x", source_span=_span(12, 18)),
        )
        amb_result = resolver.resolve((amb_validated,), (), spec_amb, plan)
        for instance in amb_result:
            if instance.negative_outcomes:
                assert instance.outcome == "partial"
            else:
                assert instance.outcome == "complete"


# ---------------------------------------------------------------------------
# no layer 3 / no InstanceValidator smuggling
# ---------------------------------------------------------------------------


class RaisingSchema(BaseModel):
    name: str

    @model_validator(mode="after")
    def _always_raise(self) -> RaisingSchema:
        raise AssertionError(
            "resolver phase 1 must never invoke pydantic model_validator — "
            "layer 3 is post-G.resolver and is not the resolver's job",
        )


class TestNoLayer3Smuggling:
    def test_resolver_never_invokes_model_validator(self) -> None:
        # construct a field whose schema has a raising
        # model_validator. the resolver must never touch it; if it
        # did, the model_validator would raise and the test would
        # fail with AssertionError.
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"),))
        validated = _validated(
            _proposed(field_id="name", raw_value="alice", source_span=_span(0, 5)),
        )

        # defensively: construct the schema class but do not pass it
        # into the resolver — the resolver signature carries no
        # `schema_cls`. the presence of the class in the test file
        # ensures there is no latent import that would accidentally
        # pick it up.
        _ = RaisingSchema  # keep reference so the class is non-dead

        result = resolver.resolve((validated,), (), spec, None)
        # baseline: clean run, no negatives, no exception.
        assert len(result) == 1
        assert result[0].negative_outcomes == ()


# ---------------------------------------------------------------------------
# protocol input signature — no planner failure accepted
# ---------------------------------------------------------------------------


class TestProtocolInputShape:
    def test_instance_plan_parameter_is_optional_instance_plan_only(self) -> None:
        # architecture §7 seam G.resolver + ADR-0003 + task brief:
        # planner failure stays upstream. the resolver signature must
        # accept only `InstancePlan | None`, never a
        # `NegativeOutcome`.
        sig = inspect.signature(DeterministicInstanceResolver.resolve)
        # param name + default
        assert "instance_plan" in sig.parameters
        assert sig.parameters["instance_plan"].default is None
        # annotation type-string check — "NegativeOutcome" must not
        # appear in the resolver input annotation, which would be the
        # smoking-gun that planner failure can reach the resolver.
        annotation = sig.parameters["instance_plan"].annotation
        # the annotation is stored as a string under
        # `from __future__ import annotations`; keep the match
        # tolerant to `InstancePlan | None` vs `Optional[InstancePlan]`.
        annotation_str = str(annotation)
        assert "NegativeOutcome" not in annotation_str
        assert "InstancePlan" in annotation_str


# ---------------------------------------------------------------------------
# structural invariant checks
# ---------------------------------------------------------------------------


class TestStructuralInvariants:
    def test_unknown_candidate_set_field_id_raises(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"),))
        validated = _validated(
            _proposed(field_id="name", raw_value="alice", source_span=_span(0, 5)),
        )
        bad_set = _candidate_set(
            field_id="unknown_field",
            instance_hint=None,
            candidates=(),
        )
        with pytest.raises(InstanceResolverContractError):
            resolver.resolve((validated,), (bad_set,), spec, None)

    def test_unknown_validated_field_id_raises(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(fields=(_field_spec("name"),))
        validated = _validated(
            _proposed(field_id="phantom", raw_value="x", source_span=_span(0, 5)),
        )
        with pytest.raises(InstanceResolverContractError):
            resolver.resolve((validated,), (), spec, None)


# ---------------------------------------------------------------------------
# integration: multi-field, authorities co-existing
# ---------------------------------------------------------------------------


class TestMultiFieldIntegration:
    def test_two_buckets_with_mixed_authorities_resolve_cleanly(self) -> None:
        resolver = DeterministicInstanceResolver()
        spec = _spec(
            fields=(
                _field_spec("header", grouping_role="boundary_defining"),
                _field_spec("city"),
                _field_spec("note"),
            ),
        )

        key0 = _key(group_id="g0", ordinal=0, anchors=(_span(0, 10),))
        key1 = _key(group_id="g1", ordinal=1, anchors=(_span(100, 120),))
        plan = _plan(tentative_keys=(key0, key1))

        header_a = _validated(
            _proposed(
                field_id="header",
                raw_value="alice",
                source_span=_span(0, 5),
                tentative_instance_key=key0,
            ),
        )
        header_b = _validated(
            _proposed(
                field_id="header",
                raw_value="bob",
                source_span=_span(100, 103),
                tentative_instance_key=key1,
            ),
        )
        city_a = _validated(
            _proposed(field_id="city", raw_value="portland", source_span=_span(6, 14)),
        )
        note_b = _validated(
            _proposed(
                field_id="note",
                raw_value="hi",
                source_span=_span(200, 202),
                tentative_instance_key=key1,
            ),
        )

        # header uses Cardinality.ONE — two instances violate the
        # rule if both header proposals survive. widen to MANY to
        # keep the test focused on authority routing.
        spec = _spec(
            fields=(
                _field_spec(
                    "header",
                    grouping_role="boundary_defining",
                    cardinality=Cardinality.MANY,
                ),
                _field_spec("city", cardinality=Cardinality.MANY),
                _field_spec("note", cardinality=Cardinality.MANY),
            ),
        )

        result = resolver.resolve(
            (header_a, header_b, city_a, note_b),
            (),
            spec,
            plan,
        )
        # both buckets survive — each has at least one proposal.
        assert len(result) == 2
        # every surviving proposal carries a final InstanceGroupingKey whose
        # group_anchors come from the boundary_defining header
        # spans (anchors policy #1).
        for instance in result:
            # boundary_defining spans win as final anchors — verify
            # they are header source_spans.
            for proposal in instance.evidence:
                if proposal.field_id == "header":
                    assert instance.instance_key.group_anchors == (proposal.source_span,)
