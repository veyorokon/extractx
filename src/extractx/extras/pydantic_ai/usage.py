"""usage projection helpers for pydantic-ai provider results."""

from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict, is_dataclass
from typing import Any, cast

from extractx.core.objects import RenderedPrompt, UsageEvent


def usage_event_from_pydantic_ai_result(
    result: Any,
    *,
    rendered: RenderedPrompt,
) -> UsageEvent | None:
    usage_fn = getattr(result, "usage", None)
    if not callable(usage_fn):
        return None
    usage = usage_fn()
    if usage is None:
        return None

    raw_usage = _mapping_from_object(usage)
    raw_response_metadata = _response_metadata_from_result(result)
    input_tokens = _int_attr(usage, "input_tokens")
    output_tokens = _int_attr(usage, "output_tokens")
    total_tokens = _int_attr(usage, "total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return UsageEvent(
        producer_version=_metadata_str(rendered, "producer_version"),
        operation=_operation_from_rendered(rendered),
        field_id=_first_metadata_tuple_str(rendered, "allowed_field_ids"),
        instance_id=_first_metadata_tuple_str(rendered, "allowed_instance_ids"),
        model_id=_metadata_str(rendered, "model_id"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        finish_reason=_optional_metadata_str(rendered, "finish_reason"),
        response_id=_optional_metadata_str(rendered, "response_id"),
        soft_call_identity=_optional_metadata_str(rendered, "soft_call_identity"),
        timestamp_ns=time.time_ns(),
        raw_usage=raw_usage,
        raw_response_metadata=raw_response_metadata,
    )


def _metadata_str(rendered: RenderedPrompt, key: str) -> str:
    value = rendered.metadata.get(key)
    return value if isinstance(value, str) else ""


def _mapping_from_object(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(cast("Mapping[str, Any]", value))
    if is_dataclass(value) and not isinstance(value, type):
        return cast("Mapping[str, Any]", asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(cast("Mapping[str, Any]", dumped))
    return {"repr": repr(value)}


def _response_metadata_from_result(result: Any) -> Mapping[str, Any] | None:
    metadata: dict[str, Any] = {"result_type": type(result).__qualname__}
    all_messages = getattr(result, "all_messages", None)
    if callable(all_messages):
        with suppress(Exception):
            messages = all_messages()
            if hasattr(messages, "__len__"):
                metadata["message_count"] = len(cast("Any", messages))
    return metadata


def _int_attr(value: Any, name: str) -> int | None:
    raw = getattr(value, name, None)
    if isinstance(raw, int):
        return raw
    return None


def _optional_metadata_str(rendered: RenderedPrompt, key: str) -> str | None:
    value = rendered.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _first_metadata_tuple_str(rendered: RenderedPrompt, key: str) -> str | None:
    value = rendered.metadata.get(key)
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        return value[0]
    return None


def _operation_from_rendered(rendered: RenderedPrompt) -> str | None:
    template_id = rendered.metadata.get("prompt_template_id")
    if template_id == "extractx.instances.proposer.v1":
        return "instance_proposer"
    if isinstance(template_id, str) and template_id.startswith("extractx.selection."):
        return "selector"
    return None
