"""integration proof for the bounded iterative object-repair slice."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

import pytest
from pydantic import BaseModel, field_validator

from extractx import (
    ExecutorPolicy,
    ExtractionSpec,
    FieldRef,
    ObjectIssue,
    Runtime,
    ValueKind,
    extract_field,
    extractx_object_validator,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import (
    CandidateSet,
    ContextPack,
    FieldSpec,
    Observation,
    SelectorBinding,
    StrategyBinding,
)
from extractx.core.versions import stable_hash


class _RepairingDateSelector:
    """test selector that changes one field only after retry feedback."""

    calls: ClassVar[list[tuple[str, tuple[str, ...]]]] = []

    @property
    def producer_version(self) -> str:
        return f"test:{stable_hash(self.__class__.__qualname__)}"

    def select(
        self,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        context_pack: ContextPack,
        instance_state: object | None = None,
        *,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> Observation:
        del instance_state
        self.calls.append((field_spec.field_id, context_pack.retry_feedback))
        if field_spec.field_id == "start_date":
            target = "2026-05-10"
        elif context_pack.retry_feedback:
            target = "2026-05-20"
        else:
            target = "2026-05-01"

        selected = next(
            candidate for candidate in candidate_set.candidates if candidate.text == target
        )
        return Observation(
            instance_id=instance_ids[0],
            field_id=field_spec.field_id,
            evidence_id=selected.candidate_id,
            abstain=False,
            outcome="SELECTED",
            selected_candidate_ids=(selected.candidate_id,),
            reason="test selector selected bounded id",
            producer_version=self.producer_version,
        )


def _date_field(description: str) -> Any:
    return extract_field(
        description=description,
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{4}-\d{2}-\d{2}"},
                kind="candidate",
            ),
        ),
        selector_binding=SelectorBinding(cls=_RepairingDateSelector),
    )


class _ScheduledEvent(BaseModel):
    start_date: Annotated[str, ValueKind.DATE] = _date_field("event start date")
    end_date: Annotated[str, ValueKind.DATE] = _date_field("event end date")

    @staticmethod
    @extractx_object_validator(implicates=("end_date",))
    def _end_on_or_after_start(
        values: dict[str, Any],
        evidence: dict[str, Any],
    ) -> ObjectIssue | None:
        del evidence
        if values["end_date"] < values["start_date"]:
            return ObjectIssue(
                code="date_order",
                reason="end_date must be on or after start_date",
                implicates=(FieldRef(field_id="end_date"),),
            )
        return None


class _RepairingCodeSelector:
    calls: ClassVar[list[tuple[str, tuple[str, ...]]]] = []

    @property
    def producer_version(self) -> str:
        return f"test:{stable_hash(self.__class__.__qualname__)}"

    def select(
        self,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        context_pack: ContextPack,
        instance_state: object | None = None,
        *,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> Observation:
        del instance_state
        self.calls.append((field_spec.field_id, context_pack.retry_feedback))
        target = "OK-42" if context_pack.retry_feedback else "BAD-42"
        selected = next(
            candidate for candidate in candidate_set.candidates if candidate.text == target
        )
        return Observation(
            instance_id=instance_ids[0],
            field_id=field_spec.field_id,
            evidence_id=selected.candidate_id,
            abstain=False,
            outcome="SELECTED",
            selected_candidate_ids=(selected.candidate_id,),
            reason="test selector selected bounded id",
            producer_version=self.producer_version,
        )


class _RepairingDateBatchSelector:
    """Batch selector that supports both initial batch and single-field repair."""

    calls: ClassVar[list[tuple[tuple[str, ...], tuple[str, ...]]]] = []

    @property
    def producer_version(self) -> str:
        return f"test:{stable_hash(self.__class__.__qualname__)}"

    def select_many(
        self,
        *,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
        context_pack: ContextPack,
        instance_state: object | None = None,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> tuple[Observation, ...]:
        del spec, instance_state
        self.calls.append(
            (
                tuple(candidate_set.field_id for candidate_set in candidate_sets),
                context_pack.retry_feedback,
            ),
        )
        observations: list[Observation] = []
        for candidate_set in candidate_sets:
            if candidate_set.field_id == "start_date":
                target = "2026-05-10"
            elif context_pack.retry_feedback:
                target = "2026-05-20"
            else:
                target = "2026-05-01"
            selected = next(
                candidate for candidate in candidate_set.candidates if candidate.text == target
            )
            observations.append(
                Observation(
                    instance_id=instance_ids[0],
                    field_id=candidate_set.field_id,
                    evidence_id=selected.candidate_id,
                    abstain=False,
                    outcome="SELECTED",
                    selected_candidate_ids=(selected.candidate_id,),
                    reason="test batch selector selected bounded id",
                    producer_version=self.producer_version,
                ),
            )
        return tuple(observations)


def _batch_date_field(description: str) -> Any:
    return extract_field(
        description=description,
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{4}-\d{2}-\d{2}"},
                kind="candidate",
            ),
        ),
        selector_binding=SelectorBinding(cls=_RepairingDateBatchSelector),
    )


class _ScheduledEventBatch(BaseModel):
    start_date: Annotated[str, ValueKind.DATE] = _batch_date_field("event start date")
    end_date: Annotated[str, ValueKind.DATE] = _batch_date_field("event end date")

    @staticmethod
    @extractx_object_validator(implicates=("end_date",))
    def _end_on_or_after_start(
        values: dict[str, Any],
        evidence: dict[str, Any],
    ) -> ObjectIssue | None:
        del evidence
        if values["end_date"] < values["start_date"]:
            return ObjectIssue(
                code="date_order",
                reason="end_date must be on or after start_date",
                implicates=(FieldRef(field_id="end_date"),),
            )
        return None


class _ValidatedTicket(BaseModel):
    ticket_code: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="approved ticket code",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"(?:BAD|OK)-\d+"},
                kind="candidate",
            ),
        ),
        selector_binding=SelectorBinding(cls=_RepairingCodeSelector),
    )

    @field_validator("ticket_code")
    @classmethod
    def _must_be_ok(cls, value: str) -> str:
        if not value.startswith("OK-"):
            raise ValueError("ticket_code must start with OK-")
        return value


@pytest.mark.asyncio
async def test_independent_strategy_does_not_repair_object_issue() -> None:
    _RepairingDateSelector.calls.clear()
    spec = ExtractionSpec.from_pydantic(_ScheduledEvent)

    result = await run_extraction(
        document="Candidate dates: 2026-05-10, 2026-05-01, 2026-05-20.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result.strategy == "independent"
    assert result.outcome == "partial"
    assert _RepairingDateSelector.calls == [
        ("start_date", ()),
        ("end_date", ()),
    ]
    negative = result.instances[0].negative_outcomes[0]
    assert negative.code == "instance_failure"
    assert negative.object_issues[0].implicates == (FieldRef(field_id="end_date"),)


@pytest.mark.asyncio
async def test_iterative_strategy_retries_implicated_field_with_feedback() -> None:
    _RepairingDateSelector.calls.clear()
    spec = ExtractionSpec.from_pydantic(_ScheduledEvent)

    result = await run_extraction(
        document="Candidate dates: 2026-05-10, 2026-05-01, 2026-05-20.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="iterative"),
    )

    assert result.strategy == "iterative"
    assert result.outcome == "complete"
    assert result.instances[0].negative_outcomes == ()
    values = {
        evidence.field_id: evidence.normalized_value for evidence in result.instances[0].evidence
    }
    assert values == {
        "start_date": "2026-05-10",
        "end_date": "2026-05-20",
    }
    assert _RepairingDateSelector.calls == [
        ("start_date", ()),
        ("end_date", ()),
        ("end_date", ("end_date must be on or after start_date",)),
    ]


@pytest.mark.asyncio
async def test_iterative_strategy_retries_pydantic_field_failure() -> None:
    _RepairingCodeSelector.calls.clear()
    spec = ExtractionSpec.from_pydantic(_ValidatedTicket)

    result = await run_extraction(
        document="Candidates: BAD-42 and OK-42.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="iterative"),
    )

    assert result.strategy == "iterative"
    assert result.outcome == "complete"
    assert result.instances[0].negative_outcomes == ()
    assert result.instances[0].evidence[0].normalized_value == "OK-42"
    assert _RepairingCodeSelector.calls[0] == ("ticket_code", ())
    assert _RepairingCodeSelector.calls[1][0] == "ticket_code"
    assert "ticket_code must start with OK-" in _RepairingCodeSelector.calls[1][1][0]


@pytest.mark.asyncio
async def test_batch_strategy_with_repair_retries_implicated_field_with_batch_selector() -> None:
    _RepairingDateBatchSelector.calls.clear()
    spec = ExtractionSpec.from_pydantic(_ScheduledEventBatch)

    result = await run_extraction(
        document="Candidate dates: 2026-05-10, 2026-05-01, 2026-05-20.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="batch", repair=True),
    )

    assert result.strategy == "batch"
    assert result.outcome == "complete"
    assert result.instances[0].negative_outcomes == ()
    values = {
        evidence.field_id: evidence.normalized_value for evidence in result.instances[0].evidence
    }
    assert values == {
        "start_date": "2026-05-10",
        "end_date": "2026-05-20",
    }
    assert _RepairingDateBatchSelector.calls == [
        (("start_date", "end_date"), ()),
        (("end_date",), ("end_date must be on or after start_date",)),
    ]
