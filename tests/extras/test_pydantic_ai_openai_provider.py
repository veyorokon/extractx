"""contract tests for the pydantic-ai OpenAI provider adapter."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import Message, RenderedPrompt, UsageEvent
from extractx.extras.pydantic_ai.openai import (
    PydanticAIOpenAIProvider,
    StructuredOutputMode,
    _agent_output_type,
    _with_structured_output_metadata,
)


class _Output(BaseModel):
    value: str


def test_openai_provider_defaults_to_auto_structured_output_mode() -> None:
    provider = PydanticAIOpenAIProvider.from_env()

    assert provider.structured_output_mode == StructuredOutputMode.AUTO


def test_openai_provider_normalizes_structured_output_mode_from_string() -> None:
    provider = PydanticAIOpenAIProvider(structured_output_mode="tool_call")

    assert provider.structured_output_mode == StructuredOutputMode.TOOL_CALL


def test_openai_provider_rejects_unknown_structured_output_mode() -> None:
    with pytest.raises(ValueError, match="unsupported structured_output_mode"):
        PydanticAIOpenAIProvider(structured_output_mode="xml")


def test_openai_provider_fails_loudly_for_declared_unimplemented_mode() -> None:
    provider = PydanticAIOpenAIProvider(structured_output_mode=StructuredOutputMode.JSON_OBJECT)
    rendered = RenderedPrompt(
        messages=(Message(role="user", content="return a value"),),
        metadata={"model_id": "test-model", "producer_version": "test"},
    )

    with pytest.raises(InfrastructureError, match="json_object"):
        provider(rendered, _Output)


def test_openai_provider_maps_explicit_modes_to_pydantic_ai_output_wrappers() -> None:
    class _ToolOutput:
        def __init__(self, output_type: type[BaseModel]) -> None:
            self.output_type = output_type

    class _NativeOutput:
        def __init__(self, output_type: type[BaseModel]) -> None:
            self.output_type = output_type

    class _PromptedOutput:
        def __init__(self, output_type: type[BaseModel]) -> None:
            self.output_type = output_type

    assert (
        _agent_output_type(
            _Output,
            mode=StructuredOutputMode.AUTO,
            tool_output_cls=_ToolOutput,
            native_output_cls=_NativeOutput,
            prompted_output_cls=_PromptedOutput,
        )
        is _Output
    )
    assert isinstance(
        _agent_output_type(
            _Output,
            mode=StructuredOutputMode.TOOL_CALL,
            tool_output_cls=_ToolOutput,
            native_output_cls=_NativeOutput,
            prompted_output_cls=_PromptedOutput,
        ),
        _ToolOutput,
    )
    assert isinstance(
        _agent_output_type(
            _Output,
            mode=StructuredOutputMode.JSON_SCHEMA,
            tool_output_cls=_ToolOutput,
            native_output_cls=_NativeOutput,
            prompted_output_cls=_PromptedOutput,
        ),
        _NativeOutput,
    )
    assert isinstance(
        _agent_output_type(
            _Output,
            mode=StructuredOutputMode.PROMPTED_JSON,
            tool_output_cls=_ToolOutput,
            native_output_cls=_NativeOutput,
            prompted_output_cls=_PromptedOutput,
        ),
        _PromptedOutput,
    )


def test_openai_provider_structured_output_mode_metadata_is_passthrough() -> None:
    usage_event = UsageEvent(producer_version="test", timestamp_ns=1)

    updated = _with_structured_output_metadata(
        usage_event,
        mode=StructuredOutputMode.TOOL_CALL,
        endpoint="responses",
        output_type=_Output,
    )

    assert updated is not None
    assert updated.raw_response_metadata == {
        "endpoint": "responses",
        "output_model": "_Output",
        "provider": "openai",
        "structured_output_mode": "tool_call",
    }
    assert usage_event.raw_response_metadata is None
