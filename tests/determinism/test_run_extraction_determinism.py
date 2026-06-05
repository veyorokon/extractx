"""determinism proof for the M8 phase-1 vertical slice.

per the brief's "Focused proof — determinism": same `(document, spec,
runtime, policy)` yields a byte-identical `Extraction` and a
byte-identical `ExecutionTrace.trace_id` across runs.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel

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


@pytest.mark.asyncio
async def test_extraction_result_is_byte_identical_across_runs() -> None:
    spec = ExtractionSpec.from_pydantic(_Phone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    document = "Call us at 555-1234."

    result_a = await run_extraction(document, spec, runtime, policy)
    result_b = await run_extraction(document, spec, runtime, policy)
    result_c = await run_extraction(document, spec, runtime, policy)

    a_dump = result_a.model_dump(mode="json")
    b_dump = result_b.model_dump(mode="json")
    c_dump = result_c.model_dump(mode="json")

    assert a_dump == b_dump == c_dump
    assert result_a.trace.trace_id == result_b.trace.trace_id == result_c.trace.trace_id


@pytest.mark.asyncio
async def test_trace_id_is_independent_of_runtime_and_policy_identity() -> None:
    """trace_id is composed from `(document_id, spec.version, "serial",
    "independent")` — re-constructing fresh `Runtime` / `ExecutorPolicy`
    objects yields the same trace_id.
    """

    spec = ExtractionSpec.from_pydantic(_Phone)
    document = "Call us at 555-1234."

    result_a = await run_extraction(
        document=document,
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )
    result_b = await run_extraction(
        document=document,
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result_a.trace.trace_id == result_b.trace.trace_id
