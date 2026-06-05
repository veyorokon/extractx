"""`PydanticAISelector` — pydantic-ai backed selector per ADR-0002 / ADR-0008.

`SelectorObservationResponse` is the provider DTO validated at the
pydantic-ai boundary. The selector maps it to canonical core
`Observation` before crossing seam D.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from extractx.candidates.generators.literal_set import LiteralSetCandidateStrategy
from extractx.core.cardinality import Cardinality
from extractx.core.contracts import Prompt
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import (
    Candidate,
    CandidateSet,
    ContextPack,
    ExtractionSpec,
    FieldSpec,
    Message,
    Observation,
    ProviderResult,
    RenderedPrompt,
    UsageEvent,
)
from extractx.core.versions import soft_producer_version, stable_hash
from extractx.execution.deferred import (
    SoftCallRequest,
    SoftCallResponse,
    SoftCallRouting,
    adapt_soft_call_response,
    make_soft_call_request_id,
)
from extractx.extras.pydantic_ai.usage import usage_event_from_pydantic_ai_result
from extractx.selection.examples import (
    ExpectedObservation,
    SelectorDemo,
    SelectorDemoSet,
    SelectorPromptPolicy,
)
from extractx.selection.prompts import (
    SelectionPrompt,
    intern_prompt_contexts,
    render_candidate_lines,
    render_context_open_tag,
)
from extractx.selection.selector import (
    SelectorContractError,
    enforce_batch_observation_contract,
    enforce_observation_contract,
)

__all__ = [
    "BatchSelectorObservationResponse",
    "PydanticAIBatchSelector",
    "PydanticAISelector",
    "SelectorObservationResponse",
    "SelectorOutputMalformedError",
]

_SELECTOR_OUTPUT_MODEL_REF = "extractx.pydantic_ai.selector_response.v1"
_BATCH_SELECTOR_OUTPUT_MODEL_REF = "extractx.pydantic_ai.batch_selector_response.v1"
logger = logging.getLogger(__name__)


ProviderFn = Callable[
    [RenderedPrompt, type["SelectorObservationResponse"]],
    "SelectorObservationResponse | ProviderResult[SelectorObservationResponse] | Mapping[str, Any]",
]


class PromptWithBoundedIds(Prompt, Protocol):
    def render_for_ids(
        self,
        *,
        field_spec: FieldSpec,
        candidate_summaries: tuple[Candidate, ...],
        allowed_instance_ids: Sequence[str],
        context_pack: ContextPack | None = None,
    ) -> RenderedPrompt: ...


class SelectorOutputMalformedError(ValueError):
    """raised when the provider output is not a valid selector DTO."""


class SelectorObservationResponse(BaseModel):
    """temporary provider DTO for ADR-0008 observation-shaped selection.

    It is not the canonical core `Observation` model. It intentionally
    carries only bounded ids and a diagnostic reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str = Field(
        description="Exact instance_id selected from the bounded allowed_instance_ids set.",
    )
    field_id: str = Field(
        description="Exact field_id selected from the bounded allowed_field_ids set.",
    )
    evidence_id: str | None = Field(
        description=(
            "Exact candidate_id selected from the bounded allowed_evidence_ids set. "
            "Never return candidate text, raw values, normalized values, or spans here."
        ),
    )
    selected_candidate_ids: tuple[str, ...] = Field(
        description=(
            "Exact candidate_id values selected from the bounded allowed_evidence_ids set "
            "for multi-select fields. Never return candidate text or raw values."
        ),
    )
    abstain: bool
    reason: str | None = Field(max_length=2_000)

    @model_validator(mode="before")
    @classmethod
    def _fill_explicit_empty_fields(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        payload = dict(cast("Mapping[str, Any]", data))
        payload.setdefault("evidence_id", None)
        payload.setdefault("selected_candidate_ids", ())
        payload.setdefault("abstain", False)
        payload.setdefault("reason", None)
        return payload

    @model_validator(mode="after")
    def _normalize_selected_ids(self) -> SelectorObservationResponse:
        if self.abstain:
            if self.evidence_id is not None or self.selected_candidate_ids:
                raise ValueError(
                    "SelectorObservationResponse: abstain=True requires no selected ids",
                )
            return self
        selected = self.selected_candidate_ids
        if self.evidence_id is not None and not selected:
            object.__setattr__(self, "selected_candidate_ids", (self.evidence_id,))
            return self
        if self.evidence_id is None and len(selected) == 1:
            object.__setattr__(self, "evidence_id", selected[0])
            return self
        return self


class BatchSelectorObservationResponse(BaseModel):
    """provider DTO for one batch selector call over many fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observations: tuple[SelectorObservationResponse, ...]


BatchProviderFn = Callable[
    [RenderedPrompt, type[BatchSelectorObservationResponse]],
    BatchSelectorObservationResponse
    | ProviderResult[BatchSelectorObservationResponse]
    | Mapping[str, Any],
]


class PydanticAISelector:
    """LLM-backed selector that classifies among bounded candidate ids."""

    def __init__(
        self,
        *,
        model_id: str,
        provider: ProviderFn | None = None,
        prompt: PromptWithBoundedIds | None = None,
        prompt_recorder: object | None = None,
        prompt_asset_resolver: object | None = None,
        prompt_policy: SelectorPromptPolicy | None = None,
        temperature: float = 0,
        seed: int | None = 0,
    ) -> None:
        if not model_id:
            raise ValueError("PydanticAISelector: model_id must be non-empty")
        self._model_id = model_id
        self._provider = provider
        self._prompt = prompt if prompt is not None else SelectionPrompt()
        self._prompt_recorder = prompt_recorder
        self._prompt_asset_resolver = prompt_asset_resolver
        self._prompt_policy = prompt_policy or SelectorPromptPolicy()
        self._temperature = temperature
        self._seed = seed
        self._last_usage_event: UsageEvent | None = None
        self._last_call_diagnostic: Mapping[str, object] | None = None
        self._code_hash = stable_hash(
            {
                "producer": "PydanticAISelector",
                "output": "SelectorObservationResponse",
                "contract": "bounded-id-observation",
                "version": 1,
            },
        )

    @property
    def producer_version(self) -> str:
        return soft_producer_version(
            model_id=self._model_id,
            prompt_template_hash=self._prompt.template_hash,
            code_hash=self._code_hash,
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def seed(self) -> int | None:
        return self._seed

    @property
    def last_usage_event(self) -> UsageEvent | None:
        return self._last_usage_event

    @property
    def last_call_diagnostic(self) -> Mapping[str, object] | None:
        return self._last_call_diagnostic

    def render_prompt(
        self,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        context_pack: ContextPack,
        *,
        instance_ids: Sequence[str] = ("inst_0",),
    ) -> RenderedPrompt:
        """render the exact prompt sent to the provider."""

        _validate_field_alignment(field_spec, candidate_set)
        rendered = self._prompt.render_for_ids(
            field_spec=field_spec,
            candidate_summaries=candidate_set.candidates,
            allowed_instance_ids=tuple(instance_ids),
            context_pack=context_pack,
        )
        demo_sets, instruction = _resolve_selector_prompt_assets(
            policy=self._prompt_policy,
            resolver=self._prompt_asset_resolver,
        )
        rendered = _rendered_with_selector_prompt_assets(
            rendered,
            demo_sets=demo_sets,
            instruction=instruction,
        )
        metadata = {
            **dict(rendered.metadata),
            "producer_version": self.producer_version,
            "model_id": self._model_id,
            "temperature": self._temperature,
            "seed": self._seed,
            "selector_prompt_policy": self._prompt_policy.model_dump(mode="json"),
            "selector_demo_set_hashes": tuple(_demo_set_hash(demo_set) for demo_set in demo_sets),
            "rendered_prompt_hash": stable_hash(
                [message.model_dump(mode="json") for message in rendered.messages],
            ),
            "candidate_overflow": (
                context_pack.candidate_overflow.model_dump(mode="json")
                if context_pack.candidate_overflow is not None
                else None
            ),
            "classification_context_by_field": _classification_context_by_field_payload(
                context_pack,
            ),
        }
        return rendered.model_copy(
            update={
                "structured_output_schema": _bounded_output_schema(
                    field_id=field_spec.field_id,
                    instance_ids=tuple(instance_ids),
                    evidence_ids=tuple(rendered.metadata["allowed_evidence_ids"]),
                ),
                "metadata": metadata,
            },
        )

    def render_soft_call_request(
        self,
        rendered: RenderedPrompt,
        *,
        field_id: str,
        instance_id: str | None = None,
        spec_hash: str = "immediate",
    ) -> SoftCallRequest:
        routing = SoftCallRouting(field_id=field_id, instance_id=instance_id)
        request_id = make_soft_call_request_id(
            soft_call_identity=_soft_call_identity(rendered),
            spec_hash=spec_hash,
            output_model_ref=_SELECTOR_OUTPUT_MODEL_REF,
            routing=routing,
        )
        return SoftCallRequest(
            request_id=request_id,
            rendered_prompt=rendered,
            output_model_ref=_SELECTOR_OUTPUT_MODEL_REF,
            soft_call_identity=_soft_call_identity(rendered),
            structured_output_mode=_structured_output_mode(rendered),
            routing=routing,
        )

    def select_observation(
        self,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        context_pack: ContextPack,
        *,
        instance_ids: Sequence[str] = ("inst_0",),
    ) -> SelectorObservationResponse:
        """return the ADR-0008-shaped provider decision over bounded ids."""

        _validate_field_alignment(field_spec, candidate_set)
        bounded_instance_ids = tuple(instance_ids)
        if not bounded_instance_ids:
            raise SelectorContractError("selector received no bounded instance ids")

        if not candidate_set.candidates:
            return SelectorObservationResponse(
                instance_id=bounded_instance_ids[0],
                field_id=field_spec.field_id,
                evidence_id=None,
                selected_candidate_ids=(),
                abstain=True,
                reason="no_candidates",
            )

        rendered = self.render_prompt(
            field_spec,
            candidate_set,
            context_pack,
            instance_ids=bounded_instance_ids,
        )
        request = self.render_soft_call_request(
            rendered,
            field_id=field_spec.field_id,
            instance_id=bounded_instance_ids[0],
        )
        prompt_ref = _record_prompt(self._prompt_recorder, rendered, seam="selector")
        try:
            raw = (
                self._provider(rendered, SelectorObservationResponse)
                if self._provider is not None
                else self._call_pydantic_ai(rendered)
            )
        except ValidationError as exc:
            raise SelectorOutputMalformedError(f"selector.output_malformed: {exc}") from exc
        response_before_translation, usage_event = _adapt_raw_soft_call_response(
            request,
            raw,
            output_model=SelectorObservationResponse,
            seam="selector",
        )
        before_payload = response_before_translation.model_dump(mode="json")
        response = _translate_prompt_response_ids(
            response_before_translation,
            prompt_to_canonical=cast(
                "Mapping[str, str]",
                rendered.metadata.get("prompt_candidate_id_map", {}),
            ),
            seam="selector",
        )
        after_payload = response.model_dump(mode="json")
        self._last_usage_event = usage_event
        self._last_call_diagnostic = _selector_prompt_diagnostic_payload(
            rendered=rendered,
            prompt_ref=prompt_ref,
            response_before_translation_hash=stable_hash(before_payload),
            response_after_translation_hash=stable_hash(after_payload),
            usage_event=usage_event,
            model_metadata={
                "model_id": self._model_id,
                "producer_version": self.producer_version,
                "temperature": self._temperature,
                "seed": self._seed,
            },
        )
        _enforce_observation_contract(
            response=response,
            field_spec=field_spec,
            candidate_set=candidate_set,
            instance_ids=bounded_instance_ids,
        )
        return response

    def select(
        self,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        context_pack: ContextPack,
        instance_state: object | None = None,
        *,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> Observation:
        """return one canonical `Observation` without provider-authored values."""

        del instance_state
        bounded_instance_ids = tuple(instance_ids)
        if not bounded_instance_ids:
            raise SelectorContractError("selector received no bounded instance ids")
        if not candidate_set.candidates:
            self._last_call_diagnostic = None
            observation = Observation(
                outcome="NO_CANDIDATES",
                selected_candidate_ids=(),
                reason=None,
                producer_version=self.producer_version,
                instance_id=bounded_instance_ids[0],
                field_id=field_spec.field_id,
                evidence_id=None,
                abstain=True,
            )
            return enforce_observation_contract(observation, candidate_set)

        response = self.select_observation(
            field_spec,
            candidate_set,
            context_pack,
            instance_ids=bounded_instance_ids,
        )
        if response.abstain:
            observation = Observation(
                outcome="ABSTAINED",
                selected_candidate_ids=(),
                reason=response.reason,
                producer_version=self.producer_version,
                instance_id=response.instance_id,
                field_id=response.field_id,
                evidence_id=None,
                abstain=True,
            )
        else:
            if (
                not response.selected_candidate_ids
                and field_spec.cardinality is not Cardinality.MANY
            ):
                raise SelectorOutputMalformedError(
                    "selector.output_malformed: non-abstaining response must carry selected ids",
                )
            observation = _response_to_observation(
                response=response,
                field_spec=field_spec,
                producer_version=self.producer_version,
                seam="selector",
            )
        return enforce_observation_contract(observation, candidate_set)

    def _call_pydantic_ai(
        self,
        rendered: RenderedPrompt,
    ) -> (
        SelectorObservationResponse
        | ProviderResult[SelectorObservationResponse]
        | Mapping[str, Any]
    ):
        """call a real pydantic-ai provider.

        This path is opt-in: default tests inject a fake provider. Missing
        optional dependencies or provider failures are surfaced as
        infrastructure defects, never converted into empty selections.
        """

        try:
            pydantic_ai = importlib.import_module("pydantic_ai")
        except ImportError as exc:
            raise InfrastructureError(
                "selector.missing_llm: pydantic-ai is not installed; install "
                "extractx[pydantic_ai] or inject a fake provider for tests",
            ) from exc

        agent_cls = getattr(pydantic_ai, "Agent", None)
        if agent_cls is None:
            raise InfrastructureError("selector.missing_llm: pydantic_ai.Agent is unavailable")

        prompt_text = "\n\n".join(message.content for message in rendered.messages)
        try:
            agent = agent_cls(
                self._model_id,
                output_type=SelectorObservationResponse,
                model_settings={
                    "temperature": self._temperature,
                    **({} if self._seed is None else {"seed": self._seed}),
                },
            )
            result = agent.run_sync(prompt_text)
        except Exception as exc:  # pragma: no cover - real provider opt-in only.
            raise InfrastructureError(
                f"selector.provider_unavailable: pydantic-ai selector call failed: {exc}",
            ) from exc

        return ProviderResult(
            output=getattr(result, "output", result),
            usage_event=usage_event_from_pydantic_ai_result(result, rendered=rendered),
        )


class PydanticAIBatchSelector:
    """LLM-backed batch selector that returns canonical observations.

    This is the batch sibling of `PydanticAISelector`: it uses the same
    bounded-id DTO for each decision, but asks the provider to classify all
    supplied fields in one structured-output call.
    """

    def __init__(
        self,
        *,
        model_id: str,
        provider: BatchProviderFn | None = None,
        prompt_recorder: object | None = None,
        prompt_asset_resolver: object | None = None,
        prompt_policies: Mapping[str, SelectorPromptPolicy] | None = None,
        temperature: float = 0,
        seed: int | None = 0,
    ) -> None:
        if not model_id:
            raise ValueError("PydanticAIBatchSelector: model_id must be non-empty")
        self._model_id = model_id
        self._provider = provider
        self._prompt_recorder = prompt_recorder
        self._prompt_asset_resolver = prompt_asset_resolver
        self._prompt_policies = dict(prompt_policies or {})
        self._temperature = temperature
        self._seed = seed
        self._last_usage_event: UsageEvent | None = None
        self._last_call_diagnostic: Mapping[str, object] | None = None
        self._template_hash = stable_hash(
            {
                "template_id": "extractx.selection.batch_observation.v1",
                "contract": "bounded-id-batch-observation",
                "version": 1,
            },
        )
        self._code_hash = stable_hash(
            {
                "producer": "PydanticAIBatchSelector",
                "output": "BatchSelectorObservationResponse",
                "contract": "bounded-id-batch-observation",
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
    def model_id(self) -> str:
        return self._model_id

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def seed(self) -> int | None:
        return self._seed

    @property
    def last_usage_event(self) -> UsageEvent | None:
        return self._last_usage_event

    @property
    def last_call_diagnostic(self) -> Mapping[str, object] | None:
        return self._last_call_diagnostic

    def render_prompt(
        self,
        *,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
        context_pack: ContextPack,
        instance_ids: Sequence[str] = ("inst_0",),
    ) -> RenderedPrompt:
        field_specs = _field_specs_by_id(spec)
        for candidate_set in candidate_sets:
            if candidate_set.field_id not in field_specs:
                raise SelectorContractError(
                    "batch selector received CandidateSet for unknown field_id "
                    f"{candidate_set.field_id!r}",
                )
        bounded_instance_ids = tuple(instance_ids)
        field_payload: list[Mapping[str, Any]] = []
        allowed_evidence_ids_by_field: dict[str, tuple[str, ...]] = {}
        prompt_field_id_map = _batch_prompt_field_id_map(
            tuple(field_spec.field_id for field_spec in spec.fields),
            candidate_sets,
        )
        prompt_candidate_id_map_by_field = _batch_prompt_candidate_id_maps(
            candidate_sets,
            prompt_field_id_map=prompt_field_id_map,
        )
        prompt_contexts_by_field: dict[str, Any] = {}
        for candidate_set in candidate_sets:
            field_spec = field_specs[candidate_set.field_id]
            requires_document_context = _is_literal_set_category_field(field_spec)
            has_classification_context = (
                candidate_set.field_id in context_pack.classification_context_by_field
            )
            if (
                requires_document_context
                and not context_pack.document_summary
                and not has_classification_context
            ):
                raise SelectorContractError(
                    "batch selector document-level classification requires document context",
                )
            prompt_to_canonical = prompt_candidate_id_map_by_field[candidate_set.field_id]
            canonical_to_prompt = {v: k for k, v in prompt_to_canonical.items()}
            allowed_ids = tuple(prompt_to_canonical)
            allowed_evidence_ids_by_field[candidate_set.field_id] = allowed_ids
            candidate_payload: list[dict[str, object]] = [
                _candidate_payload(
                    candidate,
                    candidate_id=canonical_to_prompt[candidate.candidate_id],
                )
                for candidate in candidate_set.candidates
            ]
            rendered_candidates, contexts = intern_prompt_contexts(candidate_payload)
            prompt_contexts_by_field[candidate_set.field_id] = contexts
            classification_context = _classification_context_for_field_payload(
                context_pack,
                candidate_set.field_id,
            )
            document_context = (
                context_pack.document_summary
                if requires_document_context and not classification_context
                else None
            )
            field_payload.append(
                {
                    "field": {
                        "field_id": field_spec.field_id,
                        "local_field_id": prompt_field_id_map[candidate_set.field_id],
                        "description": field_spec.description,
                        "value_kind": field_spec.value_kind.name,
                        "cardinality": field_spec.cardinality.value,
                        "python_type": _qualname(field_spec.python_type),
                        "document_context": document_context,
                    },
                    "classification_context": classification_context,
                    "allowed_evidence_ids": allowed_ids,
                    "contexts": contexts,
                    "candidates": rendered_candidates,
                },
            )

        user_message = _render_batch_user_message(
            schema_version=spec.version,
            schema_description=context_pack.schema_description,
            allowed_instance_ids=bounded_instance_ids,
            fields=field_payload,
            retry_feedback=context_pack.retry_feedback,
        )
        demo_sets_by_field, instruction_by_field = _resolve_batch_selector_prompt_assets(
            field_ids=tuple(candidate_set.field_id for candidate_set in candidate_sets),
            policies=self._prompt_policies,
            resolver=self._prompt_asset_resolver,
        )
        user_message = _user_message_with_batch_selector_prompt_assets(
            user_message,
            demo_sets_by_field=demo_sets_by_field,
            instruction_by_field=instruction_by_field,
        )
        messages = (
            Message(
                role="system",
                content=(
                    "You classify each requested field by choosing bounded candidate IDs only. "
                    "Return exactly one observation per field in the structured output. "
                    "Do not extract values, normalize values, copy candidate text, write spans, "
                    "invent IDs, or infer domain identity. For every non-abstaining observation, "
                    "copy field_id from the field block, use one allowed instance_id, and set "
                    "evidence_id / selected_candidate_ids to exact candidate_id strings from that "
                    "same field's candidate blocks. Set abstain=true only when no bounded "
                    "candidate matches that field."
                ),
            ),
            Message(role="user", content=user_message),
        )
        return RenderedPrompt(
            messages=messages,
            structured_output_schema=_batch_output_schema(
                field_ids=tuple(candidate_set.field_id for candidate_set in candidate_sets),
                instance_ids=bounded_instance_ids,
                evidence_ids_by_field=allowed_evidence_ids_by_field,
            ),
            metadata={
                "prompt_template_id": "extractx.selection.batch_observation.v1",
                "prompt_template_hash": self._template_hash,
                "producer_version": self.producer_version,
                "model_id": self._model_id,
                "temperature": self._temperature,
                "seed": self._seed,
                "selector_prompt_policies": {
                    field_id: policy.model_dump(mode="json")
                    for field_id, policy in self._prompt_policies.items()
                    if field_id in {candidate_set.field_id for candidate_set in candidate_sets}
                },
                "selector_demo_set_hashes_by_field": {
                    field_id: tuple(_demo_set_hash(demo_set) for demo_set in demo_sets)
                    for field_id, demo_sets in demo_sets_by_field.items()
                },
                "rendered_prompt_hash": stable_hash(
                    [message.model_dump(mode="json") for message in messages],
                ),
                "allowed_field_ids": tuple(
                    candidate_set.field_id for candidate_set in candidate_sets
                ),
                "allowed_instance_ids": bounded_instance_ids,
                "allowed_evidence_ids_by_field": allowed_evidence_ids_by_field,
                "canonical_allowed_evidence_ids_by_field": {
                    candidate_set.field_id: tuple(
                        candidate.candidate_id for candidate in candidate_set.candidates
                    )
                    for candidate_set in candidate_sets
                },
                "prompt_candidate_id_map_by_field": prompt_candidate_id_map_by_field,
                "prompt_field_id_map": prompt_field_id_map,
                "prompt_contexts_by_field": prompt_contexts_by_field,
                "classification_context_by_field": _classification_context_by_field_payload(
                    context_pack,
                ),
                "candidate_overflow": (
                    context_pack.candidate_overflow.model_dump(mode="json")
                    if context_pack.candidate_overflow is not None
                    else None
                ),
            },
        )

    def render_soft_call_request(
        self,
        rendered: RenderedPrompt,
        *,
        spec_hash: str,
        routing: SoftCallRouting | None = None,
    ) -> SoftCallRequest:
        routing = routing or SoftCallRouting()
        request_id = make_soft_call_request_id(
            soft_call_identity=_soft_call_identity(rendered),
            spec_hash=spec_hash,
            output_model_ref=_BATCH_SELECTOR_OUTPUT_MODEL_REF,
            routing=routing,
        )
        return SoftCallRequest(
            request_id=request_id,
            rendered_prompt=rendered,
            output_model_ref=_BATCH_SELECTOR_OUTPUT_MODEL_REF,
            soft_call_identity=_soft_call_identity(rendered),
            structured_output_mode=_structured_output_mode(rendered),
            routing=routing,
        )

    def select_many(
        self,
        *,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
        context_pack: ContextPack,
        instance_state: object | None = None,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> tuple[Observation, ...]:
        del instance_state
        bounded_instance_ids = tuple(instance_ids)
        if not bounded_instance_ids:
            raise SelectorContractError("batch selector received no bounded instance ids")
        if not candidate_sets:
            return ()

        rendered = self.render_prompt(
            spec=spec,
            candidate_sets=candidate_sets,
            context_pack=context_pack,
            instance_ids=bounded_instance_ids,
        )
        request = self.render_soft_call_request(rendered, spec_hash=spec.version)
        prompt_ref = _record_prompt(self._prompt_recorder, rendered, seam="selector.batch")
        try:
            raw: (
                BatchSelectorObservationResponse
                | ProviderResult[BatchSelectorObservationResponse]
                | Mapping[str, Any]
            ) = (
                self._provider(rendered, BatchSelectorObservationResponse)
                if self._provider is not None
                else self._call_pydantic_ai(rendered)
            )
        except ValidationError as exc:
            raise SelectorOutputMalformedError(f"batch_selector.output_malformed: {exc}") from exc
        response_before_translation, usage_event = _adapt_raw_soft_call_response(
            request,
            raw,
            output_model=BatchSelectorObservationResponse,
            seam="batch_selector",
        )
        before_payload = response_before_translation.model_dump(mode="json")
        prompt_maps = cast(
            "Mapping[str, Mapping[str, str]]",
            rendered.metadata.get("prompt_candidate_id_map_by_field", {}),
        )
        prompt_field_id_map = cast(
            "Mapping[str, str]",
            rendered.metadata.get("prompt_field_id_map", {}),
        )
        response = BatchSelectorObservationResponse(
            observations=tuple(
                _translate_batch_prompt_response_ids(
                    selector_response,
                    prompt_field_id_map=prompt_field_id_map,
                    prompt_candidate_id_maps=prompt_maps,
                    seam="batch_selector",
                )
                for selector_response in response_before_translation.observations
            ),
        )
        after_payload = response.model_dump(mode="json")
        self._last_usage_event = usage_event
        self._last_call_diagnostic = _selector_prompt_diagnostic_payload(
            rendered=rendered,
            prompt_ref=prompt_ref,
            response_before_translation_hash=stable_hash(before_payload),
            response_after_translation_hash=stable_hash(after_payload),
            usage_event=usage_event,
            model_metadata={
                "model_id": self._model_id,
                "producer_version": self.producer_version,
                "temperature": self._temperature,
                "seed": self._seed,
            },
        )

        return self._observations_from_batch_response(
            response=response,
            spec=spec,
            candidate_sets=candidate_sets,
        )

    def observations_from_soft_call_response(
        self,
        *,
        request: SoftCallRequest,
        response: SoftCallResponse,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
    ) -> tuple[Observation, ...]:
        """Map one recorded deferred batch-selector response to Observations."""

        try:
            result = adapt_soft_call_response(
                request,
                response,
                output_model=BatchSelectorObservationResponse,
            )
        except ValidationError as exc:
            raise SelectorOutputMalformedError(
                f"batch_selector.output_malformed: {exc}",
            ) from exc

        prompt_maps = cast(
            "Mapping[str, Mapping[str, str]]",
            request.rendered_prompt.metadata.get("prompt_candidate_id_map_by_field", {}),
        )
        prompt_field_id_map = cast(
            "Mapping[str, str]",
            request.rendered_prompt.metadata.get("prompt_field_id_map", {}),
        )
        translated = BatchSelectorObservationResponse(
            observations=tuple(
                _translate_batch_prompt_response_ids(
                    selector_response,
                    prompt_field_id_map=prompt_field_id_map,
                    prompt_candidate_id_maps=prompt_maps,
                    seam="batch_selector",
                )
                for selector_response in result.output.observations
            ),
        )
        self._last_usage_event = result.usage_event
        self._last_call_diagnostic = _selector_prompt_diagnostic_payload(
            rendered=request.rendered_prompt,
            prompt_ref=None,
            response_before_translation_hash=stable_hash(result.output.model_dump(mode="json")),
            response_after_translation_hash=stable_hash(translated.model_dump(mode="json")),
            usage_event=result.usage_event,
            model_metadata={
                "model_id": self._model_id,
                "producer_version": self.producer_version,
                "temperature": self._temperature,
                "seed": self._seed,
                "soft_call_request_id": request.request_id,
                "soft_call_identity": request.soft_call_identity,
            },
        )
        return self._observations_from_batch_response(
            response=translated,
            spec=spec,
            candidate_sets=candidate_sets,
        )

    def _observations_from_batch_response(
        self,
        *,
        response: BatchSelectorObservationResponse,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
    ) -> tuple[Observation, ...]:
        field_specs = _field_specs_by_id(spec)
        unknown_field_ids = [
            selector_response.field_id
            for selector_response in response.observations
            if selector_response.field_id not in field_specs
        ]
        if unknown_field_ids:
            raise SelectorContractError(
                "batch selector emitted observation for unknown field_id "
                f"{unknown_field_ids!r}; allowed={sorted(field_specs)!r}",
            )
        response = _coalesce_many_cardinality_batch_response(
            response=response,
            field_specs=field_specs,
        )
        observations = tuple(
            _response_to_observation(
                response=selector_response,
                field_spec=field_specs[selector_response.field_id],
                producer_version=self.producer_version,
                seam="batch_selector",
            )
            for selector_response in response.observations
        )
        return enforce_batch_observation_contract(observations, candidate_sets)

    def _call_pydantic_ai(
        self,
        rendered: RenderedPrompt,
    ) -> (
        BatchSelectorObservationResponse
        | ProviderResult[BatchSelectorObservationResponse]
        | Mapping[str, Any]
    ):
        try:
            pydantic_ai = importlib.import_module("pydantic_ai")
        except ImportError as exc:
            raise InfrastructureError(
                "batch_selector.missing_llm: pydantic-ai is not installed; install "
                "extractx[pydantic_ai] or inject a fake provider for tests",
            ) from exc

        agent_cls = getattr(pydantic_ai, "Agent", None)
        if agent_cls is None:
            raise InfrastructureError(
                "batch_selector.missing_llm: pydantic_ai.Agent is unavailable",
            )

        prompt_text = "\n\n".join(message.content for message in rendered.messages)
        try:
            agent = agent_cls(
                self._model_id,
                output_type=BatchSelectorObservationResponse,
                model_settings={
                    "temperature": self._temperature,
                    **({} if self._seed is None else {"seed": self._seed}),
                },
            )
            result = agent.run_sync(prompt_text)
        except Exception as exc:  # pragma: no cover - real provider opt-in only.
            raise InfrastructureError(
                "batch_selector.provider_unavailable: "
                f"pydantic-ai batch selector call failed: {exc}",
            ) from exc

        return ProviderResult(
            output=getattr(result, "output", result),
            usage_event=usage_event_from_pydantic_ai_result(result, rendered=rendered),
        )


def _adapt_raw_soft_call_response[OutputT](
    request: SoftCallRequest,
    raw: OutputT | ProviderResult[OutputT] | Mapping[str, Any],
    *,
    output_model: type[OutputT],
    seam: str,
) -> tuple[OutputT, UsageEvent | None]:
    try:
        response, usage_event = _soft_call_response_from_raw(
            request,
            raw,
            output_model=output_model,
        )
        result = adapt_soft_call_response(request, response, output_model=output_model)
    except ValidationError as exc:
        raise SelectorOutputMalformedError(f"{seam}.output_malformed: {exc}") from exc
    return result.output, usage_event if usage_event is not None else result.usage_event


def _soft_call_response_from_raw[OutputT](
    request: SoftCallRequest,
    raw: OutputT | ProviderResult[OutputT] | Mapping[str, Any],
    *,
    output_model: type[OutputT],
) -> tuple[SoftCallResponse, UsageEvent | None]:
    if isinstance(raw, ProviderResult):
        provider_result = cast("ProviderResult[OutputT]", raw)
        raw_output = provider_result.output
        response, usage_event = _soft_call_response_from_raw(
            request,
            raw_output,
            output_model=output_model,
        )
        return (
            response.model_copy(
                update={
                    "raw_usage": (
                        provider_result.usage_event.raw_usage
                        if provider_result.usage_event is not None
                        else response.raw_usage
                    ),
                    "raw_response_metadata": (
                        provider_result.usage_event.raw_response_metadata
                        if provider_result.usage_event is not None
                        else response.raw_response_metadata
                    ),
                },
            ),
            provider_result.usage_event
            if provider_result.usage_event is not None
            else usage_event,
        )
    if isinstance(raw, BaseModel):
        payload = raw.model_dump(mode="json")
    elif isinstance(raw, Mapping):
        payload = dict(cast("Mapping[str, Any]", raw))
    else:
        parsed = TypeAdapter(output_model).validate_python(raw)
        payload = _payload_from_typed_output(parsed)
    return SoftCallResponse(request_id=request.request_id, response_payload=payload), None


def _payload_from_typed_output(output: object) -> Mapping[str, Any]:
    if isinstance(output, BaseModel):
        return output.model_dump(mode="json")
    if isinstance(output, Mapping):
        return dict(cast("Mapping[str, Any]", output))
    raise SelectorOutputMalformedError(
        "selector.output_malformed: provider output must be pydantic model or mapping",
    )


def _record_prompt(recorder: object | None, rendered: RenderedPrompt, *, seam: str) -> str | None:
    if recorder is None:
        return None
    record = getattr(recorder, "record", None)
    if not callable(record):
        raise SelectorOutputMalformedError(
            "selector.prompt_recorder_malformed: prompt_recorder must expose record(...)",
        )
    ref = record(rendered, seam=seam)
    return ref if isinstance(ref, str) and ref else None


def _selector_prompt_diagnostic_payload(
    *,
    rendered: RenderedPrompt,
    prompt_ref: str | None,
    response_before_translation_hash: str,
    response_after_translation_hash: str,
    usage_event: UsageEvent | None,
    model_metadata: Mapping[str, object],
) -> Mapping[str, object]:
    field_ids = rendered.metadata.get("allowed_field_ids")
    single_field_id: str | None = None
    if isinstance(field_ids, tuple | list):
        typed_field_ids = cast("tuple[object, ...] | list[object]", field_ids)
        if len(typed_field_ids) == 1 and isinstance(typed_field_ids[0], str):
            single_field_id = typed_field_ids[0]
    allowed_evidence_ids = cast("object", rendered.metadata.get("allowed_evidence_ids"))
    prompt_candidate_id_map = cast("object", rendered.metadata.get("prompt_candidate_id_map"))
    allowed_evidence_ids_by_field: object = rendered.metadata.get(
        "allowed_evidence_ids_by_field",
    )
    if (
        allowed_evidence_ids_by_field is None
        and single_field_id is not None
        and isinstance(allowed_evidence_ids, tuple | list)
    ):
        allowed_evidence_ids_by_field = {
            single_field_id: cast("tuple[object, ...] | list[object]", allowed_evidence_ids),
        }
    prompt_candidate_id_map_by_field: object = rendered.metadata.get(
        "prompt_candidate_id_map_by_field",
    )
    if (
        prompt_candidate_id_map_by_field is None
        and single_field_id is not None
        and isinstance(prompt_candidate_id_map, Mapping)
    ):
        prompt_candidate_id_map_by_field = {
            single_field_id: cast("Mapping[object, object]", prompt_candidate_id_map),
        }
    prompt_asset_metadata = {
        key: rendered.metadata[key]
        for key in (
            "selector_prompt_policy",
            "selector_prompt_policies",
            "selector_demo_set_hashes",
            "selector_demo_set_hashes_by_field",
            "classification_context_by_field",
        )
        if key in rendered.metadata
    }
    payload: dict[str, object] = {
        "rendered_prompt_hash": rendered.metadata.get("rendered_prompt_hash"),
        "rendered_prompt_ref": prompt_ref,
        "allowed_evidence_ids": allowed_evidence_ids,
        "allowed_evidence_ids_by_field": allowed_evidence_ids_by_field,
        "prompt_candidate_id_map": prompt_candidate_id_map,
        "prompt_candidate_id_map_by_field": prompt_candidate_id_map_by_field,
        "prompt_field_id_map": rendered.metadata.get("prompt_field_id_map"),
        "classification_context_by_field": rendered.metadata.get(
            "classification_context_by_field",
        ),
        "selector_response_before_translation_hash": response_before_translation_hash,
        "selector_response_before_translation_ref": None,
        "selector_response_after_translation_hash": response_after_translation_hash,
        "selector_response_after_translation_ref": None,
        "usage_event": usage_event,
        "model_metadata": {**model_metadata, **prompt_asset_metadata},
    }
    return payload


def _soft_call_identity(rendered: RenderedPrompt) -> str:
    value = rendered.metadata.get("soft_call_identity")
    if isinstance(value, str) and value:
        return value
    value = rendered.metadata.get("rendered_prompt_hash")
    if isinstance(value, str) and value:
        return value
    return stable_hash([message.model_dump(mode="json") for message in rendered.messages])


def _structured_output_mode(rendered: RenderedPrompt) -> str | None:
    value = rendered.metadata.get("structured_output_mode")
    return value if isinstance(value, str) and value else None


def _response_to_observation(
    *,
    response: SelectorObservationResponse,
    field_spec: FieldSpec,
    producer_version: str,
    seam: str,
) -> Observation:
    if response.abstain:
        return _build_observation(
            outcome="ABSTAINED",
            selected_candidate_ids=(),
            reason=response.reason,
            producer_version=producer_version,
            instance_id=response.instance_id,
            field_id=response.field_id,
            evidence_id=None,
            abstain=True,
            raw_response=response,
            seam=seam,
        )
    if not response.selected_candidate_ids and field_spec.cardinality is not Cardinality.MANY:
        raise SelectorOutputMalformedError(
            "batch_selector.output_malformed: non-abstaining response must carry selected ids",
        )
    evidence_id = _normalized_evidence_id_for_selected_ids(response, seam=seam)
    return _build_observation(
        outcome="SELECTED",
        selected_candidate_ids=response.selected_candidate_ids,
        reason=response.reason,
        producer_version=producer_version,
        instance_id=response.instance_id,
        field_id=response.field_id,
        evidence_id=evidence_id,
        abstain=False,
        raw_response=response,
        seam=seam,
    )


def _coalesce_many_cardinality_batch_response(
    *,
    response: BatchSelectorObservationResponse,
    field_specs: Mapping[str, FieldSpec],
) -> BatchSelectorObservationResponse:
    """Normalize split multi-label observations into one field observation.

    The batch selector contract is one observation per `(field_id,
    instance_id)`. Some providers still split a multi-label field into
    repeated observations, one per selected label. The selector boundary owns
    provider DTO normalization, so coalesce that shape for `Cardinality.MANY`
    before enforcing the canonical batch observation contract. Single-label
    duplicate observations remain a contract error downstream.
    """

    grouped: dict[tuple[str, str], list[SelectorObservationResponse]] = {}
    key_order: list[tuple[str, str]] = []
    for item in response.observations:
        key = (item.field_id, item.instance_id)
        if key not in grouped:
            key_order.append(key)
            grouped[key] = []
        grouped[key].append(item)

    normalized: list[SelectorObservationResponse] = []
    for key in key_order:
        items = grouped[key]
        if len(items) == 1:
            normalized.append(items[0])
            continue

        field_id, instance_id = key
        field_spec = field_specs[field_id]
        if field_spec.cardinality is not Cardinality.MANY:
            normalized.extend(items)
            continue

        abstaining = [item for item in items if item.abstain]
        non_abstaining = [item for item in items if not item.abstain]
        if abstaining and non_abstaining:
            raise SelectorContractError(
                "batch selector emitted mixed abstain/non-abstain duplicate "
                f"observations for multi-label field_id={field_id!r} "
                f"instance_id={instance_id!r}",
            )
        if abstaining:
            normalized.append(
                SelectorObservationResponse(
                    instance_id=instance_id,
                    field_id=field_id,
                    evidence_id=None,
                    selected_candidate_ids=(),
                    abstain=True,
                    reason=abstaining[0].reason,
                ),
            )
            continue

        selected_ids = _dedupe_selected_ids(
            candidate_id
            for item in non_abstaining
            for candidate_id in item.selected_candidate_ids
        )
        normalized.append(
            SelectorObservationResponse(
                instance_id=instance_id,
                field_id=field_id,
                evidence_id=selected_ids[0] if selected_ids else None,
                selected_candidate_ids=selected_ids,
                abstain=False,
                reason=non_abstaining[0].reason if non_abstaining else None,
            ),
        )

    return BatchSelectorObservationResponse(observations=tuple(normalized))


def _dedupe_selected_ids(candidate_ids: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for candidate_id in candidate_ids:
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        out.append(candidate_id)
    return tuple(out)


def _normalized_evidence_id_for_selected_ids(
    response: SelectorObservationResponse,
    *,
    seam: str,
) -> str | None:
    selected = response.selected_candidate_ids
    evidence_id = response.evidence_id
    if evidence_id is None or evidence_id in selected:
        return evidence_id
    if len(selected) == 1:
        logger.warning(
            "extractx.selector.evidence_id_normalized",
            extra={
                "extractx_event": "selector.evidence_id_normalized",
                "seam": seam,
                "field_id": response.field_id,
                "instance_id": response.instance_id,
                "evidence_id": evidence_id,
                "selected_candidate_ids": selected,
            },
        )
        return selected[0]
    raise SelectorContractError(
        f"{seam} response failed Observation contract: evidence_id "
        f"{evidence_id!r} is not one of selected_candidate_ids={selected!r}; "
        f"raw_response={response.model_dump(mode='json')!r}",
    )


def _build_observation(
    *,
    outcome: str,
    selected_candidate_ids: tuple[str, ...],
    reason: str | None,
    producer_version: str,
    instance_id: str | None,
    field_id: str | None,
    evidence_id: str | None,
    abstain: bool,
    raw_response: SelectorObservationResponse,
    seam: str,
) -> Observation:
    try:
        return Observation(
            outcome=cast("Any", outcome),
            selected_candidate_ids=selected_candidate_ids,
            reason=reason,
            producer_version=producer_version,
            instance_id=instance_id,
            field_id=field_id,
            evidence_id=evidence_id,
            abstain=abstain,
        )
    except ValidationError as exc:
        raise SelectorContractError(
            f"{seam} response failed Observation contract: {exc}; "
            f"raw_response={raw_response.model_dump(mode='json')!r}; "
            f"evidence_id={raw_response.evidence_id!r}; "
            "selected_candidate_ids="
            f"{raw_response.selected_candidate_ids!r}",
        ) from exc


def _field_specs_by_id(spec: ExtractionSpec) -> dict[str, FieldSpec]:
    return {field_spec.field_id: field_spec for field_spec in spec.fields}


def _candidate_payload(
    candidate: Candidate,
    *,
    candidate_id: str | None = None,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id if candidate_id is not None else candidate.candidate_id,
        "text": candidate.text,
        "source_kind": candidate.source_kind,
        "source_id": candidate.source_id,
        "context": candidate.context,
        "context_span": candidate.context_span.model_dump(mode="json")
        if candidate.context_span is not None
        else None,
        "normalized_span": candidate.normalized_span.model_dump(mode="json")
        if candidate.normalized_span is not None
        else None,
        "entity_type": candidate.entity_type,
        "structured_payload_keys": sorted(candidate.structured_payload.keys())
        if candidate.structured_payload is not None
        else (),
        "structural_status": candidate.structural_status.model_dump(mode="json")
        if candidate.structural_status is not None
        else None,
        "evidence_span_count": len(candidate.evidence_spans),
    }


def _batch_prompt_field_id_map(
    spec_field_ids: tuple[str, ...],
    candidate_sets: tuple[CandidateSet, ...],
) -> dict[str, str]:
    width = max(3, len(str(len(spec_field_ids))))
    all_field_ids = {
        field_id: f"f{index:0{width}d}" for index, field_id in enumerate(spec_field_ids, start=1)
    }
    return {
        candidate_set.field_id: all_field_ids[candidate_set.field_id]
        for candidate_set in candidate_sets
    }


def _batch_prompt_candidate_id_maps(
    candidate_sets: tuple[CandidateSet, ...],
    *,
    prompt_field_id_map: Mapping[str, str],
) -> dict[str, dict[str, str]]:
    maps: dict[str, dict[str, str]] = {}
    for candidate_set in candidate_sets:
        width = max(3, len(str(len(candidate_set.candidates))))
        field_prefix = prompt_field_id_map[candidate_set.field_id]
        field_map: dict[str, str] = {}
        for index, candidate in enumerate(candidate_set.candidates, start=1):
            field_map[f"{field_prefix}_c{index:0{width}d}"] = candidate.candidate_id
        maps[candidate_set.field_id] = field_map
    return maps


def _translate_prompt_response_ids(
    response: SelectorObservationResponse,
    *,
    prompt_to_canonical: Mapping[str, str],
    seam: str,
) -> SelectorObservationResponse:
    if not prompt_to_canonical:
        return response
    if response.abstain:
        return response

    def translate(candidate_id: str) -> str:
        try:
            return prompt_to_canonical[candidate_id]
        except KeyError as exc:
            raise SelectorContractError(
                f"{seam} violated bounded evidence contract: returned prompt candidate id "
                f"{candidate_id!r}, allowed={sorted(prompt_to_canonical)!r}",
            ) from exc

    selected = tuple(translate(candidate_id) for candidate_id in response.selected_candidate_ids)
    evidence_id = translate(response.evidence_id) if response.evidence_id is not None else None
    return response.model_copy(
        update={
            "evidence_id": evidence_id,
            "selected_candidate_ids": selected,
        },
    )


def _translate_batch_prompt_response_ids(
    response: SelectorObservationResponse,
    *,
    prompt_field_id_map: Mapping[str, str],
    prompt_candidate_id_maps: Mapping[str, Mapping[str, str]],
    seam: str,
) -> SelectorObservationResponse:
    canonical_by_prompt_field = {
        prompt_field_id: canonical_field_id
        for canonical_field_id, prompt_field_id in prompt_field_id_map.items()
    }
    canonical_field_id = canonical_by_prompt_field.get(response.field_id, response.field_id)
    translated = _translate_prompt_response_ids(
        response,
        prompt_to_canonical=prompt_candidate_id_maps.get(canonical_field_id, {}),
        seam=seam,
    )
    if translated.field_id == canonical_field_id:
        return translated
    return translated.model_copy(update={"field_id": canonical_field_id})


def _render_batch_user_message(
    *,
    schema_version: str,
    schema_description: str | None,
    allowed_instance_ids: tuple[str, ...],
    fields: Sequence[Mapping[str, Any]],
    retry_feedback: tuple[str, ...] = (),
) -> str:
    """Render a readable batch selector prompt body.

    The hard bounded-id contract lives in the structured output schema and
    metadata. The prompt body presents the same candidate IDs only where the
    model needs them: attached to each candidate block.
    """

    lines: list[str] = [
        "<task>",
        "Choose bounded candidate IDs for each field. Do not write values.",
        "</task>",
        "",
        "<selection_procedure>",
        "1. Process each field block independently.",
        "2. Read that field's description and value_kind.",
        "3. Review only that field's candidate blocks and linked contexts.",
        "4. For cardinality one or optional, pick the single best matching candidate.",
        "5. For cardinality many, pick every candidate that satisfies the field description.",
        "6. If no bounded candidate satisfies the field, abstain for that field.",
        "7. Return chosen candidate IDs exactly as shown; never return value text.",
        "</selection_procedure>",
        "",
        "<output_rules>",
        "Return structured output only.",
        "Return exactly one observation for each field block.",
        "Return observations in the same order as the field blocks.",
        "Do not repeat a field_id.",
        (
            "field_id must be copied from <field id=\"...\">. "
            "Never use candidate prefixes like f001 as field_id."
        ),
        "Batch candidate ids are globally unique and start with a field-specific prefix.",
        (
            "For one/optional match: abstain=false, evidence_id=<candidate id>, "
            "selected_candidate_ids=[<candidate id>]."
        ),
        (
            "For many match: abstain=false, evidence_id may be null, "
            "selected_candidate_ids=[all matching candidate ids]."
        ),
        "For no match: abstain=true, evidence_id=null, selected_candidate_ids=[].",
        "For optional or nullable fields, abstain when no bounded candidate matches.",
        (
            "Invalid patterns: raw values as evidence_id, invented candidate ids, "
            "ids from another field, duplicate field observations, missing field observations, "
            '"None"/"N/A" as evidence_id.'
        ),
        "</output_rules>",
        "",
        "<output_example>",
        _batch_output_example(
            allowed_instance_ids=allowed_instance_ids,
            fields=fields,
        ),
        "</output_example>",
        "",
    ]
    if retry_feedback:
        lines.extend(("<retry_feedback>", *retry_feedback, "</retry_feedback>", ""))
    lines.extend(("<schema>", f"version: {schema_version}"))
    if schema_description:
        lines.extend(("description:", schema_description))
    lines.extend(
        (
            "</schema>",
            "",
            "<allowed_instance_ids>",
            ", ".join(allowed_instance_ids),
            "</allowed_instance_ids>",
            "",
            "<fields>",
        ),
    )
    for field_payload in fields:
        field = field_payload["field"]
        lines.extend(
            (
                f'<field id="{field["field_id"]}">',
                f"description: {field['description']}",
                f"value_kind: {field['value_kind']}",
                f"cardinality: {field['cardinality']}",
                f"python_type: {field['python_type']}",
            ),
        )
        document_context = field.get("document_context")
        if isinstance(document_context, str) and document_context:
            lines.extend(("<document_context>", document_context, "</document_context>"))
        classification_context = field_payload.get("classification_context")
        if isinstance(classification_context, tuple | list) and classification_context:
            lines.append("<classification_context>")
            typed_windows = cast("tuple[object, ...] | list[object]", classification_context)
            for raw_window in typed_windows:
                if not isinstance(raw_window, Mapping):
                    continue
                window = cast("Mapping[str, object]", raw_window)
                window_id = str(window.get("window_id", ""))
                rank = str(window.get("rank", ""))
                matched_terms = window.get("matched_terms", ())
                matched = (
                    ", ".join(
                        str(term)
                        for term in cast("tuple[object, ...] | list[object]", matched_terms)
                    )
                    if isinstance(matched_terms, tuple | list)
                    else ""
                )
                lines.extend(
                    (
                        (
                            f'<context_window id="{window_id}" '
                            f'rank="{rank}" matched_terms="{matched}">'
                        ),
                        str(window.get("text", "")),
                        "</context_window>",
                    ),
                )
            lines.append("</classification_context>")
        contexts = field_payload.get("contexts")
        if isinstance(contexts, dict) and contexts:
            lines.append("<contexts>")
            context_map = cast("Mapping[str, object]", contexts)
            for context_id, context in context_map.items():
                if isinstance(context, Mapping):
                    typed_context = cast("Mapping[str, object]", context)
                    lines.extend(
                        (
                            render_context_open_tag(context_id, typed_context),
                            str(typed_context["text"]),
                            "</context>",
                        ),
                    )
            lines.append("</contexts>")
        lines.append("<candidates>")
        for candidate in field_payload["candidates"]:
            lines.extend(render_candidate_lines(candidate))
        lines.extend(("</candidates>", "</field>", ""))
    lines.append("</fields>")
    return "\n".join(lines)


def _resolve_selector_prompt_assets(
    *,
    policy: SelectorPromptPolicy,
    resolver: object | None,
) -> tuple[tuple[SelectorDemoSet, ...], str | None]:
    if not policy.demo_refs and policy.instruction_ref is None:
        return (), None
    if resolver is None:
        raise InfrastructureError(
            "selector_prompt_assets.missing_resolver: selector prompt policy "
            "declares refs but Runtime.selector_prompt_assets is not set",
        )
    demo_sets = tuple(_resolve_demo_set(resolver, ref) for ref in policy.demo_refs)
    instruction = (
        None
        if policy.instruction_ref is None
        else _resolve_instruction(resolver, policy.instruction_ref)
    )
    return demo_sets, instruction


def _resolve_batch_selector_prompt_assets(
    *,
    field_ids: tuple[str, ...],
    policies: Mapping[str, SelectorPromptPolicy],
    resolver: object | None,
) -> tuple[dict[str, tuple[SelectorDemoSet, ...]], dict[str, str]]:
    demo_sets_by_field: dict[str, tuple[SelectorDemoSet, ...]] = {}
    instruction_by_field: dict[str, str] = {}
    for field_id in field_ids:
        policy = policies.get(field_id)
        if policy is None:
            continue
        demo_sets, instruction = _resolve_selector_prompt_assets(
            policy=policy,
            resolver=resolver,
        )
        if demo_sets:
            demo_sets_by_field[field_id] = demo_sets
        if instruction is not None:
            instruction_by_field[field_id] = instruction
    return demo_sets_by_field, instruction_by_field


def _resolve_demo_set(resolver: object, ref: str) -> SelectorDemoSet:
    resolve = getattr(resolver, "resolve_demo_set", None)
    if not callable(resolve):
        raise InfrastructureError(
            "selector_prompt_assets.invalid_resolver: resolver must expose "
            "resolve_demo_set(ref)",
        )
    raw = resolve(ref)
    if isinstance(raw, SelectorDemoSet):
        return raw
    return SelectorDemoSet.model_validate(raw)


def _resolve_instruction(resolver: object, ref: str) -> str:
    resolve = getattr(resolver, "resolve_instruction", None)
    if not callable(resolve):
        raise InfrastructureError(
            "selector_prompt_assets.invalid_resolver: resolver must expose "
            "resolve_instruction(ref)",
        )
    instruction = resolve(ref)
    if not isinstance(instruction, str):
        raise InfrastructureError(
            "selector_prompt_assets.invalid_instruction: resolve_instruction(ref) "
            "must return str",
        )
    return instruction


def _rendered_with_selector_prompt_assets(
    rendered: RenderedPrompt,
    *,
    demo_sets: tuple[SelectorDemoSet, ...],
    instruction: str | None,
) -> RenderedPrompt:
    if not demo_sets and instruction is None:
        return rendered
    messages = tuple(
        _message_with_selector_prompt_assets(
            message,
            demo_sets=demo_sets,
            instruction=instruction,
        )
        if message.role == "user"
        else message
        for message in rendered.messages
    )
    return rendered.model_copy(update={"messages": messages})


def _message_with_selector_prompt_assets(
    message: Message,
    *,
    demo_sets: tuple[SelectorDemoSet, ...],
    instruction: str | None,
) -> Message:
    blocks: list[str] = []
    if instruction is not None:
        blocks.extend(("<selector_instruction>", instruction, "</selector_instruction>", ""))
    if demo_sets:
        blocks.append(_render_selector_demo_sets(demo_sets))
        blocks.append("")
    blocks.append(message.content)
    return message.model_copy(update={"content": "\n".join(blocks)})


def _user_message_with_batch_selector_prompt_assets(
    user_message: str,
    *,
    demo_sets_by_field: Mapping[str, tuple[SelectorDemoSet, ...]],
    instruction_by_field: Mapping[str, str],
) -> str:
    if not demo_sets_by_field and not instruction_by_field:
        return user_message
    lines: list[str] = []
    if instruction_by_field:
        lines.append("<selector_instructions>")
        for field_id, instruction in instruction_by_field.items():
            lines.extend((f'<instruction field_id="{field_id}">', instruction, "</instruction>"))
        lines.extend(("</selector_instructions>", ""))
    if demo_sets_by_field:
        lines.append("<selector_worked_examples>")
        for field_id, demo_sets in demo_sets_by_field.items():
            lines.append(f'<field_examples field_id="{field_id}">')
            lines.append(_render_selector_demo_sets(demo_sets))
            lines.append("</field_examples>")
        lines.extend(("</selector_worked_examples>", ""))
    lines.append(user_message)
    return "\n".join(lines)


def _render_selector_demo_sets(demo_sets: tuple[SelectorDemoSet, ...]) -> str:
    lines: list[str] = ["<selector_worked_examples>"]
    for demo_set in demo_sets:
        lines.append(
            f'<demo_set id="{demo_set.demo_set_id}" version="{demo_set.version}">',
        )
        for index, demo in enumerate(demo_set.demos, start=1):
            lines.extend(_render_selector_demo(demo, index=index))
        lines.append("</demo_set>")
    lines.append("</selector_worked_examples>")
    return "\n".join(lines)


def _render_selector_demo(demo: SelectorDemo, *, index: int) -> list[str]:
    candidate_payload: list[dict[str, object]] = [
        _candidate_payload(candidate, candidate_id=candidate.candidate_id)
        for candidate in demo.candidate_set.candidates
    ]
    rendered_candidates, contexts = intern_prompt_contexts(candidate_payload)
    lines: list[str] = [
        f'<demo index="{index}" field_id="{demo.field_id}">',
    ]
    if demo.note:
        lines.extend(("<note>", demo.note, "</note>"))
    lines.extend(("<document_context>", demo.document_context, "</document_context>"))
    if contexts:
        lines.append("<contexts>")
        for context_id, context in contexts.items():
            typed_context = cast("Mapping[str, object]", context)
            lines.extend(
                (
                    render_context_open_tag(context_id, typed_context),
                    str(typed_context["text"]),
                    "</context>",
                ),
            )
        lines.append("</contexts>")
    lines.append("<candidates>")
    for candidate in rendered_candidates:
        lines.extend(render_candidate_lines(candidate))
    lines.extend(
        (
            "</candidates>",
            "<expected_observation>",
            _expected_observation_json(demo.expected),
            "</expected_observation>",
            "</demo>",
        ),
    )
    return lines


def _expected_observation_json(expected: ExpectedObservation) -> str:
    return json.dumps(
        {
            "evidence_id": expected.evidence_id,
            "selected_candidate_ids": list(expected.selected_candidate_ids),
            "abstain": expected.abstain,
        },
        separators=(",", ":"),
    )


def _demo_set_hash(demo_set: SelectorDemoSet) -> str:
    return stable_hash(demo_set.model_dump(mode="json"))


def _batch_output_example(
    *,
    allowed_instance_ids: tuple[str, ...],
    fields: Sequence[Mapping[str, Any]],
) -> str:
    instance_id = allowed_instance_ids[0] if allowed_instance_ids else "inst_0"
    observations: list[dict[str, Any]] = []
    for index, field_payload in enumerate(fields):
        field = field_payload["field"]
        candidates = field_payload.get("candidates")
        first_candidate_id: str | None = None
        if isinstance(candidates, Sequence) and not isinstance(candidates, str):
            typed_candidates = cast("Sequence[object]", candidates)
            for candidate in typed_candidates:
                if isinstance(candidate, Mapping):
                    typed_candidate = cast("Mapping[str, object]", candidate)
                    candidate_id = typed_candidate.get("candidate_id")
                    if isinstance(candidate_id, str):
                        first_candidate_id = candidate_id
                        break
        if index == 1 or first_candidate_id is None:
            observations.append(
                {
                    "instance_id": instance_id,
                    "field_id": field["field_id"],
                    "evidence_id": None,
                    "selected_candidate_ids": [],
                    "abstain": True,
                    "reason": "no bounded candidate matches",
                },
            )
            continue
        observations.append(
            {
                "instance_id": instance_id,
                "field_id": field["field_id"],
                "evidence_id": first_candidate_id,
                "selected_candidate_ids": [first_candidate_id],
                "abstain": False,
                "reason": f"{first_candidate_id} matches the field",
            },
        )
    return json.dumps({"observations": observations}, separators=(",", ":"))


def _qualname(obj: object) -> str:
    module = getattr(obj, "__module__", None)
    qualname = getattr(obj, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return f"{module}.{qualname}"
    return repr(obj)


def _classification_context_by_field_payload(
    context_pack: ContextPack,
) -> Mapping[str, object]:
    return {
        field_id: context_set.model_dump(mode="json")
        for field_id, context_set in context_pack.classification_context_by_field.items()
    }


def _classification_context_for_field_payload(
    context_pack: ContextPack,
    field_id: str,
) -> tuple[Mapping[str, object], ...]:
    context_set = context_pack.classification_context_by_field.get(field_id)
    if context_set is None:
        return ()
    return tuple(
        {
            "window_id": window.window_id,
            "text": window.text,
            "source_kind": window.source_kind,
            "source_id": window.source_id,
            "source_span": window.source_span.model_dump(mode="json"),
            "matched_terms": window.matched_terms,
            "rank": window.rank,
            "metadata": dict(window.metadata),
        }
        for window in context_set.windows
    )


def _validate_field_alignment(field_spec: FieldSpec, candidate_set: CandidateSet) -> None:
    if candidate_set.field_id != field_spec.field_id:
        raise SelectorContractError(
            "selector field mismatch: candidate_set.field_id "
            f"{candidate_set.field_id!r} != field_spec.field_id {field_spec.field_id!r}",
        )


def _is_literal_set_category_field(field_spec: FieldSpec) -> bool:
    if field_spec.value_kind.name != "CATEGORY" or not field_spec.literal_values:
        return False
    for binding in field_spec.strategy_bindings:
        if binding.kind != "candidate":
            continue
        cls = binding.cls
        if cls is LiteralSetCandidateStrategy or issubclass(cls, LiteralSetCandidateStrategy):
            return True
    return False


def _bounded_output_schema(
    *,
    field_id: str,
    instance_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
) -> Mapping[str, Any]:
    schema: dict[str, Any] = SelectorObservationResponse.model_json_schema()
    raw_properties = schema.get("properties")
    if isinstance(raw_properties, dict):
        properties = cast("dict[str, Any]", raw_properties)
        instance_property = properties.get("instance_id")
        if isinstance(instance_property, dict):
            instance_property["enum"] = list(instance_ids)
        field_property = properties.get("field_id")
        if isinstance(field_property, dict):
            field_property["enum"] = [field_id]
        evidence_property = properties.get("evidence_id")
        if isinstance(evidence_property, dict):
            evidence_property["anyOf"] = [
                {"enum": list(evidence_ids)},
                {"type": "null"},
            ]
        selected_property = properties.get("selected_candidate_ids")
        if isinstance(selected_property, dict):
            selected_property["items"] = {"enum": list(evidence_ids)}
    return schema


def _batch_output_schema(
    *,
    field_ids: tuple[str, ...],
    instance_ids: tuple[str, ...],
    evidence_ids_by_field: Mapping[str, tuple[str, ...]],
) -> Mapping[str, Any]:
    schema: dict[str, Any] = BatchSelectorObservationResponse.model_json_schema()
    raw_defs = schema.get("$defs")
    if isinstance(raw_defs, dict):
        defs = cast("dict[str, Any]", raw_defs)
        response_schema = defs.get("SelectorObservationResponse")
        if isinstance(response_schema, dict):
            raw_properties = schema.get("properties")
            if isinstance(raw_properties, dict):
                properties = cast("dict[str, Any]", raw_properties)
                observations_property = properties.get("observations")
                if isinstance(observations_property, dict):
                    observations_property["minItems"] = len(field_ids)
                    observations_property["maxItems"] = len(field_ids)
                    observations_property["prefixItems"] = [
                        _batch_observation_item_schema(
                            response_schema=cast("Mapping[str, Any]", response_schema),
                            field_id=field_id,
                            instance_ids=instance_ids,
                            evidence_ids=evidence_ids_by_field[field_id],
                        )
                        for field_id in field_ids
                    ]
    return schema


def _batch_observation_item_schema(
    *,
    response_schema: Mapping[str, Any],
    field_id: str,
    instance_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
) -> Mapping[str, Any]:
    item_schema = deepcopy(dict(response_schema))
    raw_properties = item_schema.get("properties")
    if isinstance(raw_properties, dict):
        properties = cast("dict[str, Any]", raw_properties)
        instance_property = properties.get("instance_id")
        if isinstance(instance_property, dict):
            instance_property["enum"] = list(instance_ids)
        field_property = properties.get("field_id")
        if isinstance(field_property, dict):
            field_property["enum"] = [field_id]
        evidence_property = properties.get("evidence_id")
        if isinstance(evidence_property, dict):
            evidence_property["anyOf"] = [
                {"enum": list(evidence_ids)},
                {"type": "null"},
            ]
        selected_property = properties.get("selected_candidate_ids")
        if isinstance(selected_property, dict):
            selected_property["items"] = {"enum": list(evidence_ids)}
    return item_schema


def _enforce_observation_contract(
    *,
    response: SelectorObservationResponse,
    field_spec: FieldSpec,
    candidate_set: CandidateSet,
    instance_ids: tuple[str, ...],
) -> None:
    if response.field_id != field_spec.field_id:
        raise SelectorContractError(
            "selector violated bounded field contract: returned field_id "
            f"{response.field_id!r}, expected {field_spec.field_id!r}",
        )
    if response.instance_id not in set(instance_ids):
        raise SelectorContractError(
            "selector violated bounded instance contract: returned instance_id "
            f"{response.instance_id!r}, allowed={list(instance_ids)!r}",
        )
    candidate_ids = {candidate.candidate_id for candidate in candidate_set.candidates}
    stray_ids = [cid for cid in response.selected_candidate_ids if cid not in candidate_ids]
    if stray_ids:
        raise SelectorContractError(
            "selector violated bounded evidence contract: returned selected_candidate_ids "
            f"{stray_ids!r}, allowed={sorted(candidate_ids)!r}",
        )
    if response.evidence_id is not None and response.evidence_id not in candidate_ids:
        raise SelectorContractError(
            "selector violated bounded evidence contract: returned evidence_id "
            f"{response.evidence_id!r}, allowed={sorted(candidate_ids)!r}",
        )
    if response.abstain and (response.evidence_id is not None or response.selected_candidate_ids):
        raise SelectorOutputMalformedError(
            "selector.output_malformed: abstain=True requires no selected ids",
        )
    if (
        not response.abstain
        and not response.selected_candidate_ids
        and field_spec.cardinality is not Cardinality.MANY
    ):
        raise SelectorOutputMalformedError(
            "selector.output_malformed: abstain=False requires bounded selected ids",
        )
