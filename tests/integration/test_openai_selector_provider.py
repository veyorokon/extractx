"""opt-in OpenAI provider proof for the pydantic-ai selector path."""

from __future__ import annotations

import os
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
from extractx.core.objects import SelectorBinding, StrategyBinding
from extractx.extras.pydantic_ai import PydanticAIOpenAIProvider, PydanticAISelector

pytestmark = pytest.mark.skipif(
    os.environ.get("EXTRACTX_RUN_OPENAI_TESTS") != "1" or not os.environ.get("OPENAI_API_KEY"),
    reason=(
        "live OpenAI selector test is opt-in: set EXTRACTX_RUN_OPENAI_TESTS=1 and OPENAI_API_KEY"
    ),
)


class _SupportPhone(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="the customer support phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
        selector_binding=SelectorBinding(
            cls=PydanticAISelector,
            params={
                "model_id": os.environ.get("EXTRACTX_OPENAI_MODEL", "gpt-5.4-nano"),
            },
        ),
    )


@pytest.mark.asyncio
async def test_openai_selector_provider_runs_real_extraction() -> None:
    spec = ExtractionSpec.from_pydantic(_SupportPhone)
    runtime = Runtime(llm=PydanticAIOpenAIProvider.from_env())

    result = await run_extraction(
        document="For customer support, call 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result.outcome == "complete"
    assert result.instances[0].evidence[0].field_id == "phone"
    assert result.instances[0].evidence[0].raw_value == "555-1234"

    usage = result.usage()
    assert len(usage) == 1
    assert usage[0].operation == "selector"
    assert usage[0].field_id == "phone"
    assert usage[0].input_tokens is not None
    assert usage[0].output_tokens is not None
