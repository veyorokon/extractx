"""`IndependentStrategy` per docs/architecture.md §11. internal for v1.

phase-1 (M8 vertical slice) wires the landed seams in declaration order
for one narrow supported path:

- seam A: `TextAdapter` (already adapted to a `DocumentView` by
  `SerialExecutor` before the strategy runs).
- seam C: `RegexCandidateStrategy.generate(...)`.
- seam D: `SingletonSelector.select(...)`.
- seam E: `CardinalitySelectionAdapter.adapt(...)`.
- seam F (layers 1+2): `LayeredProposalValidator.validate(...,
  schema_cls=...)`.
- seam G.resolver (after the per-field loop):
  `DeterministicInstanceResolver.resolve(...)`.

phase-1 discipline (per the brief):

- one executor (`SerialExecutor`), one strategy (this).
- per-field iteration order is `spec.fields` declaration order.
- `_build_independent_context_pack(spec, field_spec)` is the **only**
  `ContextPack` construction site for this slice; no bare
  `ContextPack()` calls. the deterministic shape:
  - `schema_description = spec.source_schema_ref.ref if present else ""`
  - `document_summary = ""`
  - `field_context = {field_spec.field_id: field_spec.description}`
  - `prior_proposals = ()`
  - `retry_feedback = ()`
  - `bounds = ContextBudget()`
  - `candidate_overflow = None`
- LLM-backed selector/proposer calls may emit `UsageEvent`s; the strategy
  records those events on `Runtime.budget` and returns the same ordered tuple
  to the executor for `Extraction` / replay capture. algorithmic seams emit no
  usage events.
- no `Reporter` invocation from inside the strategy; phase-1 trace
  assembly is executor-owned.
- `instance_hint=None` and `instance_state=None` everywhere;
  `IndependentStrategy` does not maintain or pass `InstanceState`.
- `instance_plan=None` to the resolver; `G.planner` is not wired in
  this slice.

`ValidationFailure` routing under `ExecutorPolicy.on_validation_failure
== "fail"` (the only phase-1 policy value):

- every `ValidationFailure(layer="field", field_id=<id>, reason=<r>,
  ...)` from seam F is escalated immediately to a typed
  `NegativeOutcome(category="validation", code="field_failure",
  field_id=<id>, instance_key=None, reason=<r>, candidate_count=None)`.
- the escalated negative joins the pre-resolver negative list in field
  order.
- there is no retry loop and no conversion back into
  `ValidationFailure` after escalation.

attachment of pre-resolver negatives is the executor's job, not the
strategy's. the strategy returns `StrategyOutput` carrying
`(validated_fields, candidate_sets, pre_resolver_negatives,
final_instances)`; the executor builds the final `Extraction` and
merges negatives into the sole returned instance.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel

from extractx.candidates import DeterministicSelectionGate
from extractx.candidates.candidate_set import build_candidate_set
from extractx.candidates.filters import apply_filter_binding
from extractx.candidates.generators.literal_set import LiteralSetCandidateStrategy
from extractx.candidates.generators.ner import NerCandidateStrategy
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.cardinality import Cardinality
from extractx.core.contracts import BatchSelector, InstanceProposer, Selector
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import (
    Candidate,
    CandidateSet,
    ClassificationContextSet,
    ContextBudget,
    ContextPack,
    DocumentView,
    ExtractionSpec,
    FieldSpec,
    InstanceCandidate,
    InstanceCandidateSet,
    InstanceGroupingKey,
    InstancePlan,
    InstanceProposerResponse,
    Observation,
    RenderedPrompt,
    UsageEvent,
)
from extractx.core.outcomes import (
    Instance,
    NegativeOutcome,
    ProposedField,
    ValidatedField,
    ValidationFailure,
)
from extractx.core.versions import stable_hash
from extractx.execution.deferred import (
    SoftCallRequest,
    SoftCallResponse,
    SoftCallRouting,
)
from extractx.execution.runtime import Runtime
from extractx.execution.selector_planner import (
    BatchSelectorCallPlan,
    BudgetedBatchSelectorPlanner,
    DocumentWindow,
    DocumentWindowSelectorTaskPlan,
    SelectorPlan,
    SelectorTask,
    ShardedSelectorTaskPlan,
    candidate_set_view,
)
from extractx.extras.pydantic_ai import (
    LLMInstanceProposer,
    PydanticAIBatchSelector,
    PydanticAISelector,
)
from extractx.instances.proposer import (
    build_instance_candidate_set,
    candidate_set_for_instance,
    enforce_instance_proposer_contract,
    instance_plan_from_response,
)
from extractx.instances.resolvers.deterministic import DeterministicInstanceResolver
from extractx.proposals.adapter import CardinalitySelectionAdapter
from extractx.proposals.validation import LayeredProposalValidator
from extractx.replay.diagnostics import SelectorCallDiagnostic
from extractx.selection.algorithmic.singleton import SingletonSelector
from extractx.selection.examples import (
    DocumentClassificationReducerPolicy,
    SelectorPromptPolicy,
)
from extractx.selection.prompts import ClassificationPrompt
from extractx.selection.selector import enforce_batch_observation_contract

__all__ = ["IndependentStrategy", "_build_independent_context_pack"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StrategyOutput:
    """internal aggregate handed back from `IndependentStrategy.run(...)`.

    the executor consumes this to assemble the final `Extraction`.
    `observations` is one entry per consumed `CandidateSet` (i.e. seam D
    invocation count); the executor uses it to populate
    `ReplayArtifact.observations` without re-running seam D at write
    time.
    """

    candidate_sets: tuple[CandidateSet, ...]
    observations: tuple[Observation, ...]
    validated_fields: tuple[ValidatedField, ...]
    pre_resolver_negatives: tuple[NegativeOutcome, ...]
    final_instances: tuple[Instance, ...]
    instance_candidate_set: InstanceCandidateSet | None = None
    instance_proposer_response: InstanceProposerResponse | None = None
    instance_proposer_metadata: Mapping[str, object] | None = None
    usage_events: tuple[UsageEvent, ...] = ()
    selector_call_diagnostics: tuple[SelectorCallDiagnostic, ...] = ()


def _build_independent_context_pack(
    spec: ExtractionSpec,
    field_spec: FieldSpec,
    *,
    document_view: DocumentView | None = None,
    retry_feedback: tuple[str, ...] = (),
    classification_context_by_field: Mapping[str, ClassificationContextSet] | None = None,
) -> ContextPack:
    """construct the phase-1 `ContextPack` for the independent strategy.

    deterministic, explicit, no defaults left implicit. callers must
    not bypass this helper with bare `ContextPack()` instantiation
    inside the slice — anti-pattern §15 "Silent None" / "Duplicate
    Overlapping Path".

    `schema_description` is the spec's `source_schema_ref.ref` when
    present (a stable `module.qualname` string for pydantic-backed
    specs) and `""` for manual specs that did not register a schema
    reference. this matches the brief's worker-latitude allowance.
    """

    schema_description = spec.source_schema_ref.ref if spec.source_schema_ref is not None else ""
    return ContextPack(
        schema_description=schema_description,
        document_summary="" if document_view is None else document_view.normalized_text,
        field_context={field_spec.field_id: field_spec.description},
        prior_proposals=(),
        retry_feedback=retry_feedback,
        bounds=ContextBudget(),
        candidate_overflow=None,
        classification_context_by_field=dict(classification_context_by_field or {}),
    )


def _build_batch_context_pack(
    spec: ExtractionSpec,
    field_specs: tuple[FieldSpec, ...],
    *,
    document_view: DocumentView | None = None,
    document_summary: str | None = None,
    retry_feedback: tuple[str, ...] = (),
    classification_context_by_field: Mapping[str, ClassificationContextSet] | None = None,
) -> ContextPack:
    schema_description = spec.source_schema_ref.ref if spec.source_schema_ref is not None else ""
    if document_summary is None:
        document_summary = "" if document_view is None else document_view.normalized_text
    return ContextPack(
        schema_description=schema_description,
        document_summary=document_summary,
        field_context={field_spec.field_id: field_spec.description for field_spec in field_specs},
        prior_proposals=(),
        retry_feedback=retry_feedback,
        bounds=ContextBudget(),
        candidate_overflow=None,
        classification_context_by_field=dict(classification_context_by_field or {}),
    )


def _classification_contexts_for_fields(
    *,
    field_specs: tuple[FieldSpec, ...],
    document_view: DocumentView,
    runtime: Runtime,
) -> Mapping[str, ClassificationContextSet]:
    out: dict[str, ClassificationContextSet] = {}
    for field_spec in field_specs:
        policy = runtime.selector_prompt_policies.get(field_spec.field_id)
        if policy is None or policy.classification_context_binding is None:
            continue
        if policy.document_context_mode != "classification_context":
            continue
        if field_spec.value_kind.name != "CATEGORY":
            raise InfrastructureError(
                "classification_context.invalid_policy: field "
                f"{field_spec.field_id!r} is not ValueKind.CATEGORY",
            )
        binding = policy.classification_context_binding
        strategy = binding.cls(**dict(binding.params))
        generate = getattr(strategy, "generate", None)
        if not callable(generate):
            raise InfrastructureError(
                "classification_context.invalid_strategy: strategy must expose "
                "generate(field_spec, document_view)",
            )
        context_set = generate(field_spec, document_view)
        if not isinstance(context_set, ClassificationContextSet):
            context_set = ClassificationContextSet.model_validate(context_set)
        if context_set.field_id != field_spec.field_id:
            raise InfrastructureError(
                "classification_context.field_mismatch: strategy returned "
                f"field_id={context_set.field_id!r} for field {field_spec.field_id!r}",
            )
        out[field_spec.field_id] = context_set
    return out


def _selection_gate_diagnostic(
    *,
    document_view: DocumentView,
    spec: ExtractionSpec,
    field_spec: FieldSpec,
    candidate_set: CandidateSet,
    observation: Observation,
) -> SelectorCallDiagnostic:
    decision_kind = "no_candidates" if observation.outcome == "NO_CANDIDATES" else "auto_selected"
    presented_ids = tuple(candidate.candidate_id for candidate in candidate_set.candidates)
    return SelectorCallDiagnostic(
        seam="selection_gate",
        decision_kind=decision_kind,
        document_id=document_view.document_id,
        spec_version=spec.version,
        field_ids=(field_spec.field_id,),
        instance_ids=tuple(
            instance_id for instance_id in (observation.instance_id,) if instance_id is not None
        ),
        candidate_count_by_field={field_spec.field_id: len(candidate_set.candidates)},
        presented_candidate_ids_by_field={field_spec.field_id: presented_ids},
        presented_count_by_field={field_spec.field_id: len(presented_ids)},
        allowed_evidence_ids_by_field={field_spec.field_id: presented_ids},
        final_observations=(observation,),
        model_metadata={"producer_version": _selection_gate_producer_version()},
    )


def _selection_gate_producer_version() -> str:
    return DeterministicSelectionGate().producer_version


def _selector_call_diagnostic(
    *,
    selector: object,
    document_view: DocumentView,
    spec: ExtractionSpec,
    candidate_sets: tuple[CandidateSet, ...],
    observations: tuple[Observation, ...],
    seam: Literal["selector", "batch_selector"],
    decision_kind: Literal["llm", "no_candidates", "shard_reducer"],
    batch_index: int | None = None,
    batch_count: int | None = None,
    shard_index: int | None = None,
    shard_count: int | None = None,
    reducer_round: int | None = None,
    estimated_prompt_chars: int | None = None,
    max_prompt_chars: int | None = None,
    routing: SoftCallRouting | None = None,
) -> SelectorCallDiagnostic:
    raw_payload = getattr(selector, "last_call_diagnostic", None)
    payload: Mapping[str, object] = (
        cast("Mapping[str, object]", raw_payload) if isinstance(raw_payload, Mapping) else {}
    )
    candidate_count_by_field = {
        candidate_set.field_id: len(candidate_set.candidates) for candidate_set in candidate_sets
    }
    presented_candidate_ids_by_field = {
        candidate_set.field_id: tuple(
            candidate.candidate_id for candidate in candidate_set.candidates
        )
        for candidate_set in candidate_sets
    }
    prompt_maps = _prompt_candidate_maps_by_field(
        payload=payload,
        candidate_sets=candidate_sets,
    )
    allowed = _allowed_evidence_ids_by_field(
        payload=payload,
        candidate_sets=candidate_sets,
    )
    metadata = payload.get("model_metadata")
    if not isinstance(metadata, Mapping):
        metadata = {
            "model_id": getattr(selector, "model_id", None),
            "producer_version": _selector_producer_version(selector),
        }
    usage_event = payload.get("usage_event")
    return SelectorCallDiagnostic(
        seam=seam,
        decision_kind=decision_kind,
        document_id=document_view.document_id,
        spec_version=spec.version,
        field_ids=tuple(candidate_set.field_id for candidate_set in candidate_sets),
        instance_ids=_observation_instance_ids(observations),
        batch_index=batch_index,
        batch_count=batch_count,
        shard_index=(
            shard_index if shard_index is not None else getattr(routing, "shard_index", None)
        ),
        shard_count=(
            shard_count if shard_count is not None else getattr(routing, "shard_count", None)
        ),
        window_index=getattr(routing, "window_index", None),
        window_count=getattr(routing, "window_count", None),
        reducer_round=(
            reducer_round if reducer_round is not None else getattr(routing, "reducer_round", None)
        ),
        candidate_count_by_field=candidate_count_by_field,
        presented_candidate_ids_by_field=presented_candidate_ids_by_field,
        presented_count_by_field={
            field_id: len(candidate_ids)
            for field_id, candidate_ids in presented_candidate_ids_by_field.items()
        },
        allowed_evidence_ids_by_field=allowed,
        prompt_candidate_id_map_by_field=prompt_maps,
        prompt_field_id_map=_string_mapping(payload.get("prompt_field_id_map")),
        classification_context_by_field=_object_mapping(
            payload.get("classification_context_by_field"),
        ),
        category_signals=_mapping_tuple(payload.get("category_signals")),
        rendered_prompt_hash=_optional_str(payload.get("rendered_prompt_hash")),
        rendered_prompt_ref=_optional_str(payload.get("rendered_prompt_ref")),
        estimated_prompt_chars=estimated_prompt_chars,
        max_prompt_chars=max_prompt_chars,
        selector_response_before_translation_hash=_optional_str(
            payload.get("selector_response_before_translation_hash"),
        ),
        selector_response_before_translation_ref=_optional_str(
            payload.get("selector_response_before_translation_ref"),
        ),
        selector_response_after_translation_hash=_optional_str(
            payload.get("selector_response_after_translation_hash"),
        ),
        selector_response_after_translation_ref=_optional_str(
            payload.get("selector_response_after_translation_ref"),
        ),
        final_observations=observations,
        usage_event=usage_event if isinstance(usage_event, UsageEvent) else None,
        model_metadata=cast("Mapping[str, object]", metadata),
    )


def _prompt_candidate_maps_by_field(
    *,
    payload: Mapping[str, object],
    candidate_sets: tuple[CandidateSet, ...],
) -> Mapping[str, Mapping[str, str]]:
    by_field = payload.get("prompt_candidate_id_map_by_field")
    if isinstance(by_field, Mapping):
        typed_by_field = cast("Mapping[object, object]", by_field)
        return {
            str(field_id): _string_mapping(cast("Mapping[object, object]", mapping))
            for field_id, mapping in typed_by_field.items()
            if isinstance(mapping, Mapping)
        }
    single = payload.get("prompt_candidate_id_map")
    if isinstance(single, Mapping) and len(candidate_sets) == 1:
        return {
            candidate_sets[0].field_id: _string_mapping(
                cast("Mapping[object, object]", single),
            ),
        }
    return {}


def _allowed_evidence_ids_by_field(
    *,
    payload: Mapping[str, object],
    candidate_sets: tuple[CandidateSet, ...],
) -> Mapping[str, tuple[str, ...]]:
    by_field = payload.get("allowed_evidence_ids_by_field")
    if isinstance(by_field, Mapping):
        typed_by_field = cast("Mapping[object, object]", by_field)
        return {
            str(field_id): _string_tuple(value)
            for field_id, value in typed_by_field.items()
        }
    single = payload.get("allowed_evidence_ids")
    if single is not None and len(candidate_sets) == 1:
        return {candidate_sets[0].field_id: _string_tuple(single)}
    return {
        candidate_set.field_id: tuple(
            candidate.candidate_id for candidate in candidate_set.candidates
        )
        for candidate_set in candidate_sets
    }


def _observation_instance_ids(observations: tuple[Observation, ...]) -> tuple[str, ...]:
    seen: list[str] = []
    for observation in observations:
        if observation.instance_id is not None and observation.instance_id not in seen:
            seen.append(observation.instance_id)
    return tuple(seen)


def _string_mapping(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return {}
    typed_value = cast("Mapping[object, object]", value)
    return {
        str(key): str(item)
        for key, item in typed_value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _object_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return cast("Mapping[str, object]", value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, tuple | list):
        typed_value = cast("tuple[object, ...] | list[object]", value)
        return tuple(str(item) for item in typed_value if isinstance(item, str))
    return ()


def _mapping_tuple(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, tuple | list):
        return ()
    typed_value = cast("tuple[object, ...] | list[object]", value)
    out: list[Mapping[str, object]] = []
    for item in typed_value:
        if isinstance(item, Mapping):
            out.append(cast("Mapping[str, object]", item))
    return tuple(out)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class IndependentStrategy:
    """phase-1 internal independent strategy.

    composes seams C → D → E → F → G.resolver in declaration order.
    holds no configurable state; instances are interchangeable for the
    same `(spec, document, schema_cls)`.
    """

    def __init__(self) -> None:
        # seam impls are stateless / pure; constructing once per
        # strategy instance keeps the call surface tight without
        # leaking pooled state across runs.
        self._selector = SingletonSelector()
        self._selection_gate = DeterministicSelectionGate()
        self._selection_adapter = CardinalitySelectionAdapter()
        self._validator = LayeredProposalValidator()
        self._resolver = DeterministicInstanceResolver()

    def run(
        self,
        document_view: DocumentView,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None,
        runtime: Runtime,
        *,
        batch_select: bool = False,
    ) -> StrategyOutput:
        """run the independent strategy and return per-stage aggregates.

        `schema_cls` is resolved once by the executor from the
        in-process schema-cls registry (keyed by `spec.version`) and
        threaded into every seam-F call. manual specs receive
        `schema_cls=None` and seam F dispatches to its
        `ValidationBinding` path.
        """

        candidate_sets: list[CandidateSet] = []
        observations: list[Observation] = []
        validated_fields: list[ValidatedField] = []
        pre_resolver_negatives: list[NegativeOutcome] = []
        usage_events: list[UsageEvent] = []
        selector_call_diagnostics: list[SelectorCallDiagnostic] = []

        for field_spec in spec.fields:
            self._assert_supported_field(field_spec)
            generated_sets = tuple(
                binding.cls().generate(
                    field_spec=field_spec.model_copy(
                        update={"strategy_bindings": (binding,)},
                    ),
                    document_view=document_view,
                    instance_hint=None,
                )
                for binding in field_spec.strategy_bindings
            )
            candidate_set = _merge_candidate_sets(
                field_spec=field_spec,
                document_id=document_view.document_id,
                candidate_sets=generated_sets,
            )
            generated_count = len(candidate_set.candidates)
            logger.info(
                "extractx.candidates.generated",
                extra={
                    "extractx_event": "candidates.generated",
                    "document_id": document_view.document_id,
                    "spec_version": spec.version,
                    "field_id": field_spec.field_id,
                    "candidate_count": generated_count,
                    "strategy_id": candidate_set.strategy_id,
                    "strategy_count": len(field_spec.strategy_bindings),
                },
            )
            if field_spec.filter_binding is not None:
                candidate_set = apply_filter_binding(
                    candidate_set=candidate_set,
                    binding=field_spec.filter_binding,
                )
                logger.info(
                    "extractx.candidates.filtered",
                    extra={
                        "extractx_event": "candidates.filtered",
                        "document_id": document_view.document_id,
                        "spec_version": spec.version,
                        "field_id": field_spec.field_id,
                        "input_candidate_count": generated_count,
                        "output_candidate_count": len(candidate_set.candidates),
                    },
                )
            candidate_sets.append(candidate_set)

        instance_candidate_set: InstanceCandidateSet | None = None
        instance_response: InstanceProposerResponse | None = None
        instance_proposer_metadata: Mapping[str, object] | None = None
        instance_plan: InstancePlan | None = None
        selected_instance_ids: tuple[str, ...] = ("inst_0",)
        instance_candidates_by_id: dict[str, InstanceCandidate] = {}
        instance_keys_by_id: dict[str, InstanceGroupingKey] = {}

        if spec.instance_cardinality is Cardinality.MANY:
            instance_candidate_set = build_instance_candidate_set(
                document_view=document_view,
                spec=spec,
                candidate_sets=tuple(candidate_sets),
            )
            proposer = self._instance_proposer_for_spec(spec, runtime)
            logger.info(
                "extractx.instance_proposer.started",
                extra={
                    "extractx_event": "instance_proposer.started",
                    "document_id": document_view.document_id,
                    "spec_version": spec.version,
                    "candidate_count": len(instance_candidate_set.candidates),
                    "operation": "instance_proposer",
                    "model_id": getattr(proposer, "model_id", None),
                },
            )
            instance_response = proposer.propose(
                document_view=document_view,
                spec=spec,
                candidate_set=instance_candidate_set,
            )
            logger.info(
                "extractx.instance_proposer.completed",
                extra={
                    "extractx_event": "instance_proposer.completed",
                    "document_id": document_view.document_id,
                    "spec_version": spec.version,
                    "candidate_count": len(instance_candidate_set.candidates),
                    "selected_count": len(instance_response.selected_instance_ids),
                    "operation": "instance_proposer",
                    "model_id": getattr(proposer, "model_id", None),
                },
            )
            raw_metadata = getattr(proposer, "last_metadata", None)
            if isinstance(raw_metadata, Mapping):
                instance_proposer_metadata = cast("Mapping[str, object]", raw_metadata)
            self._record_usage_event(
                getattr(proposer, "last_usage_event", None),
                runtime=runtime,
                usage_events=usage_events,
            )
            instance_response = enforce_instance_proposer_contract(
                instance_response,
                instance_candidate_set,
            )
            instance_plan = instance_plan_from_response(
                candidate_set=instance_candidate_set,
                response=instance_response,
                producer_version=getattr(proposer, "producer_version", None),
            )
            selected_instance_ids = instance_response.selected_instance_ids
            instance_candidates_by_id = {
                candidate.instance_id: candidate for candidate in instance_candidate_set.candidates
            }
            instance_keys_by_id = {key.group_id: key for key in instance_plan.tentative_keys}

        if batch_select and spec.instance_cardinality is Cardinality.ONE:
            self._select_validate_batch(
                document_view=document_view,
                spec=spec,
                schema_cls=schema_cls,
                runtime=runtime,
                candidate_sets=tuple(candidate_sets),
                observations=observations,
                validated_fields=validated_fields,
                pre_resolver_negatives=pre_resolver_negatives,
                usage_events=usage_events,
                selector_call_diagnostics=selector_call_diagnostics,
            )
        else:
            self._select_validate_independent(
                document_view=document_view,
                spec=spec,
                schema_cls=schema_cls,
                runtime=runtime,
                candidate_sets=tuple(candidate_sets),
                selected_instance_ids=selected_instance_ids,
                instance_candidates_by_id=instance_candidates_by_id,
                instance_keys_by_id=instance_keys_by_id,
                observations=observations,
                validated_fields=validated_fields,
                pre_resolver_negatives=pre_resolver_negatives,
                usage_events=usage_events,
                selector_call_diagnostics=selector_call_diagnostics,
            )

        # seam G.resolver — deterministic instance resolution.
        final_instances = self._resolver.resolve(
            validated_fields=tuple(validated_fields),
            candidate_sets=tuple(candidate_sets),
            spec=spec,
            instance_plan=instance_plan,
        )

        return StrategyOutput(
            candidate_sets=tuple(candidate_sets),
            observations=tuple(observations),
            validated_fields=tuple(validated_fields),
            pre_resolver_negatives=tuple(pre_resolver_negatives),
            final_instances=final_instances,
            instance_candidate_set=instance_candidate_set,
            instance_proposer_response=instance_response,
            instance_proposer_metadata=instance_proposer_metadata,
            usage_events=tuple(usage_events),
            selector_call_diagnostics=tuple(selector_call_diagnostics),
        )

    def render_deferred_batch_soft_calls(
        self,
        document_view: DocumentView,
        spec: ExtractionSpec,
        runtime: Runtime,
    ) -> tuple[SoftCallRequest, ...]:
        """Render batch-selector soft calls for deferred execution.

        This is the submit-phase sibling of `_select_validate_batch`: it runs
        deterministic candidate generation and planning, then stops before any
        provider call, validation, or resolution.
        """

        if spec.instance_cardinality is not Cardinality.ONE:
            raise InfrastructureError(
                "deferred batch selection currently supports only single-instance specs",
            )

        _, _, soft_field_specs, soft_candidate_sets = self._batch_soft_selection_inputs(
            document_view=document_view,
            spec=spec,
        )

        if not soft_candidate_sets:
            return ()

        selector = self._batch_selector_for_fields(tuple(soft_field_specs), runtime)
        render_soft_call_request = getattr(selector, "render_soft_call_request", None)
        if not callable(render_soft_call_request):
            raise InfrastructureError(
                "deferred batch selection requires a batch selector with "
                "render_soft_call_request(...)",
            )
        render_prompt = getattr(selector, "render_prompt", None)
        if not callable(render_prompt):
            raise InfrastructureError(
                "deferred batch selection requires a batch selector with "
                "render_prompt(...)",
            )

        plans = self._plan_batch_selector_calls(
            selector=selector,
            spec=spec,
            field_specs=tuple(soft_field_specs),
            candidate_sets=tuple(soft_candidate_sets),
            document_view=document_view,
            runtime=runtime,
        )
        requests: list[SoftCallRequest] = []
        for plan in plans:
            if isinstance(plan, DocumentWindowSelectorTaskPlan):
                for window in plan.windows:
                    context_pack = _build_batch_context_pack(
                        spec,
                        (plan.task.field_spec,),
                        document_view=document_view,
                        document_summary=window.text,
                    )
                    rendered = cast(
                        "RenderedPrompt",
                        render_prompt(
                            spec=spec,
                            candidate_sets=(plan.task.candidate_set,),
                            context_pack=context_pack,
                            instance_ids=("inst_0",),
                        ),
                    )
                    requests.append(
                        cast(
                            "SoftCallRequest",
                            render_soft_call_request(
                                rendered,
                                spec_hash=spec.version,
                                routing=SoftCallRouting(
                                    document_id=document_view.document_id,
                                    document_content_hash=document_view.source_ref.content_hash,
                                    field_id=plan.task.field_spec.field_id,
                                    instance_id="inst_0",
                                    window_index=window.index,
                                    window_count=window.count,
                                ),
                            ),
                        ),
                    )
                continue

            if isinstance(plan, ShardedSelectorTaskPlan):
                for shard_index, shard in enumerate(plan.shards, start=1):
                    shard_task = shard.tasks[0]
                    context_pack = _build_batch_context_pack(
                        spec,
                        (shard_task.field_spec,),
                        document_view=document_view,
                        classification_context_by_field=_classification_contexts_for_fields(
                            field_specs=(shard_task.field_spec,),
                            document_view=document_view,
                            runtime=runtime,
                        ),
                    )
                    rendered = cast(
                        "RenderedPrompt",
                        render_prompt(
                            spec=spec,
                            candidate_sets=(shard_task.candidate_set,),
                            context_pack=context_pack,
                            instance_ids=("inst_0",),
                        ),
                    )
                    requests.append(
                        cast(
                            "SoftCallRequest",
                            render_soft_call_request(
                                rendered,
                                spec_hash=spec.version,
                                routing=SoftCallRouting(
                                    document_id=document_view.document_id,
                                    document_content_hash=document_view.source_ref.content_hash,
                                    field_id=shard_task.field_spec.field_id,
                                    instance_id="inst_0",
                                    shard_index=shard_index,
                                    shard_count=len(plan.shards),
                                ),
                            ),
                        ),
                    )
                continue

            plan_field_specs = tuple(task.field_spec for task in plan.tasks)
            plan_candidate_sets = tuple(task.candidate_set for task in plan.tasks)
            context_pack = _build_batch_context_pack(
                spec,
                plan_field_specs,
                document_view=document_view,
                classification_context_by_field=_classification_contexts_for_fields(
                    field_specs=plan_field_specs,
                    document_view=document_view,
                    runtime=runtime,
                ),
            )
            rendered = cast(
                "RenderedPrompt",
                render_prompt(
                    spec=spec,
                    candidate_sets=plan_candidate_sets,
                    context_pack=context_pack,
                    instance_ids=("inst_0",),
                ),
            )
            requests.append(
                cast(
                    "SoftCallRequest",
                    render_soft_call_request(
                        rendered,
                        spec_hash=spec.version,
                        routing=SoftCallRouting(
                            document_id=document_view.document_id,
                            document_content_hash=document_view.source_ref.content_hash,
                        ),
                    ),
                ),
            )

        return tuple(requests)

    def collect_deferred_batch_soft_calls(
        self,
        *,
        document_view: DocumentView,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None,
        runtime: Runtime,
        manifest_requests: tuple[SoftCallRequest, ...],
        successful_responses: Mapping[str, SoftCallResponse],
    ) -> StrategyOutput:
        """Resolve recorded deferred batch-selector responses into strategy output."""

        if spec.instance_cardinality is not Cardinality.ONE:
            raise InfrastructureError(
                "deferred_collect.batch_unsupported: deferred collect currently "
                "supports only single-instance specs",
            )

        (
            candidate_sets,
            auto_observations,
            soft_field_specs,
            soft_candidate_sets,
        ) = self._batch_soft_selection_inputs(
            document_view=document_view,
            spec=spec,
        )

        observations: list[Observation] = []
        validated_fields: list[ValidatedField] = []
        pre_resolver_negatives: list[NegativeOutcome] = []
        usage_events: list[UsageEvent] = []
        selector_call_diagnostics: list[SelectorCallDiagnostic] = []
        batch_observations: tuple[Observation, ...] = ()

        if soft_candidate_sets:
            selector = self._batch_selector_for_fields(tuple(soft_field_specs), runtime)
            batch_observations = self._collect_deferred_budgeted_batches(
                selector=selector,
                document_view=document_view,
                spec=spec,
                field_specs=tuple(soft_field_specs),
                candidate_sets=tuple(soft_candidate_sets),
                manifest_requests=manifest_requests,
                successful_responses=successful_responses,
                runtime=runtime,
                usage_events=usage_events,
                selector_call_diagnostics=selector_call_diagnostics,
            )

        all_observations = (*auto_observations, *batch_observations)
        soft_field_ids = {field_spec.field_id for field_spec in soft_field_specs}
        observations_by_field = {
            observation.field_id: observation for observation in all_observations
        }
        for field_spec, candidate_set in zip(spec.fields, candidate_sets, strict=True):
            observation = observations_by_field[field_spec.field_id]
            if field_spec.field_id not in soft_field_ids:
                selector_call_diagnostics.append(
                    _selection_gate_diagnostic(
                        document_view=document_view,
                        spec=spec,
                        field_spec=field_spec,
                        candidate_set=candidate_set,
                        observation=observation,
                    ),
                )
            observations.append(observation)
            self._adapt_validate_observation(
                observation=observation,
                candidate_set=candidate_set,
                field_spec=field_spec,
                document_view=document_view,
                spec=spec,
                schema_cls=schema_cls,
                validated_fields=validated_fields,
                pre_resolver_negatives=pre_resolver_negatives,
            )

        final_instances = self._resolver.resolve(
            validated_fields=tuple(validated_fields),
            candidate_sets=tuple(candidate_sets),
            spec=spec,
            instance_plan=None,
        )
        return StrategyOutput(
            candidate_sets=tuple(candidate_sets),
            observations=tuple(observations),
            validated_fields=tuple(validated_fields),
            pre_resolver_negatives=tuple(pre_resolver_negatives),
            final_instances=final_instances,
            usage_events=tuple(usage_events),
            selector_call_diagnostics=tuple(selector_call_diagnostics),
        )

    def _batch_soft_selection_inputs(
        self,
        *,
        document_view: DocumentView,
        spec: ExtractionSpec,
    ) -> tuple[
        tuple[CandidateSet, ...],
        tuple[Observation, ...],
        tuple[FieldSpec, ...],
        tuple[CandidateSet, ...],
    ]:
        candidate_sets: list[CandidateSet] = []
        auto_observations: list[Observation] = []
        soft_field_specs: list[FieldSpec] = []
        soft_candidate_sets: list[CandidateSet] = []

        for field_spec in spec.fields:
            self._assert_supported_field(field_spec)
            generated_sets = tuple(
                binding.cls().generate(
                    field_spec=field_spec.model_copy(
                        update={"strategy_bindings": (binding,)},
                    ),
                    document_view=document_view,
                    instance_hint=None,
                )
                for binding in field_spec.strategy_bindings
            )
            candidate_set = _merge_candidate_sets(
                field_spec=field_spec,
                document_id=document_view.document_id,
                candidate_sets=generated_sets,
            )
            if field_spec.filter_binding is not None:
                candidate_set = apply_filter_binding(
                    candidate_set=candidate_set,
                    binding=field_spec.filter_binding,
                )
            candidate_sets.append(candidate_set)

            auto = self._selection_gate.evaluate(candidate_set)
            if auto is not None:
                auto_observations.append(
                    Observation(
                        instance_id="inst_0",
                        field_id=field_spec.field_id,
                        evidence_id=auto.candidate_id,
                        abstain=False,
                        outcome="SELECTED",
                        selected_candidate_ids=(auto.candidate_id,),
                        reason=auto.reason,
                        producer_version=self._selection_gate.producer_version,
                    ),
                )
                continue
            if not candidate_set.candidates:
                auto_observations.append(
                    Observation(
                        instance_id="inst_0",
                        field_id=field_spec.field_id,
                        evidence_id=None,
                        abstain=True,
                        outcome="NO_CANDIDATES",
                        selected_candidate_ids=(),
                        reason=None,
                        producer_version=self._selection_gate.producer_version,
                    ),
                )
                continue
            soft_field_specs.append(field_spec)
            soft_candidate_sets.append(candidate_set)

        return (
            tuple(candidate_sets),
            tuple(auto_observations),
            tuple(soft_field_specs),
            tuple(soft_candidate_sets),
        )

    def _collect_deferred_budgeted_batches(
        self,
        *,
        selector: BatchSelector,
        document_view: DocumentView,
        spec: ExtractionSpec,
        field_specs: tuple[FieldSpec, ...],
        candidate_sets: tuple[CandidateSet, ...],
        manifest_requests: tuple[SoftCallRequest, ...],
        successful_responses: Mapping[str, SoftCallResponse],
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic],
    ) -> tuple[Observation, ...]:
        plans = self._plan_batch_selector_calls(
            selector=selector,
            spec=spec,
            field_specs=field_specs,
            candidate_sets=candidate_sets,
            document_view=document_view,
            runtime=runtime,
        )
        request_by_id = {request.request_id: request for request in manifest_requests}
        observations: list[Observation] = []
        for batch_index, plan in enumerate(plans, start=1):
            if isinstance(plan, DocumentWindowSelectorTaskPlan):
                observations.append(
                    self._collect_deferred_document_windowed_field(
                        selector=selector,
                        document_view=document_view,
                        spec=spec,
                        plan=plan,
                        request_by_id=request_by_id,
                        successful_responses=successful_responses,
                        batch_index=batch_index,
                        batch_count=len(plans),
                        runtime=runtime,
                        usage_events=usage_events,
                        selector_call_diagnostics=selector_call_diagnostics,
                    ),
                )
                continue

            if isinstance(plan, ShardedSelectorTaskPlan):
                observations.append(
                    self._collect_deferred_sharded_field(
                        selector=selector,
                        document_view=document_view,
                        spec=spec,
                        plan=plan,
                        request_by_id=request_by_id,
                        successful_responses=successful_responses,
                        batch_index=batch_index,
                        batch_count=len(plans),
                        runtime=runtime,
                        usage_events=usage_events,
                        selector_call_diagnostics=selector_call_diagnostics,
                    ),
                )
                continue

            plan_field_specs = tuple(task.field_spec for task in plan.tasks)
            plan_candidate_sets = tuple(task.candidate_set for task in plan.tasks)
            context_pack = _build_batch_context_pack(
                spec,
                plan_field_specs,
                document_view=document_view,
                classification_context_by_field=_classification_contexts_for_fields(
                    field_specs=plan_field_specs,
                    document_view=document_view,
                    runtime=runtime,
                ),
            )
            request = self._render_deferred_batch_request(
                selector=selector,
                spec=spec,
                candidate_sets=plan_candidate_sets,
                context_pack=context_pack,
                routing=SoftCallRouting(
                    document_id=document_view.document_id,
                    document_content_hash=document_view.source_ref.content_hash,
                ),
            )
            observation_source = self._collect_deferred_batch_response(
                selector=selector,
                document_view=document_view,
                request=request,
                request_by_id=request_by_id,
                successful_responses=successful_responses,
                spec=spec,
                candidate_sets=plan_candidate_sets,
                runtime=runtime,
                usage_events=usage_events,
                selector_call_diagnostics=selector_call_diagnostics,
                batch_index=batch_index,
                batch_count=len(plans),
            )
            observations.extend(observation_source)
        return tuple(observations)

    def _collect_deferred_sharded_field(
        self,
        *,
        selector: BatchSelector,
        document_view: DocumentView,
        spec: ExtractionSpec,
        plan: ShardedSelectorTaskPlan,
        request_by_id: Mapping[str, SoftCallRequest],
        successful_responses: Mapping[str, SoftCallResponse],
        batch_index: int | None,
        batch_count: int | None,
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic] | None = None,
    ) -> Observation:
        if selector_call_diagnostics is None:
            selector_call_diagnostics = []
        shard_observations: list[Observation] = []
        for shard_index, shard in enumerate(plan.shards, start=1):
            shard_task = shard.tasks[0]
            context_pack = _build_batch_context_pack(
                spec,
                (shard_task.field_spec,),
                document_view=document_view,
                classification_context_by_field=_classification_contexts_for_fields(
                    field_specs=(shard_task.field_spec,),
                    document_view=document_view,
                    runtime=runtime,
                ),
            )
            request = self._render_deferred_batch_request(
                selector=selector,
                spec=spec,
                candidate_sets=(shard_task.candidate_set,),
                context_pack=context_pack,
                routing=SoftCallRouting(
                    document_id=document_view.document_id,
                    document_content_hash=document_view.source_ref.content_hash,
                    field_id=shard_task.field_spec.field_id,
                    instance_id="inst_0",
                    shard_index=shard_index,
                    shard_count=len(plan.shards),
                ),
            )
            shard_observations.extend(
                self._collect_deferred_batch_response(
                    selector=selector,
                    document_view=document_view,
                    request=request,
                    request_by_id=request_by_id,
                    successful_responses=successful_responses,
                    spec=spec,
                    candidate_sets=(shard_task.candidate_set,),
                    runtime=runtime,
                    usage_events=usage_events,
                    selector_call_diagnostics=selector_call_diagnostics,
                    batch_index=batch_index,
                    batch_count=batch_count,
                ),
            )

        selected_ids = _selected_candidate_ids_in_source_order(
            observations=tuple(shard_observations),
            candidate_set=plan.task.candidate_set,
        )
        if not selected_ids:
            return Observation(
                instance_id="inst_0",
                field_id=plan.task.field_spec.field_id,
                evidence_id=None,
                abstain=True,
                outcome="ABSTAINED",
                selected_candidate_ids=(),
                reason="all shards abstained",
                producer_version=_selector_producer_version(selector),
            )
        if plan.task.field_spec.cardinality is Cardinality.MANY:
            return Observation(
                instance_id="inst_0",
                field_id=plan.task.field_spec.field_id,
                evidence_id=selected_ids[0],
                abstain=False,
                outcome="SELECTED",
                selected_candidate_ids=selected_ids,
                reason="union of sharded selections",
                producer_version=_selector_producer_version(selector),
            )
        selected_shard_observations = tuple(
            observation
            for observation in shard_observations
            if observation.selected_candidate_ids
        )
        if len(selected_shard_observations) == 1:
            return selected_shard_observations[0]
        raise InfrastructureError(
            "deferred_collect.reducer_required: sharded field "
            f"{plan.task.field_spec.field_id!r} produced "
            f"{len(selected_shard_observations)} shard winners; deferred reducer "
            "follow-up is not implemented yet",
        )

    def _render_deferred_batch_request(
        self,
        *,
        selector: BatchSelector,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
        context_pack: ContextPack,
        routing: SoftCallRouting | None = None,
    ) -> SoftCallRequest:
        render_prompt = getattr(selector, "render_prompt", None)
        render_soft_call_request = getattr(selector, "render_soft_call_request", None)
        if not callable(render_prompt) or not callable(render_soft_call_request):
            raise InfrastructureError(
                "deferred_collect.batch_selector_unsupported: batch selector must "
                "expose render_prompt(...) and render_soft_call_request(...)",
            )
        rendered = cast(
            "RenderedPrompt",
            render_prompt(
                spec=spec,
                candidate_sets=candidate_sets,
                context_pack=context_pack,
                instance_ids=("inst_0",),
            ),
        )
        return cast(
            "SoftCallRequest",
            render_soft_call_request(
                rendered,
                spec_hash=spec.version,
                routing=routing,
            ),
        )

    def _collect_deferred_batch_response(
        self,
        *,
        selector: BatchSelector,
        document_view: DocumentView,
        request: SoftCallRequest,
        request_by_id: Mapping[str, SoftCallRequest],
        successful_responses: Mapping[str, SoftCallResponse],
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic],
        batch_index: int | None = None,
        batch_count: int | None = None,
    ) -> tuple[Observation, ...]:
        manifest_request = request_by_id.get(request.request_id)
        if manifest_request is None:
            raise InfrastructureError(
                "deferred_collect.request_mismatch: rendered collect request "
                f"{request.request_id!r} was not present in the manifest",
            )
        if manifest_request != request:
            raise InfrastructureError(
                "deferred_collect.request_mismatch: rendered collect request "
                f"{request.request_id!r} does not match the manifest request",
            )
        response = successful_responses[request.request_id]
        collect = getattr(selector, "observations_from_soft_call_response", None)
        if not callable(collect):
            raise InfrastructureError(
                "deferred_collect.batch_selector_unsupported: batch selector must "
                "expose observations_from_soft_call_response(...)",
            )
        observations = cast(
            "tuple[Observation, ...]",
            collect(
                request=request,
                response=response,
                spec=spec,
                candidate_sets=candidate_sets,
            ),
        )
        observations = enforce_batch_observation_contract(observations, candidate_sets)
        self._record_usage_event(
            getattr(selector, "last_usage_event", None),
            runtime=runtime,
            usage_events=usage_events,
        )
        selector_call_diagnostics.append(
            _selector_call_diagnostic(
                selector=selector,
                document_view=document_view,
                spec=spec,
                candidate_sets=candidate_sets,
                observations=observations,
                seam="batch_selector",
                decision_kind="llm",
                routing=request.routing,
                batch_index=batch_index,
                batch_count=batch_count,
            ),
        )
        return observations

    def _select_validate_independent(
        self,
        *,
        document_view: DocumentView,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None,
        runtime: Runtime,
        candidate_sets: tuple[CandidateSet, ...],
        selected_instance_ids: tuple[str, ...],
        instance_candidates_by_id: Mapping[str, InstanceCandidate],
        instance_keys_by_id: Mapping[str, InstanceGroupingKey],
        observations: list[Observation],
        validated_fields: list[ValidatedField],
        pre_resolver_negatives: list[NegativeOutcome],
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic],
    ) -> None:
        for field_spec, base_candidate_set in zip(spec.fields, candidate_sets, strict=True):
            per_instance_sets: list[tuple[str, CandidateSet]] = []
            if spec.instance_cardinality is Cardinality.MANY:
                for instance_id in selected_instance_ids:
                    per_instance_sets.append(
                        (
                            instance_id,
                            candidate_set_for_instance(
                                candidate_set=base_candidate_set,
                                instance_candidate=instance_candidates_by_id[instance_id],
                                instance_key=instance_keys_by_id[instance_id],
                            ),
                        ),
                    )
            else:
                per_instance_sets.append(("inst_0", base_candidate_set))

            for instance_id, candidate_set in per_instance_sets:
                # seam D — bounded observation.
                context_pack = _build_independent_context_pack(
                    spec,
                    field_spec,
                    document_view=document_view,
                    classification_context_by_field=_classification_contexts_for_fields(
                        field_specs=(field_spec,),
                        document_view=document_view,
                        runtime=runtime,
                    ),
                )
                auto = self._selection_gate.evaluate(candidate_set)
                if auto is not None:
                    observation = Observation(
                        instance_id=instance_id,
                        field_id=field_spec.field_id,
                        evidence_id=auto.candidate_id,
                        abstain=False,
                        outcome="SELECTED",
                        selected_candidate_ids=(auto.candidate_id,),
                        reason=auto.reason,
                        producer_version=self._selection_gate.producer_version,
                    )
                    selector_call_diagnostics.append(
                        _selection_gate_diagnostic(
                            document_view=document_view,
                            spec=spec,
                            field_spec=field_spec,
                            candidate_set=candidate_set,
                            observation=observation,
                        ),
                    )
                else:
                    selector = self._selector_for_field(field_spec, runtime)
                    logger.info(
                        "extractx.selector.started",
                        extra={
                            "extractx_event": "selector.started",
                            "document_id": document_view.document_id,
                            "spec_version": spec.version,
                            "field_id": field_spec.field_id,
                            "instance_id": instance_id,
                            "candidate_count": len(candidate_set.candidates),
                            "operation": "selector",
                            "model_id": getattr(selector, "model_id", None),
                        },
                    )
                    observation = selector.select(
                        field_spec=field_spec,
                        candidate_set=candidate_set,
                        context_pack=context_pack,
                        instance_state=None,
                        instance_ids=(instance_id,),
                    )
                    logger.info(
                        "extractx.selector.completed",
                        extra={
                            "extractx_event": "selector.completed",
                            "document_id": document_view.document_id,
                            "spec_version": spec.version,
                            "field_id": field_spec.field_id,
                            "instance_id": instance_id,
                            "candidate_count": len(candidate_set.candidates),
                            "outcome": observation.outcome,
                            "operation": "selector",
                            "model_id": getattr(selector, "model_id", None),
                        },
                    )
                    self._record_usage_event(
                        getattr(selector, "last_usage_event", None),
                        runtime=runtime,
                        usage_events=usage_events,
                    )
                    selector_call_diagnostics.append(
                        _selector_call_diagnostic(
                            selector=selector,
                            document_view=document_view,
                            spec=spec,
                            candidate_sets=(candidate_set,),
                            observations=(observation,),
                            seam="selector",
                            decision_kind=(
                                "no_candidates"
                                if observation.outcome == "NO_CANDIDATES"
                                else "llm"
                            ),
                        ),
                    )
                observations.append(observation)

                self._adapt_validate_observation(
                    observation=observation,
                    candidate_set=candidate_set,
                    field_spec=field_spec,
                    document_view=document_view,
                    spec=spec,
                    schema_cls=schema_cls,
                    validated_fields=validated_fields,
                    pre_resolver_negatives=pre_resolver_negatives,
                )

    def _select_validate_batch(
        self,
        *,
        document_view: DocumentView,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None,
        runtime: Runtime,
        candidate_sets: tuple[CandidateSet, ...],
        observations: list[Observation],
        validated_fields: list[ValidatedField],
        pre_resolver_negatives: list[NegativeOutcome],
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic],
    ) -> None:
        auto_observations: list[Observation] = []
        soft_field_specs: list[FieldSpec] = []
        soft_candidate_sets: list[CandidateSet] = []

        for field_spec, candidate_set in zip(spec.fields, candidate_sets, strict=True):
            auto = self._selection_gate.evaluate(candidate_set)
            if auto is not None:
                observation = Observation(
                    instance_id="inst_0",
                    field_id=field_spec.field_id,
                    evidence_id=auto.candidate_id,
                    abstain=False,
                    outcome="SELECTED",
                    selected_candidate_ids=(auto.candidate_id,),
                    reason=auto.reason,
                    producer_version=self._selection_gate.producer_version,
                )
                auto_observations.append(observation)
                selector_call_diagnostics.append(
                    _selection_gate_diagnostic(
                        document_view=document_view,
                        spec=spec,
                        field_spec=field_spec,
                        candidate_set=candidate_set,
                        observation=observation,
                    ),
                )
                continue
            if not candidate_set.candidates:
                observation = Observation(
                    instance_id="inst_0",
                    field_id=field_spec.field_id,
                    evidence_id=None,
                    abstain=True,
                    outcome="NO_CANDIDATES",
                    selected_candidate_ids=(),
                    reason=None,
                    producer_version=self._selection_gate.producer_version,
                )
                auto_observations.append(observation)
                selector_call_diagnostics.append(
                    _selection_gate_diagnostic(
                        document_view=document_view,
                        spec=spec,
                        field_spec=field_spec,
                        candidate_set=candidate_set,
                        observation=observation,
                    ),
                )
                continue
            soft_field_specs.append(field_spec)
            soft_candidate_sets.append(candidate_set)

        batch_observations: tuple[Observation, ...] = ()
        if soft_candidate_sets:
            selector = self._batch_selector_for_fields(tuple(soft_field_specs), runtime)
            batch_observations = self._select_budgeted_batches(
                selector=selector,
                document_view=document_view,
                spec=spec,
                field_specs=tuple(soft_field_specs),
                candidate_sets=tuple(soft_candidate_sets),
                runtime=runtime,
                usage_events=usage_events,
                selector_call_diagnostics=selector_call_diagnostics,
            )

        all_observations = (*auto_observations, *batch_observations)
        observations_by_field = {
            observation.field_id: observation for observation in all_observations
        }
        for field_spec, candidate_set in zip(spec.fields, candidate_sets, strict=True):
            observation = observations_by_field[field_spec.field_id]
            observations.append(observation)
            self._adapt_validate_observation(
                observation=observation,
                candidate_set=candidate_set,
                field_spec=field_spec,
                document_view=document_view,
                spec=spec,
                schema_cls=schema_cls,
                validated_fields=validated_fields,
                pre_resolver_negatives=pre_resolver_negatives,
            )

    def _select_budgeted_batches(
        self,
        *,
        selector: BatchSelector,
        document_view: DocumentView,
        spec: ExtractionSpec,
        field_specs: tuple[FieldSpec, ...],
        candidate_sets: tuple[CandidateSet, ...],
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic],
    ) -> tuple[Observation, ...]:
        plans = self._plan_batch_selector_calls(
            selector=selector,
            spec=spec,
            field_specs=field_specs,
            candidate_sets=candidate_sets,
            document_view=document_view,
            runtime=runtime,
        )
        observations: list[Observation] = []
        for batch_index, plan in enumerate(plans, start=1):
            if isinstance(plan, DocumentWindowSelectorTaskPlan):
                observation = self._select_document_windowed_field(
                    selector=selector,
                    document_view=document_view,
                    spec=spec,
                    plan=plan,
                    batch_index=batch_index,
                    batch_count=len(plans),
                    runtime=runtime,
                    usage_events=usage_events,
                    selector_call_diagnostics=selector_call_diagnostics,
                )
                observations.append(observation)
                continue

            if isinstance(plan, ShardedSelectorTaskPlan):
                observation = self._select_sharded_field(
                    selector=selector,
                    document_view=document_view,
                    spec=spec,
                    plan=plan,
                    batch_index=batch_index,
                    batch_count=len(plans),
                    runtime=runtime,
                    usage_events=usage_events,
                    selector_call_diagnostics=selector_call_diagnostics,
                )
                observations.append(observation)
                continue
            plan_field_specs = tuple(task.field_spec for task in plan.tasks)
            plan_candidate_sets = tuple(task.candidate_set for task in plan.tasks)
            logger.info(
                "extractx.batch_selector.started",
                extra={
                    "extractx_event": "batch_selector.started",
                    "document_id": document_view.document_id,
                    "spec_version": spec.version,
                    "batch_index": batch_index,
                    "batch_count": len(plans),
                    "field_count": len(plan.tasks),
                    "candidate_count": sum(len(c.candidates) for c in plan_candidate_sets),
                    "estimated_prompt_chars": (
                        None
                        if spec.prompt_policy.selector_prompt_max_chars is None
                        else plan.estimated_prompt_chars
                    ),
                    "max_prompt_chars": spec.prompt_policy.selector_prompt_max_chars,
                    "operation": "batch_selector",
                    "model_id": getattr(selector, "model_id", None),
                },
            )
            context_pack = _build_batch_context_pack(
                spec,
                plan_field_specs,
                document_view=document_view,
                classification_context_by_field=_classification_contexts_for_fields(
                    field_specs=plan_field_specs,
                    document_view=document_view,
                    runtime=runtime,
                ),
            )
            batch_observations = selector.select_many(
                spec=spec,
                candidate_sets=plan_candidate_sets,
                context_pack=context_pack,
                instance_state=None,
                instance_ids=("inst_0",),
            )
            batch_observations = enforce_batch_observation_contract(
                batch_observations,
                plan_candidate_sets,
            )
            logger.info(
                "extractx.batch_selector.completed",
                extra={
                    "extractx_event": "batch_selector.completed",
                    "document_id": document_view.document_id,
                    "spec_version": spec.version,
                    "batch_index": batch_index,
                    "batch_count": len(plans),
                    "field_count": len(plan.tasks),
                    "candidate_count": sum(len(c.candidates) for c in plan_candidate_sets),
                    "observation_count": len(batch_observations),
                    "estimated_prompt_chars": (
                        None
                        if spec.prompt_policy.selector_prompt_max_chars is None
                        else plan.estimated_prompt_chars
                    ),
                    "max_prompt_chars": spec.prompt_policy.selector_prompt_max_chars,
                    "operation": "batch_selector",
                    "model_id": getattr(selector, "model_id", None),
                },
            )
            self._record_usage_event(
                getattr(selector, "last_usage_event", None),
                runtime=runtime,
                usage_events=usage_events,
            )
            selector_call_diagnostics.append(
                _selector_call_diagnostic(
                    selector=selector,
                    document_view=document_view,
                    spec=spec,
                    candidate_sets=plan_candidate_sets,
                    observations=batch_observations,
                    seam="batch_selector",
                    decision_kind="llm",
                    batch_index=batch_index,
                    batch_count=len(plans),
                    estimated_prompt_chars=(
                        None
                        if spec.prompt_policy.selector_prompt_max_chars is None
                        else plan.estimated_prompt_chars
                    ),
                    max_prompt_chars=spec.prompt_policy.selector_prompt_max_chars,
                ),
            )
            observations.extend(batch_observations)
        return tuple(observations)

    def _plan_batch_selector_calls(
        self,
        *,
        selector: BatchSelector,
        spec: ExtractionSpec,
        field_specs: tuple[FieldSpec, ...],
        candidate_sets: tuple[CandidateSet, ...],
        document_view: DocumentView,
        runtime: Runtime | None = None,
    ) -> tuple[SelectorPlan, ...]:
        tasks = tuple(
            SelectorTask(field_spec=field_spec, candidate_set=candidate_set)
            for field_spec, candidate_set in zip(field_specs, candidate_sets, strict=True)
        )
        max_prompt_chars = spec.prompt_policy.selector_prompt_max_chars
        if max_prompt_chars is None:
            return (
                BatchSelectorCallPlan(tasks=tasks, estimated_prompt_chars=0),
            )

        plans: list[SelectorPlan] = []
        pending_tasks: list[SelectorTask] = []
        estimator = self._prompt_estimator(
            selector=selector,
            spec=spec,
            document_view=document_view,
            runtime=runtime,
        )
        planner = BudgetedBatchSelectorPlanner(max_prompt_chars=max_prompt_chars)

        def flush_pending() -> None:
            nonlocal pending_tasks
            if not pending_tasks:
                return
            plans.extend(
                planner.plan(
                    tasks=tuple(pending_tasks),
                    estimate_prompt_chars=estimator,
                ),
            )
            pending_tasks = []

        for task in tasks:
            estimate = estimator((task,))
            if estimate <= max_prompt_chars:
                pending_tasks.append(task)
                continue

            selector_policy = (
                None
                if runtime is None
                else runtime.selector_prompt_policies.get(task.field_spec.field_id)
            )
            if self._is_budgeted_document_classifier(
                field_spec=task.field_spec,
                runtime_policy=selector_policy,
            ):
                flush_pending()
                plans.append(
                    self._plan_document_windows(
                        selector=selector,
                        spec=spec,
                        task=task,
                        document_view=document_view,
                        policy=selector_policy,
                        original_estimated_prompt_chars=estimate,
                        max_prompt_chars=max_prompt_chars,
                    ),
                )
                continue
            pending_tasks.append(task)

        flush_pending()
        return tuple(plans)

    def _is_budgeted_document_classifier(
        self,
        *,
        field_spec: FieldSpec,
        runtime_policy: SelectorPromptPolicy | None,
    ) -> bool:
        return (
            runtime_policy is not None
            and runtime_policy.document_context_mode == "budgeted_windows"
            and _is_literal_set_category_field(field_spec)
        )

    def _plan_document_windows(
        self,
        *,
        selector: BatchSelector,
        spec: ExtractionSpec,
        task: SelectorTask,
        document_view: DocumentView,
        policy: SelectorPromptPolicy | None,
        original_estimated_prompt_chars: int,
        max_prompt_chars: int,
    ) -> DocumentWindowSelectorTaskPlan:
        if policy is None or policy.document_reducer is None:
            raise InfrastructureError(
                "document_classification.missing_prompt_policy: budgeted document "
                f"classification requires a reducer for field {task.field_spec.field_id!r}",
            )
        one_char_estimate = self._estimate_batch_selector_prompt_chars(
            selector=selector,
            spec=spec,
            field_specs=(task.field_spec,),
            candidate_sets=(task.candidate_set,),
            document_view=document_view,
            document_summary="x",
        )
        budgeted_text_chars = max_prompt_chars - one_char_estimate + 1
        # Leave room for estimator drift from XML tags, structured-output
        # schema serialization, and provider-specific envelope text.
        budgeted_text_chars -= 256
        if budgeted_text_chars <= 0:
            raise InfrastructureError(
                "selector_prompt_document_window_budget_exceeded: "
                f"field_id={task.field_spec.field_id!r} "
                f"estimated_prompt_chars={one_char_estimate} "
                f"max_prompt_chars={max_prompt_chars}",
            )

        text = document_view.normalized_text
        overlap = min(policy.document_window_overlap_chars, max(0, budgeted_text_chars - 1))
        while True:
            windows = self._document_windows_for_text(
                text=text,
                window_chars=budgeted_text_chars,
                overlap_chars=overlap,
            )
            estimated: list[DocumentWindow] = []
            oversized = False
            for index, (start, end, window_text) in enumerate(windows, start=1):
                estimate = self._estimate_batch_selector_prompt_chars(
                    selector=selector,
                    spec=spec,
                    field_specs=(task.field_spec,),
                    candidate_sets=(task.candidate_set,),
                    document_view=document_view,
                    document_summary=window_text,
                )
                if estimate > max_prompt_chars:
                    oversized = True
                    break
                estimated.append(
                    DocumentWindow(
                        index=index,
                        count=len(windows),
                        start_char=start,
                        end_char=end,
                        text=window_text,
                        estimated_prompt_chars=estimate,
                    ),
                )
            if not oversized:
                return DocumentWindowSelectorTaskPlan(
                    task=task,
                    windows=tuple(estimated),
                    original_estimated_prompt_chars=original_estimated_prompt_chars,
                    reducer_policy=policy.document_reducer,
                )
            next_budget = budgeted_text_chars // 2
            if next_budget <= 0 or next_budget == budgeted_text_chars:
                raise InfrastructureError(
                    "selector_prompt_document_window_budget_exceeded: "
                    f"field_id={task.field_spec.field_id!r} "
                    f"max_prompt_chars={max_prompt_chars}",
                )
            budgeted_text_chars = next_budget
            overlap = min(overlap, max(0, budgeted_text_chars - 1))

    def _document_windows_for_text(
        self,
        *,
        text: str,
        window_chars: int,
        overlap_chars: int,
    ) -> tuple[tuple[int, int, str], ...]:
        if window_chars <= 0:
            raise InfrastructureError("document_window.invalid_budget: window_chars must be > 0")
        windows: list[tuple[int, int, str]] = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(text_len, start + window_chars)
            windows.append((start, end, text[start:end]))
            if end >= text_len:
                break
            start = max(end - overlap_chars, start + 1)
        return tuple(windows)

    def _select_document_windowed_field(
        self,
        *,
        selector: BatchSelector,
        document_view: DocumentView,
        spec: ExtractionSpec,
        plan: DocumentWindowSelectorTaskPlan,
        batch_index: int | None,
        batch_count: int | None,
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic],
    ) -> Observation:
        window_observations: list[Observation] = []
        for window in plan.windows:
            context_pack = _build_batch_context_pack(
                spec,
                (plan.task.field_spec,),
                document_view=document_view,
                document_summary=window.text,
            )
            observations = selector.select_many(
                spec=spec,
                candidate_sets=(plan.task.candidate_set,),
                context_pack=context_pack,
                instance_state=None,
                instance_ids=("inst_0",),
            )
            observations = enforce_batch_observation_contract(
                observations,
                (plan.task.candidate_set,),
            )
            self._record_usage_event(
                getattr(selector, "last_usage_event", None),
                runtime=runtime,
                usage_events=usage_events,
            )
            routing = SoftCallRouting(
                document_id=document_view.document_id,
                document_content_hash=document_view.source_ref.content_hash,
                field_id=plan.task.field_spec.field_id,
                instance_id="inst_0",
                window_index=window.index,
                window_count=window.count,
            )
            selector_call_diagnostics.append(
                _selector_call_diagnostic(
                    selector=selector,
                    document_view=document_view,
                    spec=spec,
                    candidate_sets=(plan.task.candidate_set,),
                    observations=observations,
                    seam="batch_selector",
                    decision_kind="llm",
                    batch_index=batch_index,
                    batch_count=batch_count,
                    estimated_prompt_chars=window.estimated_prompt_chars,
                    max_prompt_chars=spec.prompt_policy.selector_prompt_max_chars,
                    routing=routing,
                ),
            )
            window_observations.extend(observations)
        return self._reduce_document_window_observations(
            field_spec=plan.task.field_spec,
            candidate_set=plan.task.candidate_set,
            observations=tuple(window_observations),
            selector=selector,
            reducer=cast("DocumentClassificationReducerPolicy", plan.reducer_policy),
        )

    def _collect_deferred_document_windowed_field(
        self,
        *,
        selector: BatchSelector,
        document_view: DocumentView,
        spec: ExtractionSpec,
        plan: DocumentWindowSelectorTaskPlan,
        request_by_id: Mapping[str, SoftCallRequest],
        successful_responses: Mapping[str, SoftCallResponse],
        batch_index: int | None,
        batch_count: int | None,
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic],
    ) -> Observation:
        window_observations: list[Observation] = []
        for window in plan.windows:
            context_pack = _build_batch_context_pack(
                spec,
                (plan.task.field_spec,),
                document_view=document_view,
                document_summary=window.text,
            )
            request = self._render_deferred_batch_request(
                selector=selector,
                spec=spec,
                candidate_sets=(plan.task.candidate_set,),
                context_pack=context_pack,
                routing=SoftCallRouting(
                    document_id=document_view.document_id,
                    document_content_hash=document_view.source_ref.content_hash,
                    field_id=plan.task.field_spec.field_id,
                    instance_id="inst_0",
                    window_index=window.index,
                    window_count=window.count,
                ),
            )
            window_observations.extend(
                self._collect_deferred_batch_response(
                    selector=selector,
                    document_view=document_view,
                    request=request,
                    request_by_id=request_by_id,
                    successful_responses=successful_responses,
                    spec=spec,
                    candidate_sets=(plan.task.candidate_set,),
                    runtime=runtime,
                    usage_events=usage_events,
                    selector_call_diagnostics=selector_call_diagnostics,
                    batch_index=batch_index,
                    batch_count=batch_count,
                ),
            )
        return self._reduce_document_window_observations(
            field_spec=plan.task.field_spec,
            candidate_set=plan.task.candidate_set,
            observations=tuple(window_observations),
            selector=selector,
            reducer=cast("DocumentClassificationReducerPolicy", plan.reducer_policy),
        )

    def _reduce_document_window_observations(
        self,
        *,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        observations: tuple[Observation, ...],
        selector: BatchSelector,
        reducer: DocumentClassificationReducerPolicy,
    ) -> Observation:
        if reducer.strategy == "union":
            if field_spec.cardinality is not Cardinality.MANY:
                raise InfrastructureError(
                    "document_classification.reducer_cardinality_mismatch: "
                    f"strategy='union' requires Cardinality.MANY, "
                    f"field_id={field_spec.field_id!r} "
                    f"cardinality={field_spec.cardinality.value!r}",
                )
            selected_ids = _selected_candidate_ids_in_source_order(
                observations=observations,
                candidate_set=candidate_set,
            )
            return Observation(
                instance_id="inst_0",
                field_id=field_spec.field_id,
                evidence_id=selected_ids[0] if selected_ids else None,
                abstain=False,
                outcome="SELECTED",
                selected_candidate_ids=selected_ids,
                reason="union reduction of document-window classifications",
                producer_version=_selector_producer_version(selector),
            )

        if field_spec.cardinality is Cardinality.MANY:
            raise InfrastructureError(
                "document_classification.reducer_cardinality_mismatch: "
                f"strategy='priority' is not valid for Cardinality.MANY, "
                f"field_id={field_spec.field_id!r}",
            )
        winning_candidate_id = _priority_winner_candidate_id(
            candidate_set=candidate_set,
            observations=observations,
            reducer=reducer,
        )
        if winning_candidate_id is None:
            return Observation(
                instance_id="inst_0",
                field_id=field_spec.field_id,
                evidence_id=None,
                abstain=True,
                outcome="ABSTAINED",
                selected_candidate_ids=(),
                reason="all document windows abstained",
                producer_version=_selector_producer_version(selector),
            )
        return Observation(
            instance_id="inst_0",
            field_id=field_spec.field_id,
            evidence_id=winning_candidate_id,
            abstain=False,
            outcome="SELECTED",
            selected_candidate_ids=(winning_candidate_id,),
            reason="priority reduction of document-window classifications",
            producer_version=_selector_producer_version(selector),
        )

    def _select_sharded_field(
        self,
        *,
        selector: BatchSelector,
        document_view: DocumentView,
        spec: ExtractionSpec,
        plan: ShardedSelectorTaskPlan,
        batch_index: int | None = None,
        batch_count: int | None = None,
        reducer_round: int = 0,
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic] | None = None,
    ) -> Observation:
        if selector_call_diagnostics is None:
            selector_call_diagnostics = []
        shard_observations: list[Observation] = []
        for shard_index, shard in enumerate(plan.shards, start=1):
            shard_task = shard.tasks[0]
            logger.info(
                "extractx.batch_selector.shard_started",
                extra={
                    "extractx_event": "batch_selector.shard_started",
                    "document_id": document_view.document_id,
                    "spec_version": spec.version,
                    "field_id": plan.task.field_spec.field_id,
                    "original_estimated_prompt_chars": plan.original_estimated_prompt_chars,
                    "shard_index": shard_index,
                    "shard_count": len(plan.shards),
                    "candidate_count": len(shard_task.candidate_set.candidates),
                    "estimated_prompt_chars": shard.estimated_prompt_chars,
                    "max_prompt_chars": spec.prompt_policy.selector_prompt_max_chars,
                    "operation": "batch_selector",
                    "model_id": getattr(selector, "model_id", None),
                },
            )
            context_pack = _build_batch_context_pack(
                spec,
                (shard_task.field_spec,),
                document_view=document_view,
                classification_context_by_field=_classification_contexts_for_fields(
                    field_specs=(shard_task.field_spec,),
                    document_view=document_view,
                    runtime=runtime,
                ),
            )
            observations = selector.select_many(
                spec=spec,
                candidate_sets=(shard_task.candidate_set,),
                context_pack=context_pack,
                instance_state=None,
                instance_ids=("inst_0",),
            )
            observations = enforce_batch_observation_contract(
                observations,
                (shard_task.candidate_set,),
            )
            self._record_usage_event(
                getattr(selector, "last_usage_event", None),
                runtime=runtime,
                usage_events=usage_events,
            )
            selector_call_diagnostics.append(
                _selector_call_diagnostic(
                    selector=selector,
                    document_view=document_view,
                    spec=spec,
                    candidate_sets=(shard_task.candidate_set,),
                    observations=observations,
                    seam="batch_selector",
                    decision_kind="llm",
                    batch_index=batch_index,
                    batch_count=batch_count,
                    shard_index=shard_index,
                    shard_count=len(plan.shards),
                    reducer_round=reducer_round,
                    estimated_prompt_chars=shard.estimated_prompt_chars,
                    max_prompt_chars=spec.prompt_policy.selector_prompt_max_chars,
                ),
            )
            shard_observations.extend(observations)

        selected_ids = _selected_candidate_ids_in_source_order(
            observations=tuple(shard_observations),
            candidate_set=plan.task.candidate_set,
        )
        if not selected_ids:
            return Observation(
                instance_id="inst_0",
                field_id=plan.task.field_spec.field_id,
                evidence_id=None,
                abstain=True,
                outcome="ABSTAINED",
                selected_candidate_ids=(),
                reason="all shards abstained",
                producer_version=_selector_producer_version(selector),
            )
        if plan.task.field_spec.cardinality is Cardinality.MANY:
            return Observation(
                instance_id="inst_0",
                field_id=plan.task.field_spec.field_id,
                evidence_id=selected_ids[0],
                abstain=False,
                outcome="SELECTED",
                selected_candidate_ids=selected_ids,
                reason="union of sharded selections",
                producer_version=_selector_producer_version(selector),
            )
        selected_shard_observations = tuple(
            observation
            for observation in shard_observations
            if observation.selected_candidate_ids
        )
        if len(selected_shard_observations) == 1:
            return selected_shard_observations[0]

        selected_id_set = set(selected_ids)
        winner_set = candidate_set_view(
            plan.task.candidate_set,
            tuple(
                candidate
                for candidate in plan.task.candidate_set.candidates
                if candidate.candidate_id in selected_id_set
            ),
        )
        max_prompt_chars = spec.prompt_policy.selector_prompt_max_chars
        if max_prompt_chars is not None:
            winner_estimate = self._estimate_batch_selector_prompt_chars(
                selector=selector,
                spec=spec,
                field_specs=(plan.task.field_spec,),
                candidate_sets=(winner_set,),
                document_view=document_view,
                runtime=runtime,
            )
            if winner_estimate > max_prompt_chars:
                if len(selected_ids) >= len(plan.task.candidate_set.candidates):
                    raise InfrastructureError(
                        "selector_prompt_reducer_no_progress: "
                        f"field_id={plan.task.field_spec.field_id!r} "
                        f"candidate_count={len(plan.task.candidate_set.candidates)} "
                        f"selected_candidate_count={len(selected_ids)} "
                        f"estimated_prompt_chars={winner_estimate} "
                        f"max_prompt_chars={max_prompt_chars}",
                    )
                return self._select_sharded_winners(
                    selector=selector,
                    document_view=document_view,
                    spec=spec,
                    field_spec=plan.task.field_spec,
                    winner_set=winner_set,
                    winner_estimate=winner_estimate,
                    max_prompt_chars=max_prompt_chars,
                    reducer_round=reducer_round + 1,
                    runtime=runtime,
                    usage_events=usage_events,
                    selector_call_diagnostics=selector_call_diagnostics,
                )
        context_pack = _build_batch_context_pack(
            spec,
            (plan.task.field_spec,),
            document_view=document_view,
            classification_context_by_field=_classification_contexts_for_fields(
                field_specs=(plan.task.field_spec,),
                document_view=document_view,
                runtime=runtime,
            ),
        )
        reduced = selector.select_many(
            spec=spec,
            candidate_sets=(winner_set,),
            context_pack=context_pack,
            instance_state=None,
            instance_ids=("inst_0",),
        )
        reduced = enforce_batch_observation_contract(reduced, (winner_set,))
        self._record_usage_event(
            getattr(selector, "last_usage_event", None),
            runtime=runtime,
            usage_events=usage_events,
        )
        selector_call_diagnostics.append(
            _selector_call_diagnostic(
                selector=selector,
                document_view=document_view,
                spec=spec,
                candidate_sets=(winner_set,),
                observations=reduced,
                seam="batch_selector",
                decision_kind="shard_reducer",
                batch_index=batch_index,
                batch_count=batch_count,
                reducer_round=reducer_round + 1,
                estimated_prompt_chars=(
                    None
                    if spec.prompt_policy.selector_prompt_max_chars is None
                    else self._estimate_batch_selector_prompt_chars(
                        selector=selector,
                        spec=spec,
                        field_specs=(plan.task.field_spec,),
                        candidate_sets=(winner_set,),
                        document_view=document_view,
                        runtime=runtime,
                    )
                ),
                max_prompt_chars=spec.prompt_policy.selector_prompt_max_chars,
            ),
        )
        return reduced[0]

    def _select_sharded_winners(
        self,
        *,
        selector: BatchSelector,
        document_view: DocumentView,
        spec: ExtractionSpec,
        field_spec: FieldSpec,
        winner_set: CandidateSet,
        winner_estimate: int,
        max_prompt_chars: int,
        reducer_round: int,
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic],
    ) -> Observation:
        planner = BudgetedBatchSelectorPlanner(max_prompt_chars=max_prompt_chars)
        winner_task = SelectorTask(field_spec=field_spec, candidate_set=winner_set)
        reducer_shards = planner.plan_candidate_shards(
            task=winner_task,
            estimate_prompt_chars=self._prompt_estimator(
                selector=selector,
                spec=spec,
                document_view=document_view,
            ),
        )
        if all(len(shard.tasks[0].candidate_set.candidates) == 1 for shard in reducer_shards):
            raise InfrastructureError(
                "selector_prompt_reducer_budget_exceeded: "
                f"field_id={field_spec.field_id!r} "
                f"candidate_count={len(winner_set.candidates)} "
                f"estimated_prompt_chars={winner_estimate} "
                f"max_prompt_chars={max_prompt_chars}",
            )
        return self._select_sharded_field(
            selector=selector,
            document_view=document_view,
            spec=spec,
            plan=ShardedSelectorTaskPlan(
                task=winner_task,
                shards=reducer_shards,
                original_estimated_prompt_chars=winner_estimate,
            ),
            reducer_round=reducer_round,
            runtime=runtime,
            usage_events=usage_events,
            selector_call_diagnostics=selector_call_diagnostics,
        )

    def _prompt_estimator(
        self,
        *,
        selector: BatchSelector,
        spec: ExtractionSpec,
        document_view: DocumentView,
        runtime: Runtime | None = None,
    ) -> Callable[[tuple[SelectorTask, ...]], int]:
        def estimate(tasks: tuple[SelectorTask, ...]) -> int:
            return self._estimate_batch_selector_prompt_chars(
                selector=selector,
                spec=spec,
                field_specs=tuple(task.field_spec for task in tasks),
                candidate_sets=tuple(task.candidate_set for task in tasks),
                document_view=document_view,
                runtime=runtime,
            )

        return estimate

    def _estimate_batch_selector_prompt_chars(
        self,
        *,
        selector: BatchSelector,
        spec: ExtractionSpec,
        field_specs: tuple[FieldSpec, ...],
        candidate_sets: tuple[CandidateSet, ...],
        document_view: DocumentView,
        document_summary: str | None = None,
        runtime: Runtime | None = None,
    ) -> int:
        render_prompt = getattr(selector, "render_prompt", None)
        if not callable(render_prompt):
            raise InfrastructureError(
                "batch_selector.prompt_budget_unsupported: configured "
                "selector_prompt_max_chars requires a batch selector with render_prompt",
            )
        context_pack = _build_batch_context_pack(
            spec,
            field_specs,
            document_view=document_view,
            document_summary=document_summary,
            classification_context_by_field={}
            if runtime is None or document_summary is not None
            else _classification_contexts_for_fields(
                field_specs=field_specs,
                document_view=document_view,
                runtime=runtime,
            ),
        )
        rendered = cast(
            "RenderedPrompt",
            render_prompt(
                spec=spec,
                candidate_sets=candidate_sets,
                context_pack=context_pack,
                instance_ids=("inst_0",),
            ),
        )
        return _rendered_prompt_chars(rendered)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _selector_for_field(self, field_spec: FieldSpec, runtime: Runtime) -> Selector:
        binding = field_spec.selector_binding
        if binding is None:
            return self._selector

        selector_cls = binding.cls
        params = dict(binding.params)
        if selector_cls is PydanticAISelector or issubclass(selector_cls, PydanticAISelector):
            if runtime.llm is None and "provider" not in params:
                raise InfrastructureError(
                    "selector.missing_llm: field "
                    f"{field_spec.field_id!r} is bound to PydanticAISelector "
                    "but Runtime.llm is not set",
                )
            params.setdefault("provider", runtime.llm)
            if runtime.prompt_recorder is not None:
                params.setdefault("prompt_recorder", runtime.prompt_recorder)
            if runtime.selector_prompt_assets is not None:
                params.setdefault("prompt_asset_resolver", runtime.selector_prompt_assets)
            policy = runtime.selector_prompt_policies.get(field_spec.field_id)
            if policy is not None:
                params.setdefault("prompt_policy", policy)
            if field_spec.value_kind.name == "CATEGORY" and "prompt" not in params:
                params["prompt"] = ClassificationPrompt()
        return cast("Selector", selector_cls(**params))

    def _batch_selector_for_fields(
        self,
        field_specs: tuple[FieldSpec, ...],
        runtime: Runtime,
    ) -> BatchSelector:
        if not field_specs:
            raise InfrastructureError("batch_selector.empty_fields: no fields were supplied")

        bindings = [field_spec.selector_binding for field_spec in field_specs]
        missing = [
            field_spec.field_id
            for field_spec, binding in zip(field_specs, bindings, strict=True)
            if binding is None
        ]
        if missing:
            raise InfrastructureError(
                "batch_selector.missing_binding: ExecutorPolicy.strategy='batch' "
                "requires every soft-selected field to bind a batch-capable selector; "
                f"missing={missing!r}",
            )

        first = bindings[0]
        assert first is not None
        for field_spec, binding in zip(field_specs[1:], bindings[1:], strict=True):
            assert binding is not None
            if binding.cls is not first.cls or dict(binding.params) != dict(first.params):
                raise InfrastructureError(
                    "batch_selector.mixed_bindings: ExecutorPolicy.strategy='batch' "
                    "requires one shared selector binding for all soft-selected fields; "
                    f"field {field_spec.field_id!r} differs",
                )

        selector_cls = first.cls
        params = dict(first.params)
        if selector_cls is PydanticAIBatchSelector or issubclass(
            selector_cls,
            PydanticAIBatchSelector,
        ):
            if runtime.llm is None and "provider" not in params:
                raise InfrastructureError(
                    "batch_selector.missing_llm: fields are bound to PydanticAIBatchSelector "
                    "but Runtime.llm is not set",
                )
            params.setdefault("provider", runtime.llm)
            if runtime.prompt_recorder is not None:
                params.setdefault("prompt_recorder", runtime.prompt_recorder)
            if runtime.selector_prompt_assets is not None:
                params.setdefault("prompt_asset_resolver", runtime.selector_prompt_assets)
            if runtime.selector_prompt_policies:
                params.setdefault("prompt_policies", runtime.selector_prompt_policies)
        selector = selector_cls(**params)
        if not hasattr(selector, "select_many"):
            raise InfrastructureError(
                "batch_selector.not_batch_capable: ExecutorPolicy.strategy='batch' "
                f"requires a BatchSelector; got {selector_cls!r}",
            )
        return cast("BatchSelector", selector)

    def select_observation(
        self,
        *,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        spec: ExtractionSpec,
        document_view: DocumentView,
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic] | None = None,
        instance_id: str = "inst_0",
        retry_feedback: tuple[str, ...] = (),
    ) -> Observation:
        """select one observation, optionally carrying retry feedback.

        The independent strategy uses this with empty feedback; the
        opt-in iterative executor path uses the same selector boundary
        with structured object-validation reasons. Selection remains
        bounded by the original `CandidateSet`.
        """

        context_pack = _build_independent_context_pack(
            spec,
            field_spec,
            document_view=document_view,
            retry_feedback=retry_feedback,
            classification_context_by_field=_classification_contexts_for_fields(
                field_specs=(field_spec,),
                document_view=document_view,
                runtime=runtime,
            ),
        )
        auto = self._selection_gate.evaluate(candidate_set)
        if auto is not None:
            observation = Observation(
                instance_id=instance_id,
                field_id=field_spec.field_id,
                evidence_id=auto.candidate_id,
                abstain=False,
                outcome="SELECTED",
                selected_candidate_ids=(auto.candidate_id,),
                reason=auto.reason,
                producer_version=self._selection_gate.producer_version,
            )
            if selector_call_diagnostics is not None:
                selector_call_diagnostics.append(
                    _selection_gate_diagnostic(
                        document_view=document_view,
                        spec=spec,
                        field_spec=field_spec,
                        candidate_set=candidate_set,
                        observation=observation,
                    ),
                )
            return observation

        selector = self._selector_for_field(field_spec, runtime)
        if _is_batch_selector(selector):
            batch_selector = cast("BatchSelector", selector)
            logger.info(
                "extractx.batch_selector.repair_started",
                extra={
                    "extractx_event": "batch_selector.repair_started",
                    "document_id": document_view.document_id,
                    "spec_version": spec.version,
                    "field_id": field_spec.field_id,
                    "instance_id": instance_id,
                    "candidate_count": len(candidate_set.candidates),
                    "operation": "batch_selector",
                    "model_id": getattr(selector, "model_id", None),
                },
            )
            batch_context_pack = _build_batch_context_pack(
                spec,
                (field_spec,),
                document_view=document_view,
                retry_feedback=retry_feedback,
                classification_context_by_field=_classification_contexts_for_fields(
                    field_specs=(field_spec,),
                    document_view=document_view,
                    runtime=runtime,
                ),
            )
            batch_observations = batch_selector.select_many(
                spec=spec,
                candidate_sets=(candidate_set,),
                context_pack=batch_context_pack,
                instance_state=None,
                instance_ids=(instance_id,),
            )
            batch_observations = enforce_batch_observation_contract(
                batch_observations,
                (candidate_set,),
            )
            self._record_usage_event(
                getattr(selector, "last_usage_event", None),
                runtime=runtime,
                usage_events=usage_events,
            )
            observation = batch_observations[0]
            if selector_call_diagnostics is not None:
                selector_call_diagnostics.append(
                    _selector_call_diagnostic(
                        selector=selector,
                        document_view=document_view,
                        spec=spec,
                        candidate_sets=(candidate_set,),
                        observations=(observation,),
                        seam="batch_selector",
                        decision_kind=(
                            "no_candidates"
                            if observation.outcome == "NO_CANDIDATES"
                            else "llm"
                        ),
                    ),
                )
            logger.info(
                "extractx.batch_selector.repair_completed",
                extra={
                    "extractx_event": "batch_selector.repair_completed",
                    "document_id": document_view.document_id,
                    "spec_version": spec.version,
                    "field_id": field_spec.field_id,
                    "instance_id": instance_id,
                    "candidate_count": len(candidate_set.candidates),
                    "outcome": observation.outcome,
                    "operation": "batch_selector",
                    "model_id": getattr(selector, "model_id", None),
                },
            )
            return observation

        logger.info(
            "extractx.selector.started",
            extra={
                "extractx_event": "selector.started",
                "document_id": document_view.document_id,
                "spec_version": spec.version,
                "field_id": field_spec.field_id,
                "instance_id": instance_id,
                "candidate_count": len(candidate_set.candidates),
                "operation": "selector",
                "model_id": getattr(selector, "model_id", None),
            },
        )
        observation = selector.select(
            field_spec=field_spec,
            candidate_set=candidate_set,
            context_pack=context_pack,
            instance_state=None,
            instance_ids=(instance_id,),
        )
        logger.info(
            "extractx.selector.completed",
            extra={
                "extractx_event": "selector.completed",
                "document_id": document_view.document_id,
                "spec_version": spec.version,
                "field_id": field_spec.field_id,
                "instance_id": instance_id,
                "candidate_count": len(candidate_set.candidates),
                "outcome": observation.outcome,
                "operation": "selector",
                "model_id": getattr(selector, "model_id", None),
            },
        )
        self._record_usage_event(
            getattr(selector, "last_usage_event", None),
            runtime=runtime,
            usage_events=usage_events,
        )
        if selector_call_diagnostics is not None:
            selector_call_diagnostics.append(
                _selector_call_diagnostic(
                    selector=selector,
                    document_view=document_view,
                    spec=spec,
                    candidate_sets=(candidate_set,),
                    observations=(observation,),
                    seam="selector",
                    decision_kind=(
                        "no_candidates" if observation.outcome == "NO_CANDIDATES" else "llm"
                    ),
                ),
            )
        return observation

    def adapt_and_validate(
        self,
        *,
        observation: Observation,
        candidate_set: CandidateSet,
        field_spec: FieldSpec,
        document_view: DocumentView,
        schema_cls: type[BaseModel] | None,
    ) -> tuple[tuple[ValidatedField, ...], tuple[NegativeOutcome, ...]]:
        """adapt one observation and validate every proposed field."""

        adapted = self._selection_adapter.adapt(
            observation=observation,
            candidate_set=candidate_set,
            field_spec=field_spec,
        )
        if isinstance(adapted, NegativeOutcome):
            return (), (adapted,)

        validated_fields: list[ValidatedField] = []
        negatives: list[NegativeOutcome] = []
        for proposed in adapted:
            validated_or_negative = self._validate_one(
                proposed=proposed,
                field_spec=field_spec,
                document_view=document_view,
                schema_cls=schema_cls,
            )
            if isinstance(validated_or_negative, ValidatedField):
                validated_fields.append(validated_or_negative)
            else:
                negatives.append(validated_or_negative)
        return tuple(validated_fields), tuple(negatives)

    def _adapt_validate_observation(
        self,
        *,
        observation: Observation,
        candidate_set: CandidateSet,
        field_spec: FieldSpec,
        document_view: DocumentView,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None,
        validated_fields: list[ValidatedField],
        pre_resolver_negatives: list[NegativeOutcome],
    ) -> None:
        validated, negatives = self.adapt_and_validate(
            observation=observation,
            candidate_set=candidate_set,
            field_spec=field_spec,
            document_view=document_view,
            schema_cls=schema_cls,
        )
        validated_fields.extend(validated)
        for negative in negatives:
            _log_validation_rejected(
                document_view=document_view,
                spec=spec,
                outcome=negative,
            )
            pre_resolver_negatives.append(negative)

    def resolve_instances(
        self,
        *,
        validated_fields: tuple[ValidatedField, ...],
        candidate_sets: tuple[CandidateSet, ...],
        spec: ExtractionSpec,
        instance_plan: InstancePlan | None,
    ) -> tuple[Instance, ...]:
        """resolve validated fields into extraction instances."""

        return self._resolver.resolve(
            validated_fields=validated_fields,
            candidate_sets=candidate_sets,
            spec=spec,
            instance_plan=instance_plan,
        )

    def _instance_proposer_for_spec(
        self,
        spec: ExtractionSpec,
        runtime: Runtime,
    ) -> InstanceProposer:
        binding = spec.instance_proposer_binding
        if binding is None:
            raise InfrastructureError(
                "instance_proposer.missing_binding: Cardinality.MANY requires "
                "ExtractionSpec.instance_proposer_binding",
            )
        proposer_cls = binding.cls
        params = dict(binding.params)
        if proposer_cls is LLMInstanceProposer or issubclass(proposer_cls, LLMInstanceProposer):
            if runtime.llm is None and "provider" not in params:
                raise InfrastructureError(
                    "instance_proposer.missing_llm: spec is bound to "
                    "LLMInstanceProposer but Runtime.llm is not set",
                )
            params.setdefault("provider", runtime.llm)
        return cast("InstanceProposer", proposer_cls(**params))

    def _assert_supported_field(self, field_spec: FieldSpec) -> None:
        """fail loudly if the executor pre-run gate let an unsupported
        field through. the canonical gate lives in
        `SerialExecutor._validate_supported_path(...)` so unsupported
        paths fail before the run begins per the brief's section 2.
        this is a defense in depth.
        """

        if not field_spec.strategy_bindings:
            raise InfrastructureError(
                "IndependentStrategy: field "
                f"{field_spec.field_id!r} has no strategy_bindings; "
                "phase-1 requires explicit candidate strategy bindings",
            )
        for binding in field_spec.strategy_bindings:
            if binding.kind != "candidate":
                raise InfrastructureError(
                    "IndependentStrategy: field "
                    f"{field_spec.field_id!r} has strategy_bindings entry "
                    f"with kind={binding.kind!r}; phase-1 supports only "
                    "kind='candidate'",
                )
            cls = binding.cls
            supported = (
                cls is RegexCandidateStrategy
                or issubclass(cls, RegexCandidateStrategy)
                or cls is NerCandidateStrategy
                or issubclass(cls, NerCandidateStrategy)
                or cls is LiteralSetCandidateStrategy
                or issubclass(cls, LiteralSetCandidateStrategy)
            )
            if not supported:
                raise InfrastructureError(
                    "IndependentStrategy: field "
                    f"{field_spec.field_id!r} bound to {cls!r}; phase-1 "
                    "supports only RegexCandidateStrategy, NerCandidateStrategy, "
                    "or LiteralSetCandidateStrategy",
                )

    def _validate_one(
        self,
        *,
        proposed: ProposedField,
        field_spec: FieldSpec,
        document_view: DocumentView,
        schema_cls: type[BaseModel] | None,
    ) -> ValidatedField | NegativeOutcome:
        """run seam F on one `ProposedField` and resolve the typed
        outcome under phase-1 `ExecutorPolicy.on_validation_failure
        == "fail"` policy.

        seam F's `validate(...)` returns one of three shapes; phase-1
        collapses them as follows:

        - `ValidatedField`: success — promoted into the validated set.
        - `NegativeOutcome`: layer-1 negative — propagated as-is into
          the pre-resolver negative list.
        - `ValidationFailure(layer="field", ...)`: layer-2 failure
          under `on_validation_failure="fail"` — escalated to a typed
          `NegativeOutcome(category="validation", code="field_failure",
          ...)` per the brief's section 5.
        """

        outcome = self._validator.validate(
            proposed=proposed,
            field_spec=field_spec,
            document_view=document_view,
            schema_cls=schema_cls,
        )
        if isinstance(outcome, ValidatedField):
            return outcome
        if isinstance(outcome, NegativeOutcome):
            return outcome
        # narrowed: `LayeredProposalValidator.validate` returns one of
        # `ValidatedField | NegativeOutcome | ValidationFailure`; the
        # first two branches above leave only the third here.
        return _escalate_validation_failure(outcome)

    @staticmethod
    def _record_usage_event(
        event: object,
        *,
        runtime: Runtime,
        usage_events: list[UsageEvent],
    ) -> None:
        if not isinstance(event, UsageEvent):
            return
        usage_events.append(event)
        runtime.budget.record(event)


def _merge_candidate_sets(
    *,
    field_spec: FieldSpec,
    document_id: str,
    candidate_sets: tuple[CandidateSet, ...],
) -> CandidateSet:
    """Compose one field's candidate sets into the canonical C->D handoff."""

    if len(candidate_sets) == 1:
        candidate_set = candidate_sets[0]
        if candidate_set.field_id != field_spec.field_id:
            raise InfrastructureError(
                "IndependentStrategy: candidate strategy returned CandidateSet "
                f"for field {candidate_set.field_id!r}, expected "
                f"{field_spec.field_id!r}",
            )
        return candidate_set

    candidates_by_key: dict[str, Candidate] = {}
    producer_ids_by_key: dict[str, list[str]] = {}
    for candidate_set in candidate_sets:
        if candidate_set.field_id != field_spec.field_id:
            raise InfrastructureError(
                "IndependentStrategy: candidate strategy returned CandidateSet "
                f"for field {candidate_set.field_id!r}, expected "
                f"{field_spec.field_id!r}",
            )
        for candidate in candidate_set.candidates:
            key = _candidate_merge_key(candidate)
            if key not in candidates_by_key:
                candidates_by_key[key] = candidate
                producer_ids_by_key[key] = [candidate.source_id]
                continue
            if candidate.source_id not in producer_ids_by_key[key]:
                producer_ids_by_key[key].append(candidate.source_id)

    merged_candidates = tuple(
        candidate.model_copy(update={"source_id": "|".join(producer_ids_by_key[key])})
        for key, candidate in candidates_by_key.items()
    )
    strategy_id = "composite:" + stable_hash(
        [candidate_set.strategy_id for candidate_set in candidate_sets],
    )
    return build_candidate_set(
        field_id=field_spec.field_id,
        document_id=document_id,
        candidates=merged_candidates,
        strategy_id=strategy_id,
        instance_hint=None,
    )


def _rendered_prompt_chars(rendered: RenderedPrompt) -> int:
    """Estimate provider request size from rendered prompt payload.

    The messages carry the dominant text payload. The structured output
    schema also crosses the provider boundary, so include its serialized
    size to keep the planner conservative.
    """

    message_chars = sum(len(message.content) for message in rendered.messages)
    schema_chars = (
        len(json.dumps(rendered.structured_output_schema, sort_keys=True))
        if rendered.structured_output_schema is not None
        else 0
    )
    return message_chars + schema_chars


def _selected_candidate_ids_in_source_order(
    *,
    observations: tuple[Observation, ...],
    candidate_set: CandidateSet,
) -> tuple[str, ...]:
    selected = {
        candidate_id
        for observation in observations
        for candidate_id in observation.selected_candidate_ids
    }
    return tuple(
        candidate.candidate_id
        for candidate in candidate_set.candidates
        if candidate.candidate_id in selected
    )


def _priority_winner_candidate_id(
    *,
    candidate_set: CandidateSet,
    observations: tuple[Observation, ...],
    reducer: DocumentClassificationReducerPolicy,
) -> str | None:
    selected_ids = {
        candidate_id
        for observation in observations
        if not observation.abstain
        for candidate_id in observation.selected_candidate_ids
    }
    if not selected_ids:
        return None

    literal_by_candidate_id: dict[str, str] = {}
    for candidate in candidate_set.candidates:
        payload = candidate.structured_payload
        literal = payload.get("literal") if payload is not None else None
        if isinstance(literal, str):
            literal_by_candidate_id[candidate.candidate_id] = literal

    priority_index = {literal: index for index, literal in enumerate(reducer.priority)}
    ranked = sorted(
        (
            (priority_index[literal_by_candidate_id[candidate_id]], candidate_id)
            for candidate_id in selected_ids
            if candidate_id in literal_by_candidate_id
            and literal_by_candidate_id[candidate_id] in priority_index
        ),
        key=lambda item: item[0],
    )
    if not ranked:
        raise InfrastructureError(
            "document_classification.reducer_no_ranked_selection: selected candidate ids "
            f"{sorted(selected_ids)!r} do not map to reducer priority literals",
        )
    return ranked[0][1]


def _selector_producer_version(selector: object) -> str:
    version = getattr(selector, "producer_version", None)
    if isinstance(version, str) and version:
        return version
    return "code:batch_selector_shard_reducer"


def _is_batch_selector(selector: object) -> bool:
    return callable(getattr(selector, "select_many", None))


def _is_literal_set_category_field(field_spec: FieldSpec) -> bool:
    if field_spec.value_kind.name != "CATEGORY" or not field_spec.literal_values:
        return False
    return any(
        binding.cls is LiteralSetCandidateStrategy for binding in field_spec.strategy_bindings
    )


def _candidate_merge_key(candidate: Candidate) -> str:
    return stable_hash(
        (
            candidate.text,
            candidate.source_span.model_dump(mode="json"),
            tuple(span.model_dump(mode="json") for span in candidate.evidence_spans),
            candidate.normalized_hint,
            candidate.source_kind,
            candidate.entity_type,
        ),
    )


def _escalate_validation_failure(failure: ValidationFailure) -> NegativeOutcome:
    """map a layer-2 `ValidationFailure(layer="field", ...)` to the
    canonical phase-1 `NegativeOutcome` per the brief.

    fixed mapping:

    - `category="validation"`
    - `code="field_failure"`
    - `field_id=<failure.field_id>`
    - `instance_key=None` (independent strategy has no per-instance
      iteration; final assignment lives at G.resolver)
    - `reason=<failure.reason>`
    - `candidate_count=None`

    no retry, no rewrap to `ValidationFailure`, no policy lookup at
    the strategy layer — `ExecutorPolicy.on_validation_failure="fail"`
    is the only supported value in phase 1.
    """

    return NegativeOutcome(
        category="validation",
        code="field_failure",
        field_id=failure.field_id,
        instance_key=None,
        reason=failure.reason,
        candidate_count=None,
    )


def _log_validation_rejected(
    *,
    document_view: DocumentView,
    spec: ExtractionSpec,
    outcome: NegativeOutcome,
) -> None:
    logger.info(
        "extractx.validation.rejected",
        extra={
            "extractx_event": "validation.rejected",
            "document_id": document_view.document_id,
            "spec_version": spec.version,
            "field_id": outcome.field_id,
            "instance_id": (
                None if outcome.instance_key is None else outcome.instance_key.group_id
            ),
            "outcome": outcome.category,
            "negative_code": outcome.code,
        },
    )
