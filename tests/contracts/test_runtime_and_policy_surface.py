"""surface tests for the M8 phase-1 tier-1 widenings.

per the brief's section 8 / architecture §10, this slice widens the
end-user surface with `Runtime` and `ExecutorPolicy`. these tests pin:

- both are constructible without arguments.
- both are exported at the top level (`from extractx import Runtime,
  ExecutorPolicy`).
- `Runtime.from_env()` returns a usable container with the documented
  defaults for the algorithmic vertical slice (no provider keys
  required).
- `TokenCountBudget` (the documented default `Budget` impl) is
  constructible and obeys the `Budget` protocol surface.
- `NullReporter` is constructible.
- `ExecutorPolicy` is frozen and rejects unsupported literals.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import extractx
from extractx import ExecutorPolicy, Runtime
from extractx.core.contracts import Budget, BudgetDecision
from extractx.core.objects import UsageEvent
from extractx.execution.budget import TokenCountBudget
from extractx.execution.reporter import NullReporter


def test_runtime_and_policy_are_top_level_exports() -> None:
    assert Runtime is extractx.Runtime
    assert ExecutorPolicy is extractx.ExecutorPolicy


def test_runtime_default_construction() -> None:
    rt = Runtime()
    assert rt.llm is None
    assert rt.nlp is None
    assert rt.fetch is None
    assert rt.prompt_recorder is None
    assert rt.deferred_provider is None
    assert isinstance(rt.budget, TokenCountBudget)
    assert isinstance(rt.reporter, NullReporter)


def test_runtime_from_env_matches_default_construction() -> None:
    """phase-1 `Runtime.from_env()` does not read provider keys; it
    matches bare `Runtime()` behavior so the algorithmic slice stays
    portable."""

    rt = Runtime.from_env()
    assert rt.llm is None
    assert rt.nlp is None
    assert rt.fetch is None
    assert rt.deferred_provider is None


def test_runtime_budget_isolated_per_instance() -> None:
    """two `Runtime()` instances must not share `Budget` state."""

    rt_a = Runtime()
    rt_b = Runtime()
    assert rt_a.budget is not rt_b.budget


def test_executor_policy_default() -> None:
    policy = ExecutorPolicy()
    assert policy.strategy == "independent"
    assert policy.execution_mode == "immediate"
    assert policy.repair is None
    assert policy.repair_enabled is False
    assert policy.capture_interview_transcripts is False
    assert policy.on_validation_failure == "fail"


def test_executor_policy_accepts_batch_strategy() -> None:
    policy = ExecutorPolicy(strategy="batch")
    assert policy.strategy == "batch"


def test_executor_policy_repair_is_orthogonal_to_strategy() -> None:
    assert ExecutorPolicy(strategy="iterative").repair_enabled is True
    assert ExecutorPolicy(strategy="iterative", repair=False).repair_enabled is False
    assert ExecutorPolicy(strategy="batch", repair=True).repair_enabled is True


def test_executor_policy_is_frozen() -> None:
    policy = ExecutorPolicy(strategy="independent")
    with pytest.raises(ValidationError):
        policy.strategy = "iterative"  # type: ignore[misc]


def test_executor_policy_rejects_unknown_literals() -> None:
    with pytest.raises(ValidationError):
        ExecutorPolicy(strategy="unknown")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ExecutorPolicy(execution_mode="online")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ExecutorPolicy(on_validation_failure="retry")  # type: ignore[arg-type]


def test_token_count_budget_protocol_compliance() -> None:
    budget: Budget = TokenCountBudget()
    decision = budget.check()
    assert isinstance(decision, BudgetDecision)
    assert decision.allowed is True
    # record a usage event with raw_usage passthrough; counters update.
    event = UsageEvent(
        producer_version="code:abc",
        model_id=None,
        input_tokens=42,
        output_tokens=7,
        finish_reason=None,
        timestamp_ns=0,
        raw_usage={"any": "blob"},
    )
    budget.record(event)
    decision = budget.check()
    assert decision.allowed is True


def test_token_count_budget_denies_when_input_exceeded() -> None:
    budget = TokenCountBudget(max_input_tokens=10)
    budget.record(
        UsageEvent(
            producer_version="code:abc",
            input_tokens=15,
            output_tokens=0,
            timestamp_ns=0,
        ),
    )
    decision = budget.check()
    assert decision.allowed is False
    assert decision.reason == "input_tokens_exceeded"


def test_token_count_budget_denies_when_output_exceeded() -> None:
    budget = TokenCountBudget(max_output_tokens=10)
    budget.record(
        UsageEvent(
            producer_version="code:abc",
            input_tokens=0,
            output_tokens=11,
            timestamp_ns=0,
        ),
    )
    decision = budget.check()
    assert decision.allowed is False
    assert decision.reason == "output_tokens_exceeded"


def test_null_reporter_constructible() -> None:
    NullReporter()
