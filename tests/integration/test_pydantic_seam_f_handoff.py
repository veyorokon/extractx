"""prove the pydantic-backed seam-F handoff is executor-owned.

scope:

- a `from_pydantic`-built spec carrying a pydantic `field_validator`
  reaches seam F via the executor-owned `schema_cls` lookup.
- the executor does **not** resolve the schema class from
  `ExtractionSpec.source_schema_ref`.
- a `field_validator` rejection becomes a typed `NegativeOutcome`
  routed through the `ExecutorPolicy.on_validation_failure="fail"`
  policy.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel, field_validator

from extractx import (
    ExecutorPolicy,
    ExtractionSpec,
    Runtime,
    ValueKind,
    extract_field,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import StrategyBinding
from extractx.core.outcomes import NegativeOutcome


class _UpperPhone(BaseModel):
    """phone field whose pydantic `field_validator` upper-cases the
    coerced value.

    the validator is a *post-coercion* transform — it does not parse
    raw text (which would be rejected at spec load by
    `detect_pydantic_as_extractor`).
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

    @field_validator("phone")
    @classmethod
    def _tag(cls, value: str) -> str:
        # post-coercion transform — the value here is already a plain
        # `str` produced by pydantic's coercion of the regex match.
        return f"NORMALIZED:{value}"


@pytest.mark.asyncio
async def test_pydantic_field_validator_runs_via_seam_f() -> None:
    spec = ExtractionSpec.from_pydantic(_UpperPhone)
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
    proposal = result.instances[0].evidence[0]
    # the post-coercion transform fired → seam F received the live
    # schema class via the executor-owned schema_cls handoff.
    assert proposal.normalized_value == "NORMALIZED:555-1234"


@pytest.mark.asyncio
async def test_executor_does_not_resolve_from_source_schema_ref() -> None:
    """proof by construction: surfacing the schema_cls handoff ignores
    `source_schema_ref`. we substitute a benign string into
    `source_schema_ref.ref` after the spec is built (frozen
    pydantic + `model_copy`) and confirm the run still succeeds —
    the executor reads the registered live class by `spec.version`,
    never by parsing the ref string.
    """

    spec = ExtractionSpec.from_pydantic(_UpperPhone)
    # mutate the ref string but keep `spec.version` (which is the
    # registry key) intact. if the executor were resolving via
    # `source_schema_ref.ref`, the run would fail with an import or
    # attribute error.
    fake_ref = spec.source_schema_ref.model_copy(update={"ref": "no.such.module:NonExistent"})
    spec_with_fake_ref = spec.model_copy(update={"source_schema_ref": fake_ref})

    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec_with_fake_ref,
        runtime=runtime,
        policy=policy,
    )
    assert result.outcome == "complete"
    proposal = result.instances[0].evidence[0]
    assert proposal.normalized_value == "NORMALIZED:555-1234"


class _RejectingPhone(BaseModel):
    """phone field whose `field_validator` always rejects.

    seam F's layer-2 path translates the `ValueError` into a
    `ValidationFailure(layer="field", ...)`; the strategy escalates it
    under `ExecutorPolicy.on_validation_failure="fail"` to a typed
    `NegativeOutcome("validation", "field_failure", ...)`.
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

    @field_validator("phone")
    @classmethod
    def _reject(cls, value: str) -> str:
        del value
        raise ValueError("disallowed phone")


@pytest.mark.asyncio
async def test_validation_failure_escalates_to_negative_outcome() -> None:
    spec = ExtractionSpec.from_pydantic(_RejectingPhone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    # the field had no successful proposals; resolver returns ().
    assert result.outcome == "failed"
    assert result.instances == ()
    # the negative is in the trace events.
    assert len(result.trace.events) == 1
    negative = result.trace.events[0]
    assert isinstance(negative, NegativeOutcome)
    assert negative.category == "validation"
    assert negative.code == "field_failure"
    assert negative.field_id == "phone"
    assert negative.instance_key is None
    assert "disallowed phone" in negative.reason
    assert negative.candidate_count is None
