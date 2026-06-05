"""opt-in OpenAI provider proof for the instance proposer path."""

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
from extractx.core.cardinality import Cardinality
from extractx.core.objects import InstanceProposerBinding, SelectorBinding, StrategyBinding
from extractx.extras.pydantic_ai import (
    LLMInstanceProposer,
    PydanticAIOpenAIProvider,
    PydanticAISelector,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("EXTRACTX_RUN_OPENAI_TESTS") != "1" or not os.environ.get("OPENAI_API_KEY"),
    reason=(
        "live OpenAI instance proposer test is opt-in: set "
        "EXTRACTX_RUN_OPENAI_TESTS=1 and OPENAI_API_KEY"
    ),
)


class _StudyDose(BaseModel):
    dose: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="dose amount for the study arm",
        cardinality=Cardinality.PER_INSTANCE,
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d+(?:\.\d+)? mg"},
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
async def test_openai_instance_proposer_runs_real_many_extraction() -> None:
    model_id = os.environ.get("EXTRACTX_OPENAI_MODEL", "gpt-5.4-nano")
    spec = ExtractionSpec.from_pydantic(
        _StudyDose,
        instance_type="StudyArm",
        instance_cardinality=Cardinality.MANY,
        instance_proposer_binding=InstanceProposerBinding(
            cls=LLMInstanceProposer,
            params={"model_id": model_id},
        ),
    )
    runtime = Runtime(llm=PydanticAIOpenAIProvider.from_env())

    result = await run_extraction(
        document="Arm A receives 20 mg daily.\nArm B receives 30 mg daily.",
        spec=spec,
        runtime=runtime,
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result.outcome == "complete"
    assert [instance.instance_id for instance in result.instances] == ["inst_0", "inst_1"]
    assert [instance.evidence[0].normalized_value for instance in result.instances] == [
        "20 mg",
        "30 mg",
    ]

    usage = result.usage()
    assert [event.operation for event in usage] == [
        "instance_proposer",
        "selector",
        "selector",
    ]
    assert all(event.input_tokens is not None for event in usage)
    assert all(event.output_tokens is not None for event in usage)
