"""focused tests for the ADR-0009 pydantic-ai instance proposer."""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import BaseModel

from extractx import ExtractionSpec, ValueKind, extract_field
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.anchors import SourceRef
from extractx.core.cardinality import Cardinality
from extractx.core.objects import (
    InstanceCandidate,
    InstanceCandidateSet,
    InstanceProposerBinding,
    ProviderResult,
    RenderedPrompt,
    StrategyBinding,
    UsageEvent,
)
from extractx.extras.pydantic_ai import (
    InstanceProposalResponse,
    LLMInstanceProposer,
)
from extractx.instances.proposer import InstanceProposerContractError
from extractx.source.adapters.text import TextAdapter


class _StudyArm(BaseModel):
    dose: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="dose amount",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d+(?:\.\d+)? mg"},
                kind="candidate",
            ),
        ),
    )


class _FakeProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[RenderedPrompt] = []

    def __call__(
        self,
        rendered: RenderedPrompt,
        output_type: type[InstanceProposalResponse],
    ) -> InstanceProposalResponse:
        self.calls.append(rendered)
        return output_type.model_validate(
            {
                "selected_instance_ids": self.payload["selected_instance_ids"],
                "reason": self.payload.get("reason"),
            },
        )


def _document_view():
    return TextAdapter().adapt(
        b"Arm A receives 20 mg daily.\nArm B receives 30 mg daily.",
        SourceRef(source_id="doc-1", content_hash="hash-doc-1"),
    )


def _spec() -> ExtractionSpec:
    return ExtractionSpec.from_pydantic(
        _StudyArm,
        instance_type="StudyArm",
        instance_cardinality=Cardinality.MANY,
        instance_proposer_binding=InstanceProposerBinding(
            cls=LLMInstanceProposer,
            params={"model_id": "fake:model"},
        ),
    )


def _candidate_set() -> InstanceCandidateSet:
    return InstanceCandidateSet(
        document_id="doc-1",
        instance_type="StudyArm",
        candidates=(
            InstanceCandidate(
                instance_id="inst_0",
                instance_type="StudyArm",
                context="Arm A receives 20 mg daily.",
            ),
            InstanceCandidate(
                instance_id="inst_1",
                instance_type="StudyArm",
                context="Arm B receives 30 mg daily.",
            ),
        ),
    )


def test_fake_provider_selects_bounded_instance_ids() -> None:
    provider = _FakeProvider(
        {
            "selected_instance_ids": ["inst_0", "inst_1"],
            "reason": "two bounded instances",
        },
    )
    proposer = LLMInstanceProposer(model_id="fake:model", provider=provider)

    response = proposer.propose(_document_view(), _spec(), _candidate_set())

    assert response.selected_instance_ids == ("inst_0", "inst_1")
    rendered = provider.calls[0]
    assert rendered.metadata["allowed_instance_ids"] == ("inst_0", "inst_1")
    assert rendered.metadata["instance_candidate_set_hash"]
    assert rendered.metadata["soft_call_identity"]
    assert rendered.metadata["temperature"] == 0
    assert rendered.metadata["seed"] == 0
    assert "selected_instance_ids" in rendered.structured_output_schema["properties"]


def test_provider_result_usage_event_is_captured_by_instance_proposer() -> None:
    usage = UsageEvent(
        producer_version="soft:test",
        operation="instance_proposer",
        model_id="fake:model",
        input_tokens=17,
        output_tokens=5,
        total_tokens=22,
        timestamp_ns=456,
        raw_usage={"input_tokens": 17, "output_tokens": 5},
    )

    def provider(
        rendered: RenderedPrompt,
        output_type: type[InstanceProposalResponse],
    ) -> ProviderResult[InstanceProposalResponse]:
        return ProviderResult(
            output=output_type.model_validate(
                {
                    "selected_instance_ids": rendered.metadata["allowed_instance_ids"],
                    "reason": "select all",
                },
            ),
            usage_event=usage,
        )

    proposer = LLMInstanceProposer(model_id="fake:model", provider=provider)

    proposer.propose(_document_view(), _spec(), _candidate_set())

    assert proposer.last_usage_event == usage


@pytest.mark.parametrize(
    "payload, match",
    [
        ({"selected_instance_ids": ["missing"]}, "outside candidate set"),
        ({"selected_instance_ids": ["inst_0", "inst_0"]}, "duplicate"),
        ({"selected_instance_ids": []}, "insufficient"),
    ],
)
def test_invalid_outputs_fail_loudly(payload: dict[str, Any], match: str) -> None:
    proposer = LLMInstanceProposer(
        model_id="fake:model",
        provider=_FakeProvider(payload),
    )

    with pytest.raises(InstanceProposerContractError, match=match):
        proposer.propose(_document_view(), _spec(), _candidate_set())
