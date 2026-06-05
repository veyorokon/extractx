"""OpenAI runtime provider for the pydantic-ai selector extra."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar, cast

from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import ProviderResult, RenderedPrompt, UsageEvent
from extractx.extras.pydantic_ai.usage import usage_event_from_pydantic_ai_result

__all__ = ["PydanticAIOpenAIProvider", "StructuredOutputMode"]

T = TypeVar("T")


class StructuredOutputMode(StrEnum):
    """Provider structured-output transport mode.

    `AUTO` keeps the current pydantic-ai default. Explicit modes select the
    corresponding pydantic-ai structured-output wrapper when the wrapper exists.
    """

    AUTO = "auto"
    TOOL_CALL = "tool_call"
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPTED_JSON = "prompted_json"


@dataclass(frozen=True, slots=True)
class PydanticAIOpenAIProvider:
    """Callable `Runtime.llm` provider backed by pydantic-ai + OpenAI.

    `PydanticAISelector` owns the selector contract and passes a rendered
    prompt plus expected output model here. This provider owns only the
    transport binding: model construction, OpenAI provider construction,
    and pydantic-ai invocation.
    """

    api_key: str | None = None
    base_url: str | None = None
    endpoint: str = "responses"
    structured_output_mode: StructuredOutputMode | str = StructuredOutputMode.AUTO

    @classmethod
    def from_env(
        cls,
        *,
        endpoint: str = "responses",
        structured_output_mode: StructuredOutputMode | str = StructuredOutputMode.AUTO,
    ) -> PydanticAIOpenAIProvider:
        """Construct a provider that lets OpenAI's client read env vars."""

        return cls(endpoint=endpoint, structured_output_mode=structured_output_mode)

    def __post_init__(self) -> None:
        try:
            mode = StructuredOutputMode(self.structured_output_mode)
        except ValueError as exc:
            allowed = ", ".join(mode.value for mode in StructuredOutputMode)
            raise ValueError(
                "unsupported structured_output_mode "
                f"{self.structured_output_mode!r}; expected one of: {allowed}",
            ) from exc
        object.__setattr__(self, "structured_output_mode", mode)

    def __call__(
        self,
        rendered: RenderedPrompt,
        output_type: type[T],
    ) -> T | ProviderResult[T]:
        mode = cast("StructuredOutputMode", self.structured_output_mode)
        _ensure_supported_mode(mode)

        try:
            from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput
            from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
            from pydantic_ai.providers.openai import OpenAIProvider
            from pydantic_ai.settings import ModelSettings
        except ImportError as exc:
            raise InfrastructureError(
                "selector.missing_llm: pydantic-ai OpenAI support is not installed; "
                "install extractx[pydantic_ai]",
            ) from exc

        model_id = _metadata_str(rendered, "model_id")
        provider = OpenAIProvider(api_key=self.api_key, base_url=self.base_url)
        if self.endpoint == "responses":
            model: Any = OpenAIResponsesModel(model_id, provider=provider)
        elif self.endpoint == "chat":
            model = OpenAIChatModel(model_id, provider=provider)
        else:
            raise InfrastructureError(
                "selector.provider_unavailable: unsupported OpenAI endpoint "
                f"{self.endpoint!r}; expected 'responses' or 'chat'",
            )

        system_prompt, user_prompt = _split_rendered_messages(rendered)
        settings: dict[str, Any] = {}
        temperature = rendered.metadata.get("temperature")
        if isinstance(temperature, int | float):
            settings["temperature"] = temperature
        seed = rendered.metadata.get("seed")
        if isinstance(seed, int):
            settings["seed"] = seed

        try:
            agent_cls = cast("Any", Agent)
            agent = agent_cls(
                model,
                output_type=_agent_output_type(
                    output_type,
                    mode=mode,
                    tool_output_cls=ToolOutput,
                    native_output_cls=NativeOutput,
                    prompted_output_cls=PromptedOutput,
                ),
                system_prompt=system_prompt,
                model_settings=ModelSettings(**settings),
            )
            result = _run_agent_sync(agent, user_prompt)
        except Exception as exc:  # pragma: no cover - exercised by opt-in live test.
            raise InfrastructureError(
                f"selector.provider_unavailable: OpenAI selector call failed: {exc}",
            ) from exc

        usage_event = usage_event_from_pydantic_ai_result(result, rendered=rendered)
        usage_event = _with_structured_output_metadata(
            usage_event,
            mode=mode,
            endpoint=self.endpoint,
            output_type=output_type,
        )
        return ProviderResult(output=result.output, usage_event=usage_event)


def _metadata_str(rendered: RenderedPrompt, key: str) -> str:
    value = rendered.metadata.get(key)
    if not isinstance(value, str) or not value:
        raise InfrastructureError(
            f"selector.provider_unavailable: rendered prompt metadata missing {key!r}",
        )
    return value


def _split_rendered_messages(rendered: RenderedPrompt) -> tuple[str, str]:
    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in rendered.messages:
        if message.role == "system":
            system_parts.append(message.content)
        else:
            user_parts.append(message.content)
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


def _run_agent_sync(agent: Any, user_prompt: str) -> Any:
    """Run pydantic-ai sync API from sync or async caller contexts."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return agent.run_sync(user_prompt)

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def target() -> None:
        try:
            result_box["result"] = agent.run_sync(user_prompt)
        except BaseException as exc:  # pragma: no cover - passthrough path.
            error_box["error"] = exc

    thread = threading.Thread(target=target, name="extractx-openai-selector", daemon=True)
    thread.start()
    thread.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box["result"]


def _ensure_supported_mode(mode: StructuredOutputMode) -> None:
    if mode in {
        StructuredOutputMode.AUTO,
        StructuredOutputMode.TOOL_CALL,
        StructuredOutputMode.JSON_SCHEMA,
        StructuredOutputMode.PROMPTED_JSON,
    }:
        return
    raise InfrastructureError(
        "selector.provider_unavailable: structured output mode "
        f"{mode.value!r} is declared but not implemented for "
        "PydanticAIOpenAIProvider; supported modes are 'auto', 'tool_call', "
        "'json_schema', and 'prompted_json'",
    )


def _agent_output_type[OutputT](
    output_type: type[OutputT],
    *,
    mode: StructuredOutputMode,
    tool_output_cls: Any,
    native_output_cls: Any,
    prompted_output_cls: Any,
) -> Any:
    if mode == StructuredOutputMode.AUTO:
        return output_type
    if mode == StructuredOutputMode.TOOL_CALL:
        return tool_output_cls(output_type)
    if mode == StructuredOutputMode.JSON_SCHEMA:
        return native_output_cls(output_type)
    if mode == StructuredOutputMode.PROMPTED_JSON:
        return prompted_output_cls(output_type)
    _ensure_supported_mode(mode)
    return output_type


def _with_structured_output_metadata(
    usage_event: UsageEvent | None,
    *,
    mode: StructuredOutputMode,
    endpoint: str,
    output_type: type[Any],
) -> UsageEvent | None:
    if usage_event is None:
        return None
    raw_response_metadata = dict(usage_event.raw_response_metadata or {})
    raw_response_metadata.update(
        {
            "provider": "openai",
            "endpoint": endpoint,
            "structured_output_mode": mode.value,
            "output_model": output_type.__qualname__,
        },
    )
    return usage_event.model_copy(
        update={"raw_response_metadata": raw_response_metadata},
    )
