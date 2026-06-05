"""Budgeted batch selector planner regressions.

These tests exercise ADR-0025 planning without calling a provider. The
critical contract is before seam D: large candidate payloads should either
pack into bounded selector calls or fail with a planner diagnostic before
soft compute is invoked.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Sequence

import pytest

from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core import (
    AnchorMap,
    BudgetSpec,
    Candidate,
    CandidateSet,
    Cardinality,
    ContextPack,
    DistanceMetric,
    DocumentView,
    ExtractionSpec,
    FieldSpec,
    GroupingPolicy,
    Message,
    PromptPolicy,
    RenderedPrompt,
    SourceRef,
    SourceSpan,
    StrategyBinding,
    ValidationBinding,
    ValidationPolicy,
    ValueKind,
)
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import Observation
from extractx.execution.runtime import Runtime
from extractx.execution.selector_planner import (
    BatchSelectorCallPlan,
    BudgetedBatchSelectorPlanner,
    SelectorTask,
    ShardedSelectorTaskPlan,
)
from extractx.execution.strategies.independent import IndependentStrategy


class _CountingRenderSelector:
    def __init__(self, *, fixed_chars: int = 0) -> None:
        self.render_calls = 0
        self.fixed_chars = fixed_chars

    def render_prompt(
        self,
        *,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
        context_pack: ContextPack,
        instance_ids: Sequence[str] = ("inst_0",),
    ) -> RenderedPrompt:
        del spec, context_pack, instance_ids
        self.render_calls += 1
        char_count = sum(
            len(candidate.context)
            for candidate_set in candidate_sets
            for candidate in candidate_set.candidates
        )
        return RenderedPrompt(
            messages=(Message(role="user", content="x" * (self.fixed_chars + char_count)),),
        )


class _CountingPromptEstimator:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, tasks: tuple[SelectorTask, ...]) -> int:
        self.calls += 1
        return sum(
            len(candidate.context)
            for task in tasks
            for candidate in task.candidate_set.candidates
        )


class _ShardSelector(_CountingRenderSelector):
    producer_version = "soft:test-shard-selector"

    def __init__(self, *, mode: str) -> None:
        super().__init__()
        self.mode = mode
        self.select_calls: list[tuple[str, tuple[str, ...]]] = []

    def select_many(
        self,
        *,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
        context_pack: ContextPack,
        instance_state: object | None = None,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> tuple[Observation, ...]:
        del spec, context_pack, instance_state
        observations: list[Observation] = []
        for candidate_set in candidate_sets:
            ids = tuple(candidate.candidate_id for candidate in candidate_set.candidates)
            self.select_calls.append((candidate_set.field_id, ids))
            if self.mode == "abstain":
                selected: tuple[str, ...] = ()
            elif self.mode == "many":
                selected = ids
            else:
                selected = ids[-1:]
            observations.append(
                Observation(
                    instance_id=instance_ids[0],
                    field_id=candidate_set.field_id,
                    evidence_id=selected[0] if selected else None,
                    abstain=not selected,
                    outcome="SELECTED" if selected else "ABSTAINED",
                    selected_candidate_ids=selected,
                    reason="synthetic shard selection",
                    producer_version=self.producer_version,
                ),
            )
        return tuple(observations)


def test_budgeted_planner_shards_synthetic_oversized_candidate_set() -> None:
    field_spec = _field_spec("maturity_date")
    candidate_set = _candidate_set(
        field_id="maturity_date",
        candidate_count=500,
        context_chars=500,
    )
    planner = BudgetedBatchSelectorPlanner(max_prompt_chars=120_000)
    estimator = _CountingPromptEstimator()

    plans = planner.plan(
        tasks=(_task(field_spec, candidate_set),),
        estimate_prompt_chars=estimator,
    )

    assert len(plans) == 1
    sharded = _assert_sharded_plan(plans[0])
    assert sum(
        len(shard.tasks[0].candidate_set.candidates)
        for shard in sharded.shards
    ) == 500
    assert all(
        shard.estimated_prompt_chars <= planner.max_prompt_chars
        for shard in sharded.shards
    )


def test_budgeted_planner_fails_fast_when_one_candidate_exceeds_budget() -> None:
    field_spec = _field_spec("maturity_date")
    candidate_set = _candidate_set(
        field_id="maturity_date",
        candidate_count=1,
        context_chars=500,
    )
    planner = BudgetedBatchSelectorPlanner(max_prompt_chars=75)

    with pytest.raises(
        InfrastructureError,
        match=(
            "selector_prompt_candidate_budget_exceeded: "
            "field_id='maturity_date' candidate_id='maturity_date-0'"
        ),
    ):
        planner.plan(
            tasks=(_task(field_spec, candidate_set),),
            estimate_prompt_chars=_CountingPromptEstimator(),
        )


def test_budgeted_planner_renders_each_single_field_task_once() -> None:
    field_specs = tuple(_field_spec(f"field_{index}") for index in range(3))
    candidate_sets = tuple(
        _candidate_set(field_id=field_spec.field_id, candidate_count=1, context_chars=10)
        for field_spec in field_specs
    )
    planner = BudgetedBatchSelectorPlanner(max_prompt_chars=45)
    estimator = _CountingPromptEstimator()

    plans = planner.plan(
        tasks=tuple(
            _task(field_spec, candidate_set)
            for field_spec, candidate_set in zip(field_specs, candidate_sets, strict=True)
        ),
        estimate_prompt_chars=estimator,
    )

    assert estimator.calls == 3
    batch_plans = tuple(_assert_batch_plan(plan) for plan in plans)
    assert tuple(len(plan.tasks) for plan in batch_plans) == (2, 1)


def test_budgeted_planner_shards_oversized_single_field_task() -> None:
    field_spec = _field_spec("maturity_date")
    candidate_set = _candidate_set(
        field_id="maturity_date",
        candidate_count=5,
        context_chars=20,
    )
    planner = BudgetedBatchSelectorPlanner(max_prompt_chars=75)

    plans = planner.plan(
        tasks=(_task(field_spec, candidate_set),),
        estimate_prompt_chars=_CountingPromptEstimator(),
    )

    assert len(plans) == 1
    sharded = _assert_sharded_plan(plans[0])
    assert tuple(
        len(shard.tasks[0].candidate_set.candidates)
        for shard in sharded.shards
    ) == (2, 2, 1)


def test_strategy_prompt_estimator_counts_fixed_demo_overhead_before_sharding() -> None:
    strategy = IndependentStrategy()
    field_spec = _field_spec("maturity_date")
    candidate_set = _candidate_set(
        field_id="maturity_date",
        candidate_count=5,
        context_chars=20,
    )
    spec = _spec((field_spec,), selector_prompt_max_chars=100)
    selector = _CountingRenderSelector(fixed_chars=60)

    plans = strategy._plan_batch_selector_calls(  # noqa: SLF001 - planner contract regression.
        selector=selector,  # type: ignore[arg-type]
        spec=spec,
        field_specs=(field_spec,),
        candidate_sets=(candidate_set,),
        document_view=_document(),
    )

    assert selector.render_calls >= 1
    sharded = _assert_sharded_plan(plans[0])
    assert tuple(
        len(shard.tasks[0].candidate_set.candidates)
        for shard in sharded.shards
    ) == (1, 1, 1, 1, 1)
    assert all(shard.estimated_prompt_chars <= 100 for shard in sharded.shards)


def test_sharded_optional_field_reduces_multiple_shard_winners() -> None:
    strategy = IndependentStrategy()
    field_spec = _field_spec("maturity_date")
    candidate_set = _candidate_set(
        field_id="maturity_date",
        candidate_count=5,
        context_chars=20,
    )
    spec = _spec((field_spec,), selector_prompt_max_chars=75)
    selector = _ShardSelector(mode="last")
    plan = BudgetedBatchSelectorPlanner(
        max_prompt_chars=spec.prompt_policy.selector_prompt_max_chars or 0,
    ).plan(
        tasks=(_task(field_spec, candidate_set),),
        estimate_prompt_chars=strategy._prompt_estimator(  # noqa: SLF001 - reducer contract regression.
            selector=selector,
            spec=spec,
            document_view=_document(),
        ),
    )[0]

    observation = strategy._select_sharded_field(  # noqa: SLF001 - planner contract regression.
        selector=selector,
        document_view=_document(),
        spec=spec,
        plan=plan,  # type: ignore[arg-type]
        runtime=Runtime(),
        usage_events=[],
    )

    assert observation.field_id == "maturity_date"
    assert observation.selected_candidate_ids == ("maturity_date-4",)
    assert selector.select_calls == [
        ("maturity_date", ("maturity_date-0", "maturity_date-1")),
        ("maturity_date", ("maturity_date-2", "maturity_date-3")),
        ("maturity_date", ("maturity_date-4",)),
        ("maturity_date", ("maturity_date-1", "maturity_date-3")),
        ("maturity_date", ("maturity_date-4",)),
        ("maturity_date", ("maturity_date-3", "maturity_date-4")),
    ]


def test_sharded_many_field_unions_shard_winners_in_source_order() -> None:
    strategy = IndependentStrategy()
    field_spec = _field_spec("labels", cardinality=Cardinality.MANY)
    candidate_set = _candidate_set(
        field_id="labels",
        candidate_count=5,
        context_chars=20,
    )
    spec = _spec((field_spec,), selector_prompt_max_chars=75)
    selector = _ShardSelector(mode="many")
    plan = BudgetedBatchSelectorPlanner(
        max_prompt_chars=spec.prompt_policy.selector_prompt_max_chars or 0,
    ).plan(
        tasks=(_task(field_spec, candidate_set),),
        estimate_prompt_chars=strategy._prompt_estimator(  # noqa: SLF001 - reducer contract regression.
            selector=selector,
            spec=spec,
            document_view=_document(),
        ),
    )[0]

    observation = strategy._select_sharded_field(  # noqa: SLF001 - planner contract regression.
        selector=selector,
        document_view=_document(),
        spec=spec,
        plan=plan,  # type: ignore[arg-type]
        runtime=Runtime(),
        usage_events=[],
    )

    assert observation.field_id == "labels"
    assert observation.selected_candidate_ids == tuple(f"labels-{index}" for index in range(5))


def test_sharded_optional_field_fails_when_reducer_makes_no_progress() -> None:
    strategy = IndependentStrategy()
    field_spec = _field_spec("maturity_date")
    candidate_set = _candidate_set(
        field_id="maturity_date",
        candidate_count=5,
        context_chars=20,
    )
    spec = _spec((field_spec,), selector_prompt_max_chars=75)
    selector = _ShardSelector(mode="many")
    plan = BudgetedBatchSelectorPlanner(
        max_prompt_chars=spec.prompt_policy.selector_prompt_max_chars or 0,
    ).plan(
        tasks=(_task(field_spec, candidate_set),),
        estimate_prompt_chars=strategy._prompt_estimator(  # noqa: SLF001 - reducer contract regression.
            selector=selector,
            spec=spec,
            document_view=_document(),
        ),
    )[0]

    with pytest.raises(
        InfrastructureError,
        match="selector_prompt_reducer_no_progress",
    ):
        strategy._select_sharded_field(  # noqa: SLF001 - reducer contract regression.
            selector=selector,
            document_view=_document(),
            spec=spec,
            plan=plan,  # type: ignore[arg-type]
            runtime=Runtime(),
            usage_events=[],
        )


def _field_spec(
    field_id: str,
    *,
    cardinality: Cardinality = Cardinality.OPTIONAL,
) -> FieldSpec:
    return FieldSpec(
        field_id=field_id,
        description=f"{field_id} value",
        value_kind=ValueKind.PERSON,
        cardinality=cardinality,
        priority=0,
        depends_on=(),
        python_type=str,
        strategy_bindings=(
            StrategyBinding(cls=RegexCandidateStrategy, kind="candidate", params={}),
        ),
        validation_binding=ValidationBinding(),
    )


def _spec(
    field_specs: tuple[FieldSpec, ...],
    *,
    selector_prompt_max_chars: int,
) -> ExtractionSpec:
    return ExtractionSpec(
        fields=field_specs,
        prompt_policy=PromptPolicy(selector_prompt_max_chars=selector_prompt_max_chars),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(default_distance_metric=DistanceMetric(name="default")),
        budget=BudgetSpec(),
        version="test-spec",
    )


def _task(field_spec: FieldSpec, candidate_set: CandidateSet) -> SelectorTask:
    return SelectorTask(field_spec=field_spec, candidate_set=candidate_set)


def _assert_batch_plan(plan: object) -> BatchSelectorCallPlan:
    assert isinstance(plan, BatchSelectorCallPlan)
    return plan


def _assert_sharded_plan(plan: object) -> ShardedSelectorTaskPlan:
    assert isinstance(plan, ShardedSelectorTaskPlan)
    return plan


def _candidate_set(
    *,
    field_id: str,
    candidate_count: int,
    context_chars: int,
) -> CandidateSet:
    return CandidateSet(
        field_id=field_id,
        document_id="doc",
        candidates=tuple(
            Candidate(
                candidate_id=f"{field_id}-{index}",
                text=f"value-{index}",
                source_span=_span(index, index + 1),
                context=f"context-{index} " + ("x" * context_chars),
                context_span=_span(index, index + context_chars),
            )
            for index in range(candidate_count)
        ),
        strategy_id="synthetic:test",
    )


def _document() -> DocumentView:
    source_ref = SourceRef(source_id="doc", content_hash="hash")
    return DocumentView(
        document_id="doc",
        normalized_text="x" * 1_000_000,
        anchor_map=AnchorMap(),
        source_ref=source_ref,
    )


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="doc", content_hash="hash"),
        text_anchor_space="normalized_text",
        byte_start=start,
        byte_end=end,
    )
