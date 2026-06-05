"""pydantic-ai backed instance proposer per ADR-0009."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import (
    DocumentView,
    ExtractionSpec,
    InstanceCandidateSet,
    InstanceProposerResponse,
    Message,
    ProviderResult,
    RenderedPrompt,
    UsageEvent,
)
from extractx.core.versions import soft_producer_version, stable_hash
from extractx.extras.pydantic_ai.usage import usage_event_from_pydantic_ai_result
from extractx.instances.proposer import (
    enforce_instance_proposer_contract,
    instance_candidate_set_hash,
)

__all__ = [
    "InstanceProposalResponse",
    "InstanceProposerOutputMalformedError",
    "LLMInstanceProposer",
]


ProviderFn = Callable[
    [RenderedPrompt, type["InstanceProposalResponse"]],
    "InstanceProposalResponse | ProviderResult[InstanceProposalResponse] | Mapping[str, Any]",
]


class InstanceProposerOutputMalformedError(ValueError):
    """raised when provider output is not a valid instance proposer DTO."""


class InstanceProposalResponse(BaseModel):
    """provider DTO for bounded instance-id selection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected_instance_ids: tuple[str, ...]
    reason: str | None = Field(max_length=2_000)

    @model_validator(mode="before")
    @classmethod
    def _fill_explicit_empty_fields(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        payload = dict(cast("Mapping[str, Any]", data))
        payload.setdefault("reason", None)
        return payload


class LLMInstanceProposer:
    """LLM-backed proposer that selects from bounded instance candidates."""

    def __init__(
        self,
        *,
        model_id: str,
        provider: ProviderFn | None = None,
        temperature: float = 0,
        seed: int | None = 0,
    ) -> None:
        if not model_id:
            raise ValueError("LLMInstanceProposer: model_id must be non-empty")
        self._model_id = model_id
        self._provider = provider
        self._temperature = temperature
        self._seed = seed
        self._last_metadata: Mapping[str, Any] | None = None
        self._last_usage_event: UsageEvent | None = None
        self._template_hash = stable_hash(
            {
                "template_id": "extractx.instances.proposer.v1",
                "contract": "bounded-id-instance-proposal",
                "version": 1,
            },
        )
        self._code_hash = stable_hash(
            {
                "producer": "LLMInstanceProposer",
                "output": "InstanceProposalResponse",
                "contract": "bounded-id-instance-proposal",
                "version": 1,
            },
        )

    @property
    def producer_version(self) -> str:
        return soft_producer_version(
            model_id=self._model_id,
            prompt_template_hash=self._template_hash,
            code_hash=self._code_hash,
        )

    @property
    def last_metadata(self) -> Mapping[str, Any] | None:
        return self._last_metadata

    @property
    def last_usage_event(self) -> UsageEvent | None:
        return self._last_usage_event

    def render_prompt(
        self,
        document_view: DocumentView,
        spec: ExtractionSpec,
        candidate_set: InstanceCandidateSet,
    ) -> RenderedPrompt:
        candidate_payload = [
            {
                "instance_id": candidate.instance_id,
                "instance_type": candidate.instance_type,
                "label": candidate.label,
                "context": candidate.context,
                "anchor_candidate_ids": candidate.anchor_candidate_ids,
                "anchor_span_count": len(candidate.anchor_spans),
            }
            for candidate in candidate_set.candidates
        ]
        user_payload = {
            "document_id": document_view.document_id,
            "instance_type": spec.instance_type,
            "fields": [
                {
                    "field_id": field.field_id,
                    "description": field.description,
                    "cardinality": field.cardinality.value,
                }
                for field in spec.fields
            ],
            "candidate_instances": candidate_payload,
        }
        user_message = json.dumps(
            user_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        messages = (
            Message(
                role="system",
                content=(
                    "Select which bounded extraction instance ids are real instances "
                    "of the requested instance_type in this one document. Return only "
                    "structured output. Do not invent ids, assign fields, extract "
                    "values, write spans, normalize values, or infer domain identity. "
                    "Select every candidate instance that should be processed; return "
                    "an empty selection only if no candidate is a valid instance."
                ),
            ),
            Message(role="user", content=user_message),
        )
        rendered_prompt_hash = stable_hash([m.model_dump(mode="json") for m in messages])
        metadata = {
            "prompt_template_id": "extractx.instances.proposer.v1",
            "prompt_template_hash": self._template_hash,
            "producer_version": self.producer_version,
            "model_id": self._model_id,
            "temperature": self._temperature,
            "seed": self._seed,
            "document_hash": document_view.source_ref.content_hash,
            "spec_version": spec.version,
            "instance_candidate_set_hash": instance_candidate_set_hash(candidate_set),
            "rendered_prompt_hash": rendered_prompt_hash,
            "soft_call_identity": stable_hash(
                {
                    "document_hash": document_view.source_ref.content_hash,
                    "spec_version": spec.version,
                    "instance_candidate_set_hash": instance_candidate_set_hash(candidate_set),
                    "rendered_prompt_hash": rendered_prompt_hash,
                    "model_id": self._model_id,
                    "temperature": self._temperature,
                    "seed": self._seed,
                    "producer_code_hash": self._code_hash,
                },
            ),
            "allowed_instance_ids": tuple(c.instance_id for c in candidate_set.candidates),
        }
        return RenderedPrompt(
            messages=messages,
            structured_output_schema=_bounded_output_schema(
                tuple(c.instance_id for c in candidate_set.candidates),
            ),
            metadata=metadata,
        )

    def propose(
        self,
        document_view: DocumentView,
        spec: ExtractionSpec,
        candidate_set: InstanceCandidateSet,
    ) -> InstanceProposerResponse:
        if not candidate_set.candidates:
            raise InstanceProposerOutputMalformedError(
                "instance_proposer.output_malformed: candidate set is empty",
            )
        rendered = self.render_prompt(document_view, spec, candidate_set)
        self._last_metadata = rendered.metadata
        raw = (
            self._provider(rendered, InstanceProposalResponse)
            if self._provider is not None
            else self._call_pydantic_ai(rendered)
        )
        response, usage_event = _coerce_response(raw)
        self._last_usage_event = usage_event
        canonical = InstanceProposerResponse(
            selected_instance_ids=response.selected_instance_ids,
            reason=response.reason,
        )
        return enforce_instance_proposer_contract(canonical, candidate_set)

    def _call_pydantic_ai(
        self,
        rendered: RenderedPrompt,
    ) -> InstanceProposalResponse | ProviderResult[InstanceProposalResponse] | Mapping[str, Any]:
        try:
            pydantic_ai = importlib.import_module("pydantic_ai")
        except ImportError as exc:
            raise InfrastructureError(
                "instance_proposer.missing_llm: pydantic-ai is not installed; "
                "install extractx[pydantic_ai] or inject a fake provider for tests",
            ) from exc

        agent_cls = getattr(pydantic_ai, "Agent", None)
        if agent_cls is None:
            raise InfrastructureError(
                "instance_proposer.missing_llm: pydantic_ai.Agent is unavailable",
            )

        prompt_text = "\n\n".join(message.content for message in rendered.messages)
        try:
            agent = agent_cls(
                self._model_id,
                output_type=InstanceProposalResponse,
                model_settings={
                    "temperature": self._temperature,
                    **({} if self._seed is None else {"seed": self._seed}),
                },
            )
            result = agent.run_sync(prompt_text)
        except Exception as exc:  # pragma: no cover - real provider opt-in only.
            raise InfrastructureError(
                "instance_proposer.provider_unavailable: pydantic-ai instance "
                f"proposer call failed: {exc}",
            ) from exc
        return ProviderResult(
            output=getattr(result, "output", result),
            usage_event=usage_event_from_pydantic_ai_result(result, rendered=rendered),
        )


def _coerce_response(
    raw: InstanceProposalResponse | ProviderResult[InstanceProposalResponse] | Mapping[str, Any],
) -> tuple[InstanceProposalResponse, UsageEvent | None]:
    if isinstance(raw, ProviderResult):
        response, usage_event = _coerce_response(raw.output)
        return response, raw.usage_event if raw.usage_event is not None else usage_event
    if isinstance(raw, InstanceProposalResponse):
        return raw, None
    try:
        return InstanceProposalResponse.model_validate(raw), None
    except ValidationError as exc:
        raise InstanceProposerOutputMalformedError(
            f"instance_proposer.output_malformed: {exc}",
        ) from exc


def _bounded_output_schema(instance_ids: tuple[str, ...]) -> Mapping[str, Any]:
    schema: dict[str, Any] = InstanceProposalResponse.model_json_schema()
    raw_properties = schema.get("properties")
    if isinstance(raw_properties, dict):
        properties = cast("dict[str, Any]", raw_properties)
        selected = properties.get("selected_instance_ids")
        if isinstance(selected, dict):
            selected["items"] = {"enum": list(instance_ids)}
    return schema
