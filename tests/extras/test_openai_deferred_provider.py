"""contract tests for the OpenAI Batch API deferred provider adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import Message, RenderedPrompt
from extractx.execution.deferred import DeferredPending, DeferredResults, SoftCallRequest
from extractx.extras.pydantic_ai.openai_deferred import (
    OpenAIDeferredProvider,
    _chat_completion_body,
)


class _Content:
    def __init__(self, text: str) -> None:
        self.text = text


class _Files:
    def __init__(self) -> None:
        self.created_payload: bytes | None = None
        self.content_by_id: dict[str, str] = {}

    def create(self, *, file: tuple[str, object], purpose: str) -> SimpleNamespace:
        assert purpose == "batch"
        _, stream = file
        self.created_payload = stream.read()
        return SimpleNamespace(id="file-input")

    def content(self, file_id: str) -> _Content:
        return _Content(self.content_by_id[file_id])


class _Batches:
    def __init__(self) -> None:
        self.created: dict[str, object] | None = None
        self.batch = SimpleNamespace(
            id="batch-1",
            status="completed",
            created_at=1_715_000_000,
            completed_at=1_715_000_100,
            output_file_id="file-output",
            error_file_id=None,
        )
        self.cancelled: str | None = None

    def create(
        self,
        *,
        input_file_id: str,
        endpoint: str,
        completion_window: str,
        metadata: dict[str, str] | None,
    ) -> SimpleNamespace:
        self.created = {
            "input_file_id": input_file_id,
            "endpoint": endpoint,
            "completion_window": completion_window,
            "metadata": metadata,
        }
        return self.batch

    def retrieve(self, batch_id: str) -> SimpleNamespace:
        assert batch_id == self.batch.id
        return self.batch

    def cancel(self, batch_id: str) -> SimpleNamespace:
        self.cancelled = batch_id
        return self.batch


class _Client:
    def __init__(self) -> None:
        self.files = _Files()
        self.batches = _Batches()


def _request() -> SoftCallRequest:
    return SoftCallRequest(
        request_id="request-1",
        rendered_prompt=RenderedPrompt(
            messages=(
                Message(role="system", content="Classify."),
                Message(role="user", content="Return one observation."),
            ),
            metadata={
                "model_id": "gpt-test",
                "temperature": 0,
                "seed": 0,
            },
        ),
        output_model_ref="extractx.pydantic_ai.batch_selector_response.v1",
        soft_call_identity="soft-call-1",
        structured_output_mode="json_schema",
    )


def test_openai_deferred_provider_schema_is_strict_json_schema_compatible() -> None:
    body = _chat_completion_body(_request())
    schema = body["response_format"]["json_schema"]["schema"]

    observation = schema["$defs"]["SelectorObservationResponse"]
    assert set(observation["required"]) == set(observation["properties"])


@pytest.mark.asyncio
async def test_openai_deferred_provider_submits_jsonl_batch_requests() -> None:
    client = _Client()
    provider = OpenAIDeferredProvider(client=client, metadata={"purpose": "test"})

    handle = await provider.submit((_request(),))

    assert handle.provider == "openai"
    assert handle.provider_batch_id == "batch-1"
    assert handle.request_count == 1
    assert client.batches.created == {
        "input_file_id": "file-input",
        "endpoint": "/v1/chat/completions",
        "completion_window": "24h",
        "metadata": {"purpose": "test"},
    }
    assert client.files.created_payload is not None
    line = json.loads(client.files.created_payload.decode("utf-8"))
    assert line["custom_id"] == "request-1"
    assert line["method"] == "POST"
    assert line["url"] == "/v1/chat/completions"
    assert line["body"]["model"] == "gpt-test"
    assert line["body"]["messages"] == [
        {"role": "system", "content": "Classify."},
        {"role": "user", "content": "Return one observation."},
    ]
    assert line["body"]["response_format"]["type"] == "json_schema"
    assert line["body"]["response_format"]["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_openai_deferred_provider_polls_completed_output_file() -> None:
    client = _Client()
    client.files.content_by_id["file-output"] = (
        json.dumps(
            {
                "custom_id": "request-1",
                "response": {
                    "status_code": 200,
                    "request_id": "req-openai",
                    "body": {
                        "id": "chatcmpl-1",
                        "model": "gpt-test",
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "observations": [
                                                {
                                                    "instance_id": "inst_0",
                                                    "field_id": "f001",
                                                    "evidence_id": "f001_c001",
                                                    "selected_candidate_ids": ["f001_c001"],
                                                    "abstain": False,
                                                    "reason": "selected",
                                                },
                                            ],
                                        },
                                    ),
                                },
                            },
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 2,
                            "prompt_tokens_details": {"cached_tokens": 1},
                        },
                        "service_tier": "batch",
                        "system_fingerprint": "fp-test",
                    },
                },
                "error": None,
            },
        )
        + "\n"
    )
    provider = OpenAIDeferredProvider(client=client)
    handle = await provider.submit((_request(),))

    results = await provider.poll(handle)

    assert isinstance(results, DeferredResults)
    assert results.completed_at == datetime.fromtimestamp(1_715_000_100, tz=UTC)
    response = results.successful["request-1"]
    assert response.response_payload["observations"][0]["field_id"] == "f001"
    assert response.raw_usage == {
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "prompt_tokens_details": {"cached_tokens": 1},
    }
    assert response.raw_response_metadata is not None
    assert response.raw_response_metadata["openai_request_id"] == "req-openai"
    assert response.raw_response_metadata["model"] == "gpt-test"
    assert response.raw_response_metadata["service_tier"] == "batch"


@pytest.mark.asyncio
async def test_openai_deferred_provider_returns_pending_for_in_progress_batch() -> None:
    client = _Client()
    client.batches.batch.status = "in_progress"
    client.batches.batch.output_file_id = None
    provider = OpenAIDeferredProvider(client=client)
    handle = await provider.submit((_request(),))

    result = await provider.poll(handle)

    assert isinstance(result, DeferredPending)


@pytest.mark.asyncio
async def test_openai_deferred_provider_parses_error_file() -> None:
    client = _Client()
    client.batches.batch.output_file_id = None
    client.batches.batch.error_file_id = "file-error"
    client.files.content_by_id["file-error"] = (
        json.dumps(
            {
                "custom_id": "request-1",
                "response": None,
                "error": {"code": "batch_expired", "message": "expired"},
            },
        )
        + "\n"
    )
    provider = OpenAIDeferredProvider(client=client)
    handle = await provider.submit((_request(),))

    results = await provider.poll(handle)

    assert isinstance(results, DeferredResults)
    assert results.failed["request-1"].error_type == "batch_expired"


@pytest.mark.asyncio
async def test_openai_deferred_provider_rejects_tool_call_mode() -> None:
    provider = OpenAIDeferredProvider(client=_Client())
    request = _request().model_copy(update={"structured_output_mode": "tool_call"})

    with pytest.raises(InfrastructureError, match="unsupported_structured_output_mode"):
        await provider.submit((request,))
