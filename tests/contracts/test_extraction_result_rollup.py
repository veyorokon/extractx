"""contract tests for the M8 phase-1 `Extraction` outcome rollup.

phase-1 outcome semantics (per the brief):

- `complete` iff `instances != ()` and every `Instance.outcome
  == "complete"`.
- `partial` iff any instance is `partial`.
- `failed` iff `instances == ()`.

stub-honesty: `.interview()` remains a typed stub raising
`NotImplementedError`. `.usage()` is a derived operational-metadata
projection and returns the captured usage-event tuple.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel, field_validator

from extractx import (
    ExecutorPolicy,
    Extraction,
    ExtractionSpec,
    Runtime,
    ValueKind,
    extract_field,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import StrategyBinding


class _Phone(BaseModel):
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


class _PhonePlusReject(BaseModel):
    """two fields — one extracts cleanly; the other rejects.

    the surviving field validates → resolver returns one instance.
    the rejected field's escalated `NegativeOutcome` lands on the sole
    instance per the brief's attachment rule, flipping the outcome to
    `partial`.
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
    rejected: Annotated[str, ValueKind.PERSON] = extract_field(
        description="zip code",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{5}"},
                kind="candidate",
            ),
        ),
    )

    @field_validator("rejected")
    @classmethod
    def _reject(cls, value: str) -> str:
        del value
        raise ValueError("disallowed zip")


@pytest.mark.asyncio
async def test_complete_rollup_when_no_negatives() -> None:
    spec = ExtractionSpec.from_pydantic(_Phone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert result.outcome == "complete"
    assert len(result.instances) == 1
    assert result.instances[0].outcome == "complete"
    assert result.negatives() == ()


@pytest.mark.asyncio
async def test_partial_rollup_when_pre_resolver_negatives_attach() -> None:
    spec = ExtractionSpec.from_pydantic(_PhonePlusReject)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    result = await run_extraction(
        document="Call us at 555-1234. ZIP 90210.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert result.outcome == "partial"
    assert len(result.instances) == 1
    sole = result.instances[0]
    assert sole.outcome == "partial"
    # one resolved field, one attached negative.
    assert len(sole.evidence) == 1
    assert sole.evidence[0].field_id == "phone"
    assert len(sole.negative_outcomes) == 1
    negative = sole.negative_outcomes[0]
    assert negative.category == "validation"
    assert negative.code == "field_failure"
    assert negative.field_id == "rejected"


@pytest.mark.asyncio
async def test_failed_rollup_when_no_instances() -> None:
    spec = ExtractionSpec.from_pydantic(_Phone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    # document does not contain the regex pattern; resolver returns ().
    result = await run_extraction(
        document="No matches here.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert result.outcome == "failed"
    assert result.instances == ()


# ---------------------------------------------------------------------------
# stub honesty
# ---------------------------------------------------------------------------


def _build_completed_result() -> Extraction:
    """build a real `Extraction` synchronously to exercise stubs."""

    import asyncio

    spec = ExtractionSpec.from_pydantic(_Phone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    return asyncio.run(
        run_extraction(
            document="Call us at 555-1234.",
            spec=spec,
            runtime=runtime,
            policy=policy,
        ),
    )


def test_to_pydantic_materializes_completed_result() -> None:
    result = _build_completed_result()
    materialized = result.to_pydantic(_Phone)

    assert len(materialized) == 1
    assert materialized[0].phone == "555-1234"


def test_usage_returns_captured_events() -> None:
    result = _build_completed_result()
    assert result.usage() == ()


def test_interview_stub() -> None:
    result = _build_completed_result()
    with pytest.raises(NotImplementedError):
        result.interview(field_id="phone", question="why?")
