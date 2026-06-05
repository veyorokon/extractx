"""OpenAI Batch API deferred provider for pydantic-ai selector requests."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel

from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import Message, RenderedPrompt
from extractx.execution.deferred import (
    DeferredHandle,
    DeferredPending,
    DeferredResults,
    SoftCallError,
    SoftCallRequest,
    SoftCallResponse,
)

from .selector import BatchSelectorObservationResponse, SelectorObservationResponse

__all__ = ["OpenAIDeferredProvider"]

_CHAT_COMPLETIONS_ENDPOINT = "/v1/chat/completions"
_PENDING_BATCH_STATUSES = {"validating", "in_progress", "finalizing", "cancelling"}
_TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}
_SUPPORTED_STRUCTURED_OUTPUT_MODES = {None, "auto", "json_schema"}
_OUTPUT_MODEL_BY_REF: dict[str, type[BaseModel]] = {
    "extractx.pydantic_ai.selector_response.v1": SelectorObservationResponse,
    "extractx.pydantic_ai.batch_selector_response.v1": BatchSelectorObservationResponse,
}


class OpenAIDeferredProvider:
    """DeferredProvider backed by OpenAI's Batch API.

    The adapter owns only the OpenAI transport lifecycle. Extractx request ids
    are written as OpenAI `custom_id` values so result order is irrelevant.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
        endpoint: str = _CHAT_COMPLETIONS_ENDPOINT,
        completion_window: str = "24h",
        metadata: Mapping[str, str] | None = None,
        input_filename: str = "extractx-deferred-requests.jsonl",
    ) -> None:
        if endpoint != _CHAT_COMPLETIONS_ENDPOINT:
            raise InfrastructureError(
                "openai_deferred_provider.unsupported_endpoint: expected "
                f"{_CHAT_COMPLETIONS_ENDPOINT!r}; got {endpoint!r}",
            )
        if completion_window != "24h":
            raise InfrastructureError(
                "openai_deferred_provider.unsupported_completion_window: "
                "OpenAI Batch currently supports completion_window='24h'",
            )
        self._api_key = api_key
        self._base_url = base_url
        self._client: Any | None = client
        self._endpoint = endpoint
        self._completion_window = completion_window
        self._metadata = dict(metadata or {})
        self._input_filename = input_filename

    @classmethod
    def from_env(
        cls,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> OpenAIDeferredProvider:
        """Construct an adapter that lets the OpenAI SDK read env vars."""

        return cls(metadata=metadata)

    async def submit(
        self,
        requests: tuple[SoftCallRequest, ...],
    ) -> DeferredHandle:
        if not requests:
            raise InfrastructureError(
                "openai_deferred_provider.empty_submission: cannot submit zero requests",
            )
        payload = _requests_jsonl(requests, endpoint=self._endpoint)
        client = self._openai_client()
        try:
            uploaded = client.files.create(
                file=(self._input_filename, io.BytesIO(payload)),
                purpose="batch",
            )
            batch = client.batches.create(
                input_file_id=_required_str(uploaded, "id"),
                endpoint=self._endpoint,
                completion_window="24h",
                metadata=self._metadata or None,
            )
        except Exception as exc:  # pragma: no cover - live SDK path.
            raise InfrastructureError(
                f"openai_deferred_provider.submit_failed: {exc}",
            ) from exc
        return _handle_from_batch(batch, request_count=len(requests))

    async def poll(self, handle: DeferredHandle) -> DeferredPending | DeferredResults:
        client = self._openai_client()
        try:
            batch = client.batches.retrieve(handle.provider_batch_id)
        except Exception as exc:  # pragma: no cover - live SDK path.
            raise InfrastructureError(
                f"openai_deferred_provider.poll_failed: {exc}",
            ) from exc

        status = _required_str(batch, "status")
        if status in _PENDING_BATCH_STATUSES:
            return DeferredPending(handle=handle, checked_at=datetime.now(UTC))
        if status not in _TERMINAL_BATCH_STATUSES:
            raise InfrastructureError(
                "openai_deferred_provider.unknown_status: "
                f"batch {handle.provider_batch_id!r} has status {status!r}",
            )

        successful: dict[str, SoftCallResponse] = {}
        failed: dict[str, SoftCallError] = {}
        output_file_id = _optional_str(batch, "output_file_id")
        error_file_id = _optional_str(batch, "error_file_id")
        if output_file_id is not None:
            _parse_batch_file(
                _download_file_text(client, output_file_id),
                successful=successful,
                failed=failed,
            )
        if error_file_id is not None:
            _parse_batch_file(
                _download_file_text(client, error_file_id),
                successful=successful,
                failed=failed,
            )
        return DeferredResults(
            handle=handle,
            completed_at=_batch_completed_at(batch),
            successful=successful,
            failed=failed,
        )

    async def cancel(self, handle: DeferredHandle) -> None:
        client = self._openai_client()
        try:
            client.batches.cancel(handle.provider_batch_id)
        except Exception as exc:  # pragma: no cover - live SDK path.
            raise InfrastructureError(
                f"openai_deferred_provider.cancel_failed: {exc}",
            ) from exc

    def _openai_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise InfrastructureError(
                "openai_deferred_provider.missing_sdk: install the openai package",
            ) from exc
        kwargs: dict[str, str] = {}
        if self._api_key is not None:
            kwargs["api_key"] = self._api_key
        if self._base_url is not None:
            kwargs["base_url"] = self._base_url
        openai_cls = cast("Any", OpenAI)
        self._client = openai_cls(**kwargs)
        return self._client


def _requests_jsonl(
    requests: tuple[SoftCallRequest, ...],
    *,
    endpoint: str,
) -> bytes:
    return b"".join(
        (
            json.dumps(
                {
                    "custom_id": request.request_id,
                    "method": "POST",
                    "url": endpoint,
                    "body": _chat_completion_body(request),
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for request in requests
    )


def _chat_completion_body(request: SoftCallRequest) -> dict[str, Any]:
    if request.structured_output_mode not in _SUPPORTED_STRUCTURED_OUTPUT_MODES:
        raise InfrastructureError(
            "openai_deferred_provider.unsupported_structured_output_mode: "
            f"{request.structured_output_mode!r}",
        )
    output_model = _output_model_for_ref(request.output_model_ref)
    body: dict[str, Any] = {
        "model": _metadata_str(request.rendered_prompt, "model_id"),
        "messages": [_message_payload(message) for message in request.rendered_prompt.messages],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": _json_schema_name(request.output_model_ref),
                "schema": output_model.model_json_schema(),
                "strict": True,
            },
        },
    }
    temperature = request.rendered_prompt.metadata.get("temperature")
    if isinstance(temperature, int | float):
        body["temperature"] = temperature
    seed = request.rendered_prompt.metadata.get("seed")
    if isinstance(seed, int):
        body["seed"] = seed
    return body


def _parse_batch_file(
    text: str,
    *,
    successful: dict[str, SoftCallResponse],
    failed: dict[str, SoftCallError],
) -> None:
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InfrastructureError(
                "openai_deferred_provider.output_malformed: invalid JSONL line "
                f"{line_number}",
            ) from exc
        custom_id = row.get("custom_id")
        if not isinstance(custom_id, str) or not custom_id:
            raise InfrastructureError(
                "openai_deferred_provider.output_malformed: output line missing custom_id",
            )
        error = row.get("error")
        response = row.get("response")
        if error is not None:
            failed[custom_id] = _soft_call_error_from_openai_row(custom_id, error)
            continue
        if not isinstance(response, Mapping):
            failed[custom_id] = SoftCallError(
                request_id=custom_id,
                error_type="openai_missing_response",
                message="OpenAI batch output row had neither response nor error",
                raw_error={"row": row},
            )
            continue
        response_map = cast("Mapping[str, Any]", response)
        parsed_response = _soft_call_response_from_openai_row(
            custom_id,
            response_map,
        )
        if parsed_response is None:
            failed[custom_id] = _soft_call_error_from_openai_row(
                custom_id,
                {
                    "code": "openai_non_200_response",
                    "message": f"status_code={response_map.get('status_code')!r}",
                    "response": response_map,
                },
            )
            continue
        successful[custom_id] = parsed_response


def _soft_call_response_from_openai_row(
    custom_id: str,
    response: Mapping[str, Any],
) -> SoftCallResponse | None:
    status_code = response.get("status_code")
    if status_code != 200:
        return None
    body = response.get("body")
    if not isinstance(body, Mapping):
        raise InfrastructureError(
            "openai_deferred_provider.output_malformed: response body must be an object",
        )
    body = cast("Mapping[str, Any]", body)
    content = _chat_completion_content(body)
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise InfrastructureError(
            "openai_deferred_provider.output_malformed: chat completion content "
            f"for {custom_id!r} was not JSON",
        ) from exc
    if not isinstance(payload, Mapping):
        raise InfrastructureError(
            "openai_deferred_provider.output_malformed: parsed response payload "
            "must be an object",
        )
    return SoftCallResponse(
        request_id=custom_id,
        response_payload=dict(cast("Mapping[str, Any]", payload)),
        raw_usage=_mapping_or_none(body.get("usage")),
        raw_response_metadata={
            "provider": "openai",
            "endpoint": _CHAT_COMPLETIONS_ENDPOINT,
            "openai_request_id": response.get("request_id"),
            "status_code": status_code,
            "body_id": body.get("id"),
            "model": body.get("model"),
            "service_tier": body.get("service_tier"),
            "system_fingerprint": body.get("system_fingerprint"),
        },
    )


def _soft_call_error_from_openai_row(custom_id: str, error: object) -> SoftCallError:
    if isinstance(error, Mapping):
        error = cast("Mapping[str, Any]", error)
        error_type = str(error.get("code") or error.get("type") or "openai_batch_error")
        message = str(error.get("message") or error)
        raw_error = dict(error)
    else:
        error_type = "openai_batch_error"
        message = str(error)
        raw_error = {"error": error}
    return SoftCallError(
        request_id=custom_id,
        error_type=error_type,
        message=message,
        raw_error=raw_error,
    )


def _chat_completion_content(body: Mapping[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InfrastructureError(
            "openai_deferred_provider.output_malformed: chat completion missing choices",
        )
    first = cast("object", choices[0])
    if not isinstance(first, Mapping):
        raise InfrastructureError(
            "openai_deferred_provider.output_malformed: chat completion choice must be object",
        )
    first = cast("Mapping[str, Any]", first)
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise InfrastructureError(
            "openai_deferred_provider.output_malformed: chat completion message must be object",
        )
    message = cast("Mapping[str, Any]", message)
    content = message.get("content")
    if not isinstance(content, str):
        raise InfrastructureError(
            "openai_deferred_provider.output_malformed: chat completion content must be string",
        )
    return content


def _download_file_text(client: Any, file_id: str) -> str:
    content = client.files.content(file_id)
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    read = getattr(content, "read", None)
    if callable(read):
        raw = read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
    raise InfrastructureError(
        "openai_deferred_provider.output_malformed: files.content(...) did not "
        "return readable text",
    )


def _handle_from_batch(batch: object, *, request_count: int) -> DeferredHandle:
    return DeferredHandle(
        provider="openai",
        provider_batch_id=_required_str(batch, "id"),
        submitted_at=_datetime_from_timestamp(_optional_int(batch, "created_at")),
        request_count=request_count,
    )


def _batch_completed_at(batch: object) -> datetime:
    for key in ("completed_at", "cancelled_at", "expired_at", "failed_at"):
        value = _optional_int(batch, key)
        if value is not None:
            return _datetime_from_timestamp(value)
    return datetime.now(UTC)


def _output_model_for_ref(ref: str) -> type[BaseModel]:
    try:
        return _OUTPUT_MODEL_BY_REF[ref]
    except KeyError as exc:
        raise InfrastructureError(
            f"openai_deferred_provider.unknown_output_model_ref: {ref!r}",
        ) from exc


def _metadata_str(rendered: RenderedPrompt, key: str) -> str:
    value = rendered.metadata.get(key)
    if not isinstance(value, str) or not value:
        raise InfrastructureError(
            f"openai_deferred_provider.request_malformed: rendered prompt metadata missing {key!r}",
        )
    return value


def _message_payload(message: Message) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


def _json_schema_name(ref: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in ref).strip("_")[:64]


def _mapping_or_none(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(cast("Mapping[str, Any]", value))
    return None


def _required_str(obj: object, key: str) -> str:
    value = _value(obj, key)
    if not isinstance(value, str) or not value:
        raise InfrastructureError(
            f"openai_deferred_provider.response_malformed: missing {key!r}",
        )
    return value


def _optional_str(obj: object, key: str) -> str | None:
    value = _value(obj, key)
    return value if isinstance(value, str) and value else None


def _optional_int(obj: object, key: str) -> int | None:
    value = _value(obj, key)
    return value if isinstance(value, int) else None


def _value(obj: object, key: str) -> object:
    if isinstance(obj, Mapping):
        return cast("Mapping[str, object]", obj).get(key)
    return getattr(obj, key, None)


def _datetime_from_timestamp(value: int | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(value, tz=UTC)
