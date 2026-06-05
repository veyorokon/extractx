"""integration proof: seam-F layer 3 is wired into the M8 executor.

proof targets (from docs/tasks/seam-f-layer3-phase-1-instance-validation.md):

- single canonical invocation: each `Instance` reaching layer 3 is
  validated exactly once by the executor; resolver does not invoke the
  validator (already protected by the resolver's contract test, mirrored
  here at the call-site).
- failure escalation: a raising `model_validator(mode="after")` produces
  `NegativeOutcome(category="validation", code="instance_failure", ...)`
  appended to the affected `Instance.negative_outcomes`. the
  escalated negative carries `field_id=None`, the same `instance_key`,
  the failure reason, and `candidate_count=None`. instance outcome flips
  `complete -> partial`. `evidence` remain intact.
- no reassignment: `instance_key` is unchanged across the failure path.
- determinism: same M8 inputs → byte-identical post-layer-3
  `Extraction` payload.
- success-path identity: on success, the executor returns the original
  `Instance` reference unchanged (no defensive rebuild).
- manual-spec pass-through: layer 3 is a no-op for `schema_cls=None`.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import BaseModel, model_validator

from extractx import (
    ExecutorPolicy,
    ExtractionSpec,
    Runtime,
    ValueKind,
    extract_field,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.cardinality import Cardinality
from extractx.core.objects import (
    BudgetSpec,
    DistanceMetric,
    FieldSpec,
    GroupingPolicy,
    PromptPolicy,
    StrategyBinding,
    ValidationBinding,
    ValidationPolicy,
)
from extractx.core.outcomes import ObjectIssue
from extractx.core.versions import stable_hash
from extractx.execution.executor.serial import SerialExecutor
from extractx.proposals.validation import LayeredProposalValidator
from extractx.schema import extractx_object_validator

# ---------------------------------------------------------------------------
# pydantic-backed specs with model_validator(mode="after")
# ---------------------------------------------------------------------------


class _PassingPhone(BaseModel):
    """spec with a passing `model_validator(mode="after")`.

    layer 3 fires once on success; the original `Instance` is
    returned unchanged.
    """

    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )

    @model_validator(mode="after")
    def _ok(self) -> _PassingPhone:
        # always passes — we only care that it runs and that the
        # success-path returns the original instance reference.
        return self


class _RejectingPhone(BaseModel):
    """spec whose `model_validator(mode="after")` always rejects."""

    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )

    @model_validator(mode="after")
    def _bad(self) -> _RejectingPhone:
        raise ValueError("layer-3 reject: phone instance is invalid")


class _CountingCallsPhone(BaseModel):
    """spec used to assert single canonical invocation per instance."""

    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )

    @model_validator(mode="after")
    def _record(self) -> _CountingCallsPhone:
        _SINGLE_INVOCATION_COUNTER.append(1)
        return self


_SINGLE_INVOCATION_COUNTER: list[int] = []


class _ObjectRejectingPhone(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )

    @staticmethod
    @extractx_object_validator(implicates=("phone",))
    def _bad(values: dict[str, Any], evidence: dict[str, Any]) -> ObjectIssue:
        del values, evidence
        return ObjectIssue(code="phone_object_reject", reason="phone object rejected")


# ---------------------------------------------------------------------------
# success path — identity preserved, layer 3 fires once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_layer3_success_returns_clean_extraction_result() -> None:
    spec = ExtractionSpec.from_pydantic(_PassingPhone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    # layer-3 success → no instance_failure negatives, outcome stays
    # complete, single instance with the original proposal.
    assert result.outcome == "complete"
    assert len(result.instances) == 1
    sole = result.instances[0]
    assert sole.outcome == "complete"
    assert sole.negative_outcomes == ()
    proposal = sole.evidence[0]
    assert proposal.field_id == "phone"
    assert proposal.normalized_value == "555-1234"


@pytest.mark.asyncio
async def test_layer3_runs_exactly_once_per_resolved_instance() -> None:
    spec = ExtractionSpec.from_pydantic(_CountingCallsPhone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    _SINGLE_INVOCATION_COUNTER.clear()
    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    # one resolved instance → exactly one layer-3 invocation. if the
    # resolver also invoked validators (it must not per ADR-0003), or
    # if the strategy duplicated the call, the counter would tick more
    # than once.
    assert result.outcome == "complete"
    assert len(result.instances) == 1
    assert sum(_SINGLE_INVOCATION_COUNTER) == 1


# ---------------------------------------------------------------------------
# failure escalation — typed NegativeOutcome appended on the same instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_layer3_failure_escalates_to_typed_negative_outcome() -> None:
    spec = ExtractionSpec.from_pydantic(_RejectingPhone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    # the resolver still produced one instance — layer-3 failure does
    # not drop the instance.
    assert len(result.instances) == 1
    sole = result.instances[0]

    # outcome flips complete → partial; Extraction.outcome
    # rolls up to partial.
    assert sole.outcome == "partial"
    assert result.outcome == "partial"

    # evidence remain intact — layer 3 never mutates or drops
    # resolved proposals on failure.
    assert len(sole.evidence) == 1
    assert sole.evidence[0].field_id == "phone"
    assert sole.evidence[0].normalized_value == "555-1234"

    # exactly one escalated negative on the affected instance.
    assert len(sole.negative_outcomes) == 1
    negative = sole.negative_outcomes[0]
    assert negative.category == "validation"
    assert negative.code == "instance_failure"
    assert negative.field_id is None  # cross-field; no individual field
    assert negative.instance_key == sole.instance_key  # unchanged
    assert "layer-3 reject" in negative.reason
    assert negative.candidate_count is None


@pytest.mark.asyncio
async def test_layer3_failure_does_not_change_instance_key() -> None:
    spec = ExtractionSpec.from_pydantic(_RejectingPhone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    # run once with a passing spec to capture the resolver's
    # `instance_key` for the same input. then run with the rejecting
    # spec and assert the failure path carries the same key forward.
    result_pass = await run_extraction(
        document="Call us at 555-1234.",
        spec=ExtractionSpec.from_pydantic(_PassingPhone),
        runtime=runtime,
        policy=policy,
    )
    expected_key = result_pass.instances[0].instance_key

    result_fail = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    # `instance_key` is unchanged on the failure path — layer 3 never
    # rebuckets, removes, or rewrites grouping truth.
    assert result_fail.instances[0].instance_key == expected_key


@pytest.mark.asyncio
async def test_object_validator_issue_escalates_to_typed_negative_outcome() -> None:
    spec = ExtractionSpec.from_pydantic(_ObjectRejectingPhone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert result.outcome == "partial"
    sole = result.instances[0]
    negative = sole.negative_outcomes[0]
    assert negative.category == "validation"
    assert negative.code == "instance_failure"
    assert negative.object_issues[0].code == "phone_object_reject"
    assert negative.object_issues[0].implicates[0].field_id == "phone"


# ---------------------------------------------------------------------------
# manual-spec pass-through — layer 3 is a no-op when schema_cls is None
# ---------------------------------------------------------------------------


def _identity_normalizer(raw: Any) -> Any:
    return raw


def _build_manual_spec() -> ExtractionSpec:
    field = FieldSpec(
        field_id="phone",
        description="phone number",
        value_kind=ValueKind.PERSON,
        cardinality=Cardinality.ONE,
        priority=0,
        depends_on=(),
        python_type=str,
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
        validation_binding=ValidationBinding(
            normalizer=_identity_normalizer,
            field_validators=(),
        ),
    )
    fields = (field,)
    payload = {
        "manual": True,
        "fields": [
            {
                "field_id": f.field_id,
                "cardinality": f.cardinality.value,
                "value_kind": f.value_kind.name,
            }
            for f in fields
        ],
    }
    version = stable_hash(payload)
    return ExtractionSpec(
        fields=fields,
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="default"),
        ),
        budget=BudgetSpec(),
        version=version,
        source_schema_ref=None,
    )


@pytest.mark.asyncio
async def test_manual_spec_layer3_is_pass_through() -> None:
    spec = _build_manual_spec()
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    # manual-spec layer 3 is a no-op pass-through; the resolved
    # instance flows through unchanged.
    assert result.outcome == "complete"
    assert len(result.instances) == 1
    sole = result.instances[0]
    assert sole.outcome == "complete"
    assert sole.negative_outcomes == ()
    assert sole.evidence[0].normalized_value == "555-1234"


# ---------------------------------------------------------------------------
# determinism — same inputs → same post-layer-3 Extraction bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_layer3_result_is_deterministic_on_pass() -> None:
    spec = ExtractionSpec.from_pydantic(_PassingPhone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    a = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    b = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    # byte-identical Extraction shape across two runs.
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


@pytest.mark.asyncio
async def test_post_layer3_result_is_deterministic_on_failure() -> None:
    spec = ExtractionSpec.from_pydantic(_RejectingPhone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    a = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    b = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert a.model_dump(mode="json") == b.model_dump(mode="json")


# ---------------------------------------------------------------------------
# success-path identity at the executor — original Instance reference
# is returned unchanged
# ---------------------------------------------------------------------------


def test_executor_layer3_success_preserves_instance_identity() -> None:
    """white-box assertion against the executor's per-instance call.

    `_apply_layer3_validation` is pinned to return the original
    `Instance` reference on layer-3 success — no defensive
    rebuild. this preserves identity through the pipeline so the
    `Extraction.instances` tuple shares object identity with
    what `G.resolver` produced when layer 3 has nothing to add.
    """

    from extractx.core.anchors import SourceRef as _SourceRef
    from extractx.core.objects import (
        GroupingEvidence,
        InstanceGroupingKey,
        SourceSpan,
    )
    from extractx.core.outcomes import (
        Evidence,
        Instance,
        ProposalProvenance,
    )

    executor = SerialExecutor()

    ref = _SourceRef(source_id="d", content_hash="sha256:1")
    span = SourceSpan(
        source_ref=ref,
        text_anchor_space="source_bytes",
        byte_start=0,
        byte_end=4,
    )
    key = InstanceGroupingKey(group_id="g", ordinal=0, group_anchors=(span,))
    proposal = Evidence(
        field_id="phone",
        instance_key=key,
        raw_value="555-1234",
        evidence_text="555-1234",
        source_span=span,
        evidence_spans=(),
        normalized_value="555-1234",
        proposal_provenance=ProposalProvenance(strategy_id="regex:test"),
    )
    grouping = GroupingEvidence(
        stage="resolved",
        anchor_spans=(span,),
        clustering_signals={},
        confidence=None,
        producer_version="code:test",
    )
    instance = Instance(
        instance_key=key,
        outcome="complete",
        evidence=(proposal,),
        negative_outcomes=(),
        grouping_evidence=grouping,
    )
    spec = _build_manual_spec()  # schema_cls handed in next call

    rebuilt = executor._apply_layer3_validation(  # type: ignore[reportPrivateUsage]
        final_instances=(instance,),
        spec=spec,
        schema_cls=_PassingPhone,
    )

    # success path → the executor returns the same instance reference.
    assert len(rebuilt) == 1
    assert rebuilt[0] is instance


# ---------------------------------------------------------------------------
# call-site singularity — only the executor invokes layer 3 in phase 1
# ---------------------------------------------------------------------------


def test_strategy_does_not_invoke_layer3_directly() -> None:
    """`IndependentStrategy.run(...)` must not call
    `LayeredProposalValidator.validate_instance(...)` directly.

    seam F layer 3 is executor-owned per ADR-0003 + the layer-3 brief.
    if a future change moves the call into the strategy, this guard
    surfaces the duplication.
    """

    import inspect

    from extractx.execution.strategies import independent as _independent

    # the validator type is constructed inside the strategy for layers
    # 1+2, so the symbol shows up in source. but `validate_instance`
    # — the layer-3 method name — must not appear in the strategy
    # module.
    source = inspect.getsource(_independent)
    assert "validate_instance" not in source, (
        "IndependentStrategy must not invoke `validate_instance` — "
        "seam F layer 3 is executor-owned per ADR-0003"
    )


def test_resolver_does_not_invoke_validators() -> None:
    """`DeterministicInstanceResolver` must not call any validator.

    ADR-0003: G.resolver does not invoke `InstanceValidator`s or
    pydantic `model_validator`s. `validate` and `validate_instance`
    must not appear in the resolver source.
    """

    import inspect

    from extractx.instances.resolvers import deterministic as _resolver_mod

    source = inspect.getsource(_resolver_mod)
    assert "LayeredProposalValidator" not in source
    assert "validate_instance(" not in source
    # `validate(` is too generic — we just guard against a bound call
    # to `LayeredProposalValidator` by checking the type-name above.


# ---------------------------------------------------------------------------
# the LayeredProposalValidator instance under the executor satisfies the
# widened protocol — surface check at the wiring boundary.
# ---------------------------------------------------------------------------


def test_executor_holds_a_layered_validator_with_validate_instance() -> None:
    executor = SerialExecutor()
    # private attribute — the executor pins this for phase 1 per the
    # brief (no Runtime widening). the assertion guards against a
    # future refactor that drops the validator handle without rewiring.
    validator = executor._validator  # type: ignore[reportPrivateUsage]
    assert isinstance(validator, LayeredProposalValidator)
    assert hasattr(validator, "validate_instance")
