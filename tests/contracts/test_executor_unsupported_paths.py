"""contract tests for the M8 phase-1 unsupported-path gate.

every unsupported execution shape must surface as `InfrastructureError`
**before the run begins** per the M8 brief. these tests pin the exact
failure mode for each unsupported shape so a future thread that widens
the surface has to re-state the contract explicitly.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import BaseModel

from extractx import (
    ExecutorPolicy,
    ExtractionSpec,
    InfrastructureError,
    Runtime,
    ValueKind,
    extract_field,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.cardinality import Cardinality
from extractx.core.contracts import GroundedProposalGenerator
from extractx.core.objects import (
    BudgetSpec,
    DistanceMetric,
    FieldSpec,
    GroupingPolicy,
    InstanceProposerBinding,
    PromptPolicy,
    StrategyBinding,
    ValidationBinding,
    ValidationPolicy,
)
from extractx.core.versions import stable_hash


class _GoodPhone(BaseModel):
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


# ---------------------------------------------------------------------------
# unsupported deferred execution surface — phase 1 kernel only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deferred_execution_mode_rejects_repair_until_chained_repair_lands() -> None:
    spec = ExtractionSpec.from_pydantic(_GoodPhone)

    with pytest.raises(InfrastructureError, match="does not support repair=True"):
        await run_extraction(
            document="Call us at 555-1234.",
            spec=spec,
            runtime=Runtime(),
            policy=ExecutorPolicy(strategy="batch", execution_mode="deferred", repair=True),
        )


@pytest.mark.asyncio
async def test_deferred_execution_mode_requires_deferred_provider() -> None:
    spec = ExtractionSpec.from_pydantic(_GoodPhone)

    with pytest.raises(InfrastructureError, match="Runtime.deferred_provider"):
        await run_extraction(
            document="Call us at 555-1234.",
            spec=spec,
            runtime=Runtime(),
            policy=ExecutorPolicy(strategy="batch", execution_mode="deferred", repair=False),
        )


# ---------------------------------------------------------------------------
# unsupported iterative shape — multi-instance planning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iterative_strategy_rejects_multi_instance_specs() -> None:
    spec = _build_many_instance_spec()
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="iterative")

    with pytest.raises(InfrastructureError, match="single-instance specs"):
        await run_extraction(
            document="Call us at 555-1234.",
            spec=spec,
            runtime=runtime,
            policy=policy,
        )


# ---------------------------------------------------------------------------
# unsupported field path — strategy_bindings=()
# ---------------------------------------------------------------------------


def _build_spec_with_unbound_field() -> ExtractionSpec:
    field = FieldSpec(
        field_id="phone",
        description="phone number",
        value_kind=ValueKind.PERSON,
        cardinality=Cardinality.ONE,
        priority=0,
        depends_on=(),
        python_type=str,
        strategy_bindings=(),
        validation_binding=ValidationBinding(),
    )
    fields = (field,)
    return ExtractionSpec(
        fields=fields,
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="default"),
        ),
        budget=BudgetSpec(),
        version=stable_hash(("manual_unbound", "phone")),
        source_schema_ref=None,
    )


@pytest.mark.asyncio
async def test_unbound_strategy_raises_infrastructure_error() -> None:
    spec = _build_spec_with_unbound_field()
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    with pytest.raises(InfrastructureError, match="strategy_bindings=\\(\\)"):
        await run_extraction(
            document="Call us at 555-1234.",
            spec=spec,
            runtime=runtime,
            policy=policy,
        )


# ---------------------------------------------------------------------------
# unsupported field path — kind="grounded_proposal"
# ---------------------------------------------------------------------------


class _StubGenerator(GroundedProposalGenerator):
    """structural stub satisfying `GroundedProposalGenerator` so we can
    construct a `StrategyBinding(kind="grounded_proposal")` for the
    rejection test."""


class _StubInstanceProposer:
    pass


def _build_many_instance_spec() -> ExtractionSpec:
    base = ExtractionSpec.from_pydantic(_GoodPhone)
    return base.model_copy(
        update={
            "instance_cardinality": Cardinality.MANY,
            "instance_proposer_binding": InstanceProposerBinding(
                cls=_StubInstanceProposer,
            ),
        },
    )


@pytest.mark.asyncio
async def test_many_instance_cardinality_rejects_non_llm_proposer() -> None:
    spec = _build_many_instance_spec()

    with pytest.raises(InfrastructureError, match="supports only LLMInstanceProposer"):
        await run_extraction(
            document="Call us at 555-1234.",
            spec=spec,
            runtime=Runtime(),
            policy=ExecutorPolicy(strategy="independent"),
        )


def _build_grounded_spec() -> ExtractionSpec:
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
                cls=_StubGenerator,
                params={},
                kind="grounded_proposal",
            ),
        ),
        validation_binding=ValidationBinding(),
    )
    fields = (field,)
    return ExtractionSpec(
        fields=fields,
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="default"),
        ),
        budget=BudgetSpec(),
        version=stable_hash(("manual_grounded", "phone")),
        source_schema_ref=None,
    )


@pytest.mark.asyncio
async def test_grounded_proposal_binding_raises_infrastructure_error() -> None:
    spec = _build_grounded_spec()
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    with pytest.raises(InfrastructureError, match="grounded_proposal"):
        await run_extraction(
            document="Call us at 555-1234.",
            spec=spec,
            runtime=runtime,
            policy=policy,
        )


# ---------------------------------------------------------------------------
# unsupported strategy class — not RegexCandidateStrategy
# ---------------------------------------------------------------------------


class _OtherStrategy:
    """structural stub for a non-regex `CandidateStrategy`."""


def _build_spec_with_other_strategy() -> ExtractionSpec:
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
                cls=_OtherStrategy,
                params={},
                kind="candidate",
            ),
        ),
        validation_binding=ValidationBinding(),
    )
    return ExtractionSpec(
        fields=(field,),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="default"),
        ),
        budget=BudgetSpec(),
        version=stable_hash(("manual_other", "phone")),
        source_schema_ref=None,
    )


@pytest.mark.asyncio
async def test_non_regex_strategy_raises_infrastructure_error() -> None:
    spec = _build_spec_with_other_strategy()
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    with pytest.raises(
        InfrastructureError,
        match="RegexCandidateStrategy, NerCandidateStrategy, or LiteralSetCandidateStrategy",
    ):
        await run_extraction(
            document="Call us at 555-1234.",
            spec=spec,
            runtime=runtime,
            policy=policy,
        )


# ---------------------------------------------------------------------------
# unsupported document type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_str_non_bytes_document_raises_infrastructure_error() -> None:
    spec = ExtractionSpec.from_pydantic(_GoodPhone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    bad_input: Any = {"not": "a document"}
    with pytest.raises(InfrastructureError, match="str / bytes"):
        await run_extraction(
            document=bad_input,
            spec=spec,
            runtime=runtime,
            policy=policy,
        )


# ---------------------------------------------------------------------------
# pydantic-backed spec missing live schema_cls registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pydantic_spec_without_registered_class_raises_infrastructure_error() -> None:
    """fabricate a pydantic-backed-looking spec whose `version` is not
    registered in the in-process schema_cls registry. the executor must
    surface `InfrastructureError` rather than fall back to
    `source_schema_ref` resolution.
    """

    real_spec = ExtractionSpec.from_pydantic(_GoodPhone)
    # forge a new version that is **not** registered to any class;
    # source_schema_ref is preserved so the executor classifies the
    # spec as pydantic-backed.
    forged_version = stable_hash(("forged", real_spec.version))
    forged_spec = real_spec.model_copy(update={"version": forged_version})

    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    with pytest.raises(InfrastructureError, match="no live schema class"):
        await run_extraction(
            document="Call us at 555-1234.",
            spec=forged_spec,
            runtime=runtime,
            policy=policy,
        )
