"""Budgeted selector-call planning.

This module owns the pre-seam-D planning contract from ADR-0025. It
operates only on canonical candidate sets and a prompt-size estimator. It
does not call selectors, record usage, validate proposals, or assemble
observations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import Candidate, CandidateSet, FieldSpec


@dataclass(frozen=True, slots=True)
class SelectorTask:
    field_spec: FieldSpec
    candidate_set: CandidateSet


@dataclass(frozen=True, slots=True)
class BatchSelectorCallPlan:
    tasks: tuple[SelectorTask, ...]
    estimated_prompt_chars: int


@dataclass(frozen=True, slots=True)
class ShardedSelectorTaskPlan:
    task: SelectorTask
    shards: tuple[BatchSelectorCallPlan, ...]
    original_estimated_prompt_chars: int


@dataclass(frozen=True, slots=True)
class DocumentWindow:
    index: int
    count: int
    start_char: int
    end_char: int
    text: str
    estimated_prompt_chars: int


@dataclass(frozen=True, slots=True)
class DocumentWindowSelectorTaskPlan:
    task: SelectorTask
    windows: tuple[DocumentWindow, ...]
    original_estimated_prompt_chars: int
    reducer_policy: object


type SelectorPlan = (
    BatchSelectorCallPlan | ShardedSelectorTaskPlan | DocumentWindowSelectorTaskPlan
)
type PromptEstimator = Callable[[tuple[SelectorTask, ...]], int]


class BudgetedBatchSelectorPlanner:
    """Pack selector work into prompt-budgeted provider calls."""

    def __init__(self, *, max_prompt_chars: int) -> None:
        self.max_prompt_chars = max_prompt_chars

    def plan(
        self,
        *,
        tasks: tuple[SelectorTask, ...],
        estimate_prompt_chars: PromptEstimator,
    ) -> tuple[SelectorPlan, ...]:
        plans: list[SelectorPlan] = []
        current_tasks: list[SelectorTask] = []
        current_estimate = 0

        for task in tasks:
            single_estimate = estimate_prompt_chars((task,))
            if single_estimate > self.max_prompt_chars:
                if current_tasks:
                    plans.append(
                        BatchSelectorCallPlan(
                            tasks=tuple(current_tasks),
                            estimated_prompt_chars=current_estimate,
                        ),
                    )
                    current_tasks = []
                    current_estimate = 0
                plans.append(
                    ShardedSelectorTaskPlan(
                        task=task,
                        shards=self.plan_candidate_shards(
                            task=task,
                            estimate_prompt_chars=estimate_prompt_chars,
                        ),
                        original_estimated_prompt_chars=single_estimate,
                    ),
                )
                continue

            if not current_tasks:
                current_tasks = [task]
                current_estimate = single_estimate
                continue

            # Conservative and cheap: each task is rendered once, then
            # packed by additive single-task estimates. This may split
            # slightly more often than exact combined rendering, but it
            # avoids repeatedly materializing very large prompt payloads
            # during preflight planning.
            combined_estimate = current_estimate + single_estimate
            if combined_estimate <= self.max_prompt_chars:
                current_tasks.append(task)
                current_estimate = combined_estimate
                continue

            plans.append(
                BatchSelectorCallPlan(
                    tasks=tuple(current_tasks),
                    estimated_prompt_chars=current_estimate,
                ),
            )
            current_tasks = [task]
            current_estimate = single_estimate

        if current_tasks:
            plans.append(
                BatchSelectorCallPlan(
                    tasks=tuple(current_tasks),
                    estimated_prompt_chars=current_estimate,
                ),
            )
        return tuple(plans)

    def plan_candidate_shards(
        self,
        *,
        task: SelectorTask,
        estimate_prompt_chars: PromptEstimator,
    ) -> tuple[BatchSelectorCallPlan, ...]:
        shards: list[BatchSelectorCallPlan] = []
        current_candidates: list[Candidate] = []
        current_estimate = 0

        for candidate in task.candidate_set.candidates:
            single_candidate_task = SelectorTask(
                field_spec=task.field_spec,
                candidate_set=candidate_set_view(task.candidate_set, (candidate,)),
            )
            single_estimate = estimate_prompt_chars((single_candidate_task,))
            if single_estimate > self.max_prompt_chars:
                raise InfrastructureError(
                    "selector_prompt_candidate_budget_exceeded: "
                    f"field_id={task.field_spec.field_id!r} "
                    f"candidate_id={candidate.candidate_id!r} "
                    f"estimated_prompt_chars={single_estimate} "
                    f"max_prompt_chars={self.max_prompt_chars}",
                )

            if not current_candidates:
                current_candidates = [candidate]
                current_estimate = single_estimate
                continue

            combined_estimate = current_estimate + single_estimate
            if combined_estimate <= self.max_prompt_chars:
                current_candidates.append(candidate)
                current_estimate = combined_estimate
                continue

            shards.append(
                BatchSelectorCallPlan(
                    tasks=(
                        SelectorTask(
                            field_spec=task.field_spec,
                            candidate_set=candidate_set_view(
                                task.candidate_set,
                                tuple(current_candidates),
                            ),
                        ),
                    ),
                    estimated_prompt_chars=current_estimate,
                ),
            )
            current_candidates = [candidate]
            current_estimate = single_estimate

        if current_candidates:
            shards.append(
                BatchSelectorCallPlan(
                    tasks=(
                        SelectorTask(
                            field_spec=task.field_spec,
                            candidate_set=candidate_set_view(
                                task.candidate_set,
                                tuple(current_candidates),
                            ),
                        ),
                    ),
                    estimated_prompt_chars=current_estimate,
                ),
            )
        return tuple(shards)


def candidate_set_view(
    candidate_set: CandidateSet,
    candidates: tuple[Candidate, ...],
) -> CandidateSet:
    return CandidateSet(
        field_id=candidate_set.field_id,
        document_id=candidate_set.document_id,
        instance_hint=candidate_set.instance_hint,
        candidates=candidates,
        strategy_id=candidate_set.strategy_id,
    )
