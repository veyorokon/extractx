"""`SerialExecutor` per docs/architecture.md §11. M8 phase-1 implementation.

this is the runnable v1 executor. it is the **single** writer of
`Extraction` and `ExecutionTrace` for the supported phase-1 path.

phase-1 supported surface (intentionally narrow per the M8 brief):

- `document`: `str` (UTF-8 encoded) or `bytes`. both are adapted via
  the landed `TextAdapter` (linearizable subcontract, ADR-0006).
- `policy.strategy`: `"independent"`, `"iterative"`, or `"batch"`.
  `"batch"` routes the initial selection pass through the batch selector
  surface. `policy.repair_enabled` composes one bounded repair round for
  single-instance specs.
- `FieldSpec.strategy_bindings[*].kind`: `"candidate"` only.
- `FieldSpec.strategy_bindings[*].cls`: `RegexCandidateStrategy` (or a
  subclass) only.

unsupported paths fail loudly **before the run begins** with
`InfrastructureError`. this is the brief's section 2 invariant: typed
`NegativeOutcome` is for *runtime* surfacing of structured failure;
unsupported execution shapes are setup-time defects of the supported
phase-1 surface and must surface as `InfrastructureError` with a
diagnostic message naming the broken contract.

`schema_cls` handoff:

- the executor reads `lookup_schema_cls(spec.version)` from the
  in-process registry seeded by `ExtractionSpec.from_pydantic(...)`.
- pydantic-backed specs (`spec.source_schema_ref is not None`) require
  a registered class; absence raises `InfrastructureError` at executor
  setup.
- manual specs (`spec.source_schema_ref is None`) get `schema_cls=None`
  and seam F dispatches to the manual `ValidationBinding` path.

`ValidationFailure` routing:

- the strategy escalates every seam-F `ValidationFailure(layer="field",
  ...)` to a typed `NegativeOutcome` per the brief; the executor
  receives that already-escalated negative in the strategy output's
  `pre_resolver_negatives` and merges it into the sole final instance
  (or surfaces it via `ExecutionTrace` when the run resolved to zero
  instances).

`Extraction` assembly (phase-1, fixed):

- `document_id = document_view.document_id`
- `spec_version = spec.version`
- `strategy = policy.strategy`
- `instances = <merged final instances>`
- `trace = ExecutionTrace(trace_id=<deterministic>, events=<minimal>)`
- `replay_artifact_ref = ""`

`trace_id` composition (deterministic): `stable_hash` over the run contract
fields. no wall-clock, no uuid4, no provider state.

outcome rollup:

- `complete` iff `instances != ()` and every `Instance.outcome
  == "complete"`.
- `partial` iff any instance is `partial` (or pre-resolver negatives
  flipped a previously-complete instance).
- `failed` iff `instances == ()`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel

import extractx as _extractx_pkg
from extractx.candidates.generators.literal_set import LiteralSetCandidateStrategy
from extractx.candidates.generators.ner import NerCandidateStrategy
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.candidates.generators.regex import (
    algorithmic_code_hash as _candidate_strategy_code_hash,
)
from extractx.core.cardinality import Cardinality
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import (
    CandidateSet,
    DocumentView,
    ExtractionSpec,
    FieldSpec,
    Observation,
    SourceRef,
    UsageEvent,
)
from extractx.core.outcomes import (
    ExecutionTrace,
    Extraction,
    Instance,
    NegativeOutcome,
    ValidatedField,
    ValidationFailure,
)
from extractx.core.versions import stable_hash
from extractx.execution.deferred import (
    DeferredProvider,
    DeferredResults,
    DeferredSubmission,
    DeferredSubmissionManifest,
    ExecutionMode,
    RenderedDeferredSubmission,
    deferred_submission_manifest_from_rendered,
    validate_deferred_collect_contract,
)
from extractx.extras.pydantic_ai import (
    LLMInstanceProposer,
    PydanticAIBatchSelector,
    PydanticAISelector,
)
from extractx.instances.resolvers.deterministic import (
    algorithmic_code_hash as _resolver_code_hash,
)
from extractx.proposals import validation as _validation_module
from extractx.proposals.validation import LayeredProposalValidator
from extractx.replay.artifact import ReplayArtifact
from extractx.replay.diagnostics import SelectorCallDiagnostic
from extractx.replay.schema import CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION
from extractx.replay.writer import ReplayArtifactWriter
from extractx.schema._schema_cls_registry import lookup_schema_cls
from extractx.schema.summary import summarize_spec
from extractx.selection.algorithmic.singleton import (
    algorithmic_code_hash as _selector_code_hash,
)
from extractx.source.adapters.text import TextAdapter

from ..manifest import RunManifest
from ..policy import PolicySummary
from ..strategies.independent import IndependentStrategy, StrategyOutput

if TYPE_CHECKING:
    from extractx.storage.protocol import ExtractxStore

    from ..policy import ExecutorPolicy
    from ..runtime import Runtime

__all__ = ["SerialExecutor"]

logger = logging.getLogger(__name__)


# phase-1 `runtime_bindings_summary` is a constant pin per the M9 phase-1
# brief §5: the algorithmic slice has no soft providers, so the bindings
# summary is `stable_hash(("algorithmic_v1",))`. when the soft-compute
# thread lands, this composition widens.
_PHASE1_RUNTIME_BINDINGS_SUMMARY = stable_hash(("algorithmic_v1",))


# stable, deterministic source-id used for documents that arrive at the
# executor as raw `bytes` / `str` (i.e., the caller did not supply a
# pre-built `DocumentView`). chosen to be opaque and not collide with
# real source identifiers; document_id is `f"{source_id}@{content_hash}"`
# so two callers passing the same bytes get the same `document_id`.
_PHASE1_DEFAULT_SOURCE_ID = "extractx.execution.serial:phase1"


class SerialExecutor:
    """phase-1 in-process serial executor.

    structural `Executor` subtype. holds a pre-constructed
    `IndependentStrategy` for the phase-1 supported path; all other
    strategies are rejected at the pre-run gate.

    `storage` is an executor-owned infrastructure binding, not a step
    capability — it is **not** carried on `Runtime` and does not
    widen seam J's capability list (M9 phase-1 hard pin #4). when
    `storage` is `None`, behavior is byte-identical to M8: no
    persistence, no filesystem writes, `replay_artifact_ref == ""`.
    when `storage` is bound, the executor persists source / spec /
    replay / manifest and rebuilds `Extraction` with a populated
    `replay_artifact_ref`.
    """

    def __init__(self, *, storage: ExtractxStore | None = None) -> None:
        self._strategy = IndependentStrategy()
        # phase-1 pin: the executor is the sole layer-3 call site
        # (ADR-0003). instantiating directly avoids widening
        # `Runtime` or introducing protocol injection in this thread.
        self._validator = LayeredProposalValidator()
        # storage is the sole opt-in persistence trigger; presence of
        # the binding is the trigger (no `ExecutorPolicy.persist`
        # knob, M9 phase-1 hard pin #11).
        self._storage = storage
        # writer + reader are stateless / pure; constructing once per
        # executor is fine.
        self._artifact_writer = ReplayArtifactWriter()

    async def execute(
        self,
        document: bytes | str,
        spec: ExtractionSpec,
        runtime: Runtime,
        policy: ExecutorPolicy,
    ) -> Extraction | DeferredSubmission:
        """run one extraction end-to-end on the supported phase-1 path.

        unsupported execution paths fail with `InfrastructureError`
        before the run begins. after the pre-run gate accepts inputs,
        nothing else is raised to the caller — failures become typed
        `NegativeOutcome` or `ValidationFailure` (the latter escalated
        per `ExecutorPolicy.on_validation_failure="fail"`).

        when `self._storage` is bound, the executor persists source /
        spec / replay / manifest after the in-memory result is
        assembled, then rebuilds the result with a populated
        `replay_artifact_ref`. persistence happens **after** in-memory
        assembly; if a write raises, the exception propagates as
        `InfrastructureError` (no silent fallback).
        """

        # pre-run gate — reject unsupported execution surfaces loudly
        # before any seam runs.
        self._validate_supported_policy(policy)
        self._validate_supported_spec(spec, policy=policy)
        self._validate_runtime_capabilities(spec, runtime)
        document_view, raw_bytes = self._adapt_supported_document(document)
        schema_cls = self._resolve_schema_cls(spec)
        if policy.execution_mode == ExecutionMode.DEFERRED.value:
            return await self._execute_deferred(
                document_view=document_view,
                spec=spec,
                runtime=runtime,
                policy=policy,
            )
        logger.info(
            "extractx.extraction.started",
            extra={
                "extractx_event": "extraction.started",
                "document_id": document_view.document_id,
                "spec_version": spec.version,
                "strategy": policy.strategy,
                "field_count": len(spec.fields),
                "storage_bound": self._storage is not None,
            },
        )

        batch_select = policy.strategy == "batch"
        repair_enabled = policy.repair_enabled

        # run the independent strategy. `policy.repair_enabled` composes
        # one bounded repair round around this canonical pass below; it
        # does not mutate the independent strategy contract. batch
        # policy routes the initial soft-selection pass through the
        # batch selector surface.
        output = self._strategy.run(
            document_view=document_view,
            spec=spec,
            schema_cls=schema_cls,
            runtime=runtime,
            batch_select=batch_select,
        )
        if repair_enabled:
            output = self._repair_field_failures_once(
                output=output,
                document_view=document_view,
                spec=spec,
                schema_cls=schema_cls,
                runtime=runtime,
            )

        # canonical seam-F layer 3 — runs exactly once per resolved
        # `Instance`, post-`G.resolver`, executor-owned per
        # ADR-0003. failures translate to `NegativeOutcome(
        # category="validation", code="instance_failure", ...)` and
        # are appended to the affected instance immutably; success-
        # path returns the original `Instance` reference
        # unchanged.
        layer3_instances = self._apply_layer3_validation(
            final_instances=output.final_instances,
            spec=spec,
            schema_cls=schema_cls,
        )
        if repair_enabled:
            output, layer3_instances = self._repair_layer3_once(
                output=output,
                layer3_instances=layer3_instances,
                document_view=document_view,
                spec=spec,
                schema_cls=schema_cls,
                runtime=runtime,
            )

        # assemble the final `Extraction` from the strategy
        # output. attachment of pre-resolver negatives, outcome
        # rollup, and trace assembly are all executor-owned.
        result = self._assemble_result(
            document_view=document_view,
            spec=spec,
            strategy=policy.strategy,
            repair_enabled=repair_enabled,
            final_instances=layer3_instances,
            pre_resolver_negatives=output.pre_resolver_negatives,
            usage_events=output.usage_events,
        )

        # opt-in persistence — phase-1 hard pin #11: presence of
        # `self._storage` is the sole trigger. when unbound, behavior
        # matches M8 byte-identically.
        if self._storage is None:
            self._log_completed(result)
            return result

        persisted = self._persist_run(
            result=result,
            spec=spec,
            policy=policy,
            output=output,
            layer3_instances=layer3_instances,
            document_view=document_view,
            raw_bytes=raw_bytes,
        )
        self._log_completed(persisted)
        return persisted

    async def collect_deferred_submission(
        self,
        *,
        document: bytes | str,
        spec: ExtractionSpec,
        runtime: Runtime,
        policy: ExecutorPolicy,
        manifest: DeferredSubmissionManifest,
        results: DeferredResults,
    ) -> Extraction:
        """Collect a completed deferred submission into an `Extraction`.

        The collect path re-runs deterministic setup, validates that the
        manifest still matches the spec/document/request set, then resolves
        recorded provider responses through the same selector and validation
        seams as immediate execution.
        """

        self._validate_supported_policy(policy)
        self._validate_supported_spec(spec, policy=policy)
        self._validate_runtime_capabilities(spec, runtime)
        if policy.execution_mode != ExecutionMode.DEFERRED.value:
            raise InfrastructureError(
                "deferred_collect.policy_mismatch: collect requires "
                "ExecutorPolicy.execution_mode='deferred'",
            )
        if policy.strategy != "batch":
            raise InfrastructureError(
                "deferred_collect.policy_mismatch: collect currently requires "
                "ExecutorPolicy.strategy='batch'",
            )
        if results.failed:
            raise InfrastructureError(
                "deferred_collect.partial_results_unsupported: collect-to-Extraction "
                "requires all deferred requests to succeed; failed_request_ids="
                f"{sorted(results.failed)!r}",
            )

        document_view, raw_bytes = self._adapt_supported_document(document)
        schema_cls = self._resolve_schema_cls(spec)
        validate_deferred_collect_contract(
            manifest=manifest,
            results=results,
            spec_hash=spec.version,
            document_id=document_view.document_id,
            document_content_hash=document_view.source_ref.content_hash,
        )

        output = self._strategy.collect_deferred_batch_soft_calls(
            document_view=document_view,
            spec=spec,
            schema_cls=schema_cls,
            runtime=runtime,
            manifest_requests=manifest.requests,
            successful_responses=results.successful,
        )
        layer3_instances = self._apply_layer3_validation(
            final_instances=output.final_instances,
            spec=spec,
            schema_cls=schema_cls,
        )
        result = self._assemble_result(
            document_view=document_view,
            spec=spec,
            strategy=policy.strategy,
            repair_enabled=False,
            final_instances=layer3_instances,
            pre_resolver_negatives=output.pre_resolver_negatives,
            usage_events=output.usage_events,
        )
        if self._storage is None:
            self._log_completed(result)
            return result

        persisted = self._persist_run(
            result=result,
            spec=spec,
            policy=policy,
            output=output,
            layer3_instances=layer3_instances,
            document_view=document_view,
            raw_bytes=raw_bytes,
        )
        self._log_completed(persisted)
        return persisted

    def render_deferred_submission(
        self,
        *,
        document: bytes | str,
        spec: ExtractionSpec,
        runtime: Runtime,
        policy: ExecutorPolicy,
    ) -> RenderedDeferredSubmission:
        """Render one document's deferred soft calls without provider submission."""

        self._validate_supported_policy(policy)
        self._validate_supported_spec(spec, policy=policy)
        self._validate_runtime_capabilities(spec, runtime)
        if policy.execution_mode != ExecutionMode.DEFERRED.value:
            raise InfrastructureError(
                "deferred_render.policy_mismatch: render requires "
                "ExecutorPolicy.execution_mode='deferred'",
            )
        if policy.strategy != "batch":
            raise InfrastructureError(
                "deferred_render.policy_mismatch: render currently requires "
                "ExecutorPolicy.strategy='batch'",
            )

        document_view, _ = self._adapt_supported_document(document)
        requests = self._strategy.render_deferred_batch_soft_calls(
            document_view=document_view,
            spec=spec,
            runtime=runtime,
        )
        return RenderedDeferredSubmission(
            spec_hash=spec.version,
            document_id=document_view.document_id,
            document_content_hash=document_view.source_ref.content_hash,
            requests=requests,
        )

    @staticmethod
    def _log_completed(result: Extraction) -> None:
        logger.info(
            "extractx.extraction.completed",
            extra={
                "extractx_event": "extraction.completed",
                "document_id": result.document_id,
                "spec_version": result.spec_version,
                "outcome": result.outcome,
                "strategy": result.strategy,
                "instance_count": len(result.instances),
                "replay_artifact_ref": result.replay_artifact_ref,
            },
        )

    # ------------------------------------------------------------------
    # persistence (M9 phase-1)
    # ------------------------------------------------------------------

    def _persist_run(
        self,
        *,
        result: Extraction,
        spec: ExtractionSpec,
        policy: ExecutorPolicy,
        output: StrategyOutput,
        layer3_instances: tuple[Instance, ...],
        document_view: DocumentView,
        raw_bytes: bytes,
    ) -> Extraction:
        """persist source / spec / replay / manifest and rebuild the
        result with a populated `replay_artifact_ref`.

        single source of truth for manifest construction is
        `RunManifest.from_artifact(...)` per M9 phase-1 hard pin #3 —
        the manifest is **derived** from the artifact, never assembled
        from raw run state independently.

        failed runs (`outcome="failed"`, `instances=()`) still persist
        artifact + manifest + source + spec_summary blobs (M9 phase-1
        hard pin #12 — failed runs are diagnostically valuable).
        """

        assert self._storage is not None  # narrowed by caller

        artifact = self._build_replay_artifact(
            result=result,
            spec=spec,
            policy=policy,
            output=output,
            layer3_instances=layer3_instances,
            document_view=document_view,
        )

        artifact_blob = self._artifact_writer.serialize(artifact)
        artifact_id = self._artifact_writer.compute_artifact_id(artifact_blob)

        # source bytes go under their content hash so identical
        # documents share storage across runs.
        self._storage.put_object(
            "source",
            document_view.source_ref.content_hash,
            raw_bytes,
        )

        # `objects/spec/<spec-version>.json` carries `SpecSummary`,
        # **not** `ExtractionSpec` (M9 phase-1 hard pin #2).
        summary = summarize_spec(spec)
        summary_blob = summary.model_dump_json().encode("utf-8")
        self._storage.put_object("spec", spec.version, summary_blob)

        # replay artifact under its content-addressed id.
        self._storage.put_object("replay", artifact_id, artifact_blob)

        # SOLE manifest construction site — `from_artifact` is the
        # only allowed path per M9 phase-1 hard pin #3. white-box
        # tests assert no other constructor call exists in this file.
        manifest = RunManifest.from_artifact(
            artifact,
            run_id=str(uuid.uuid4()),
            replay_ref=artifact_id,
        )
        manifest_blob = manifest.model_dump_json().encode("utf-8")
        self._storage.put_manifest(manifest.run_id, manifest_blob)

        # rebuild the result immutably with the populated
        # `replay_artifact_ref` (replacing the `""` set during
        # in-memory assembly).
        return Extraction(
            document_id=result.document_id,
            spec_version=result.spec_version,
            outcome=result.outcome,
            strategy=result.strategy,
            instances=result.instances,
            trace=result.trace,
            replay_artifact_ref=artifact_id,
            usage_events=result.usage_events,
        )

    def _build_replay_artifact(
        self,
        *,
        result: Extraction,
        spec: ExtractionSpec,
        policy: ExecutorPolicy,
        output: StrategyOutput,
        layer3_instances: tuple[Instance, ...],
        document_view: DocumentView,
    ) -> ReplayArtifact:
        """assemble the `ReplayArtifact` from the gathered run state.

        producer-version keys: `"candidate_strategy"`, `"selector"`,
        `"resolver"` (M9 phase-1) plus `"validator"` (replay drift-gate
        phase 1) covering `LayeredProposalValidator`. `"planner"` /
        `"strategy"` / `"executor"` remain unwidened.

        the `"validator"` value is sourced via the
        `extractx.proposals.validation` module attribute so monkey-
        patches at the module level surface during replay (mirrors the
        engine's `_live_producer_versions()` composition pattern).
        """

        producer_versions: dict[str, str] = {
            "candidate_strategy": _candidate_strategy_code_hash(),
            "selector": _selector_code_hash(),
            "resolver": _resolver_code_hash(),
            "validator": _validation_module.algorithmic_code_hash(),
        }
        policy_summary = PolicySummary(
            strategy=policy.strategy,
            execution_mode=policy.execution_mode,
            repair=policy.repair,
            on_validation_failure=policy.on_validation_failure,
            capture_interview_transcripts=policy.capture_interview_transcripts,
        )

        return ReplayArtifact(
            schema_version=CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION,
            extractx_version=_extractx_pkg.__version__,
            source_ref=document_view.source_ref,
            document_id=document_view.document_id,
            spec_version=spec.version,
            strategy=policy.strategy,
            outcome=result.outcome,
            producer_versions=producer_versions,
            policy_summary=policy_summary,
            runtime_bindings_summary=_PHASE1_RUNTIME_BINDINGS_SUMMARY,
            candidate_sets=output.candidate_sets,
            instance_candidate_set=output.instance_candidate_set,
            instance_proposer_response=output.instance_proposer_response,
            instance_proposer_metadata=output.instance_proposer_metadata,
            observations=output.observations,
            selector_call_diagnostics=output.selector_call_diagnostics,
            validated_fields=output.validated_fields,
            pre_resolver_negatives=output.pre_resolver_negatives,
            final_instances=layer3_instances,
            usage_events=output.usage_events,
            trace=result.trace,
        )

    # ------------------------------------------------------------------
    # pre-run gate
    # ------------------------------------------------------------------

    def _validate_supported_policy(self, policy: ExecutorPolicy) -> None:
        """reject unsupported `ExecutorPolicy` shapes loudly.

        `strategy="independent"` is the single-pass path.
        `strategy="batch"` keeps candidate generation / validation unchanged
        while routing soft selection through the batch selector surface.
        `policy.repair_enabled` enables the bounded object-repair slice.
        one post-layer-3 retry round for single-instance specs. interview
        capture must remain `False` because the capture path is owned by a
        later thread; flipping it on without an implementation is silently
        dishonest.
        """

        if policy.strategy not in {"independent", "iterative", "batch"}:
            raise InfrastructureError(
                "SerialExecutor: supports only "
                "ExecutorPolicy.strategy='independent', 'iterative', or 'batch'; got "
                f"{policy.strategy!r}",
            )
        supported_execution_modes = {
            ExecutionMode.IMMEDIATE.value,
            ExecutionMode.DEFERRED.value,
        }
        if policy.execution_mode not in supported_execution_modes:
            raise InfrastructureError(
                "SerialExecutor: supports only "
                "ExecutorPolicy.execution_mode='immediate' or 'deferred'; got "
                f"{policy.execution_mode!r}",
            )
        if policy.execution_mode == ExecutionMode.DEFERRED.value and policy.repair_enabled:
            raise InfrastructureError(
                "SerialExecutor: execution_mode='deferred' does not support "
                "repair=True yet; chained deferred repair is a later contract",
            )
        if policy.capture_interview_transcripts:
            raise InfrastructureError(
                "SerialExecutor: phase-1 does not implement interview "
                "capture; ExecutorPolicy.capture_interview_transcripts "
                "must remain False until the capture thread lands",
            )
        if policy.on_validation_failure != "fail":
            # the type system already restricts this literal to "fail",
            # but a runtime guard keeps the contract honest if a future
            # widening of the literal sneaks past pyright.
            raise InfrastructureError(
                "SerialExecutor: phase-1 supports only "
                "ExecutorPolicy.on_validation_failure='fail'; got "
                f"{policy.on_validation_failure!r}",
            )

    async def _execute_deferred(
        self,
        *,
        document_view: DocumentView,
        spec: ExtractionSpec,
        runtime: Runtime,
        policy: ExecutorPolicy,
    ) -> DeferredSubmission:
        if policy.strategy != "batch":
            raise InfrastructureError(
                "SerialExecutor: execution_mode='deferred' currently requires "
                "ExecutorPolicy.strategy='batch'",
            )
        deferred_provider = runtime.deferred_provider
        if deferred_provider is None:
            raise InfrastructureError(
                "SerialExecutor: execution_mode='deferred' requires "
                "Runtime.deferred_provider",
            )
        provider = cast("DeferredProvider", deferred_provider)
        submit = getattr(provider, "submit", None)
        if not callable(submit):
            raise InfrastructureError(
                "SerialExecutor: Runtime.deferred_provider must expose async submit(...)",
            )

        rendered = RenderedDeferredSubmission(
            spec_hash=spec.version,
            document_id=document_view.document_id,
            document_content_hash=document_view.source_ref.content_hash,
            requests=self._strategy.render_deferred_batch_soft_calls(
                document_view=document_view,
                spec=spec,
                runtime=runtime,
            ),
        )
        handle = await provider.submit(rendered.requests)
        manifest = deferred_submission_manifest_from_rendered(rendered, handle=handle)
        return DeferredSubmission(
            manifest=manifest,
            handle=handle,
            submitted_at=handle.submitted_at,
            spec_hash=spec.version,
            request_count=len(rendered.requests),
        )

    def _validate_supported_spec(
        self,
        spec: ExtractionSpec,
        *,
        policy: ExecutorPolicy,
    ) -> None:
        """reject specs whose fields exercise an unsupported path.

        phase-1 supports only fields with explicit
        `StrategyBinding.kind == "candidate"` whose `cls` is
        `RegexCandidateStrategy` / `NerCandidateStrategy` /
        `LiteralSetCandidateStrategy` (or a subclass). every other shape fails
        at executor setup with `InfrastructureError` before any seam runs.
        """

        if spec.instance_cardinality is Cardinality.MANY:
            if policy.repair_enabled or policy.strategy == "batch":
                raise InfrastructureError(
                    f"SerialExecutor: ExecutorPolicy.strategy={policy.strategy!r} "
                    "currently supports only single-instance specs; "
                    "multi-instance planning lands with the "
                    "resolution-policy thread",
                )
            binding = spec.instance_proposer_binding
            if binding is None:
                raise InfrastructureError(
                    "SerialExecutor: Cardinality.MANY requires "
                    "ExtractionSpec.instance_proposer_binding",
                )
            proposer_cls = binding.cls
            if proposer_cls is not LLMInstanceProposer and not issubclass(
                proposer_cls,
                LLMInstanceProposer,
            ):
                raise InfrastructureError(
                    "SerialExecutor: Cardinality.MANY supports only "
                    "LLMInstanceProposer in phase 2; got "
                    f"{proposer_cls!r}",
                )
        elif spec.instance_cardinality is not Cardinality.ONE:
            raise InfrastructureError(
                "SerialExecutor: unsupported "
                "ExtractionSpec.instance_cardinality; "
                f"got {spec.instance_cardinality!r}. "
            )
        for field_spec in spec.fields:
            self._validate_supported_field(field_spec)

    def _validate_runtime_capabilities(
        self,
        spec: ExtractionSpec,
        runtime: Runtime,
    ) -> None:
        """reject supported specs whose declared producers lack runtime bindings.

        Selector bindings are per-field. A pydantic-ai selector may take
        an explicit provider in its binding params for tests, but the
        normal runtime path is `Runtime.llm`; missing both is a setup
        defect and must fail before candidate generation starts.
        """

        for field_spec in spec.fields:
            binding = field_spec.selector_binding
            if binding is None:
                continue
            selector_cls = binding.cls
            if (
                (
                    selector_cls is PydanticAISelector
                    or issubclass(selector_cls, PydanticAISelector)
                    or selector_cls is PydanticAIBatchSelector
                    or issubclass(selector_cls, PydanticAIBatchSelector)
                )
                and runtime.llm is None
                and "provider" not in binding.params
            ):
                raise InfrastructureError(
                    "selector.missing_llm: field "
                    f"{field_spec.field_id!r} is bound to a pydantic-ai selector "
                    "but Runtime.llm is not set",
                )
        if spec.instance_cardinality is Cardinality.MANY:
            binding = spec.instance_proposer_binding
            if binding is not None:
                proposer_cls = binding.cls
                if (
                    (
                        proposer_cls is LLMInstanceProposer
                        or issubclass(proposer_cls, LLMInstanceProposer)
                    )
                    and runtime.llm is None
                    and "provider" not in binding.params
                ):
                    raise InfrastructureError(
                        "instance_proposer.missing_llm: spec is bound to "
                        "LLMInstanceProposer but Runtime.llm is not set",
                    )

    def _validate_supported_field(self, field_spec: FieldSpec) -> None:
        """fail loudly when a `FieldSpec` falls outside the phase-1 surface."""

        if not field_spec.strategy_bindings:
            raise InfrastructureError(
                "SerialExecutor: phase-1 requires explicit "
                "candidate strategy bindings; field "
                f"{field_spec.field_id!r} has strategy_bindings=()",
            )
        for binding in field_spec.strategy_bindings:
            if binding.kind == "grounded_proposal":
                raise InfrastructureError(
                    "SerialExecutor: phase-1 does not support seam C.alt "
                    "grounded_proposal bindings; field "
                    f"{field_spec.field_id!r} declares "
                    "StrategyBinding.kind='grounded_proposal'",
                )
            if binding.kind != "candidate":
                raise InfrastructureError(
                    "SerialExecutor: phase-1 supports only "
                    "StrategyBinding.kind='candidate'; field "
                    f"{field_spec.field_id!r} declares "
                    f"StrategyBinding.kind={binding.kind!r}",
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
                    "SerialExecutor: phase-1 supports only "
                    "RegexCandidateStrategy, NerCandidateStrategy, or "
                    "LiteralSetCandidateStrategy bindings; field "
                    f"{field_spec.field_id!r} declares "
                    f"StrategyBinding.cls={cls!r}",
                )

    def _adapt_supported_document(
        self,
        document: bytes | str,
    ) -> tuple[DocumentView, bytes]:
        """adapt a `str` / `bytes` document via `TextAdapter` and
        return both the adapted view and the raw input bytes.

        the raw bytes are returned alongside the view so opt-in
        persistence (M9 phase-1) can write them under
        `objects/source/<content-hash>.bin` without re-deriving them
        from the view. callers that do not persist (M8 path) ignore
        the second tuple element.

        non-`str` / non-`bytes` inputs are unsupported and raise
        `InfrastructureError` per the brief's section 2. `str` is
        UTF-8 encoded before adaptation; `bytes` is passed straight
        through.

        the runtime type guard exists so that callers who reach
        `run_extraction(...)` with an `Any`-typed value (e.g. a
        `dict`, a `BytesIO`, `None`) still surface the documented
        `InfrastructureError` rather than a generic `AttributeError`
        from inside `TextAdapter`.
        """

        if isinstance(document, bytes):
            raw_bytes = document
        elif isinstance(
            document,
            str,
        ):  # pyright: ignore[reportUnnecessaryIsInstance]
            # explicit runtime check: callers may pass arbitrary
            # `Any`-typed values through the `run_extraction` entry
            # point; the static type narrowing is not load-bearing.
            raw_bytes = document.encode("utf-8")
        else:
            raise InfrastructureError(
                "SerialExecutor: phase-1 supports only str / bytes "
                f"document inputs; got {type(document).__name__!r}",
            )

        # deterministic SourceRef — content_hash pins the document
        # identity so identical inputs map to identical document_ids,
        # supporting the determinism clause.
        content_hash = stable_hash(raw_bytes.decode("utf-8", errors="strict"))
        source_ref = SourceRef(
            source_id=_PHASE1_DEFAULT_SOURCE_ID,
            content_hash=content_hash,
        )
        return TextAdapter().adapt(raw_bytes, source_ref), raw_bytes

    def _resolve_schema_cls(self, spec: ExtractionSpec) -> type[BaseModel] | None:
        """resolve the live pydantic class for the run, if any.

        - `spec.source_schema_ref is None` → manual spec; return
          `None`. seam F's manual path runs.
        - `spec.source_schema_ref is not None` → pydantic-backed spec.
          look up the live class in the in-process registry by
          `spec.version`. miss raises `InfrastructureError`.

        this is the documented executor-owned schema_cls handoff. the
        registry is seeded by `ExtractionSpec.from_pydantic(...)` and
        keyed by `spec.version`; we **never** resolve from
        `spec.source_schema_ref.ref` (that string is a stable reference
        token, not an import path).
        """

        if spec.source_schema_ref is None:
            return None
        live = lookup_schema_cls(spec.version)
        if live is None:
            raise InfrastructureError(
                "SerialExecutor: spec claims a pydantic-backed source "
                f"(source_schema_ref={spec.source_schema_ref.ref!r}) "
                "but no live schema class is registered in this process "
                f"under spec.version={spec.version!r}; the executor "
                "does not resolve schema classes from "
                "source_schema_ref. build the spec via "
                "ExtractionSpec.from_pydantic(...) in the same process",
            )
        # narrow `Any` from the registry back to `type[BaseModel]`. the
        # registration site (`from_pydantic`) only registers
        # `BaseModel` subclasses, so the runtime check is defense in
        # depth, not a public-surface tightening.
        if not (isinstance(live, type) and issubclass(live, BaseModel)):
            raise InfrastructureError(
                "SerialExecutor: registered schema class for "
                f"spec.version={spec.version!r} is not a "
                f"BaseModel subclass: {live!r}",
            )
        return live

    # ------------------------------------------------------------------
    # canonical seam-F layer 3 (post-resolver)
    # ------------------------------------------------------------------

    def _apply_layer3_validation(
        self,
        *,
        final_instances: tuple[Instance, ...],
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None,
    ) -> tuple[Instance, ...]:
        """invoke seam-F layer 3 once per resolved `Instance`.

        per ADR-0003, layer 3 is the sole instance-layer validation
        phase and runs exactly once per `Instance` that reaches
        layer 3, post-`G.resolver`. the executor is the sole call site
        in phase 1 (no strategy duplication). manual specs
        (`schema_cls is None`) and pydantic-backed specs without
        registered `model_validator(mode="after")` decorators are
        byte-identical pass-through inside the validator.

        on layer-3 success the original `Instance` reference is
        returned unchanged — no defensive rebuild. on layer-3 failure
        a typed `ValidationFailure(layer="instance", ...)` is escalated
        to `NegativeOutcome(category="validation",
        code="instance_failure", ...)` and appended to the instance's
        `negative_outcomes`; the rebuilt instance carries the original
        `instance_key` and `evidence` unchanged. outcome flips
        `complete -> partial`; `partial` stays `partial`.
        """

        if not final_instances:
            return final_instances

        rebuilt: list[Instance] = []
        for instance in final_instances:
            outcome = self._validator.validate_instance(
                instance_result=instance,
                spec=spec,
                schema_cls=schema_cls,
            )
            if isinstance(outcome, ValidationFailure):
                rebuilt.append(_escalate_layer3_failure(instance, outcome))
                continue
            # layer-3 success: validator returns the original
            # `Instance` reference unchanged. preserve identity
            # rather than rebuild defensively.
            rebuilt.append(outcome)
        return tuple(rebuilt)

    def _repair_layer3_once(
        self,
        *,
        output: StrategyOutput,
        layer3_instances: tuple[Instance, ...],
        document_view: DocumentView,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None,
        runtime: Runtime,
    ) -> tuple[StrategyOutput, tuple[Instance, ...]]:
        """perform one bounded object-validator repair round.

        This is the first runnable `iterative` slice. It deliberately does
        not plan field order or mechanically derive candidate filters from
        validators. It consumes the structured object issues already emitted
        by layer 3, retries only implicated fields with those reasons in
        `ContextPack.retry_feedback`, resolves again, then runs layer 3 one
        final time.
        """

        repair_feedback = _object_repair_feedback(layer3_instances)
        if not repair_feedback:
            return output, layer3_instances

        implicated_field_ids = tuple(
            field_spec.field_id
            for field_spec in spec.fields
            if field_spec.field_id in repair_feedback
        )
        if not implicated_field_ids:
            return output, layer3_instances

        candidate_sets_by_field = {
            candidate_set.field_id: candidate_set for candidate_set in output.candidate_sets
        }
        field_specs_by_id = {field_spec.field_id: field_spec for field_spec in spec.fields}

        logger.info(
            "extractx.iterative_repair.started",
            extra={
                "extractx_event": "iterative_repair.started",
                "document_id": document_view.document_id,
                "spec_version": spec.version,
                "field_ids": implicated_field_ids,
                "repair_round": 1,
            },
        )

        repair_observations: list[Observation] = []
        repair_validated: list[ValidatedField] = []
        repair_negatives: list[NegativeOutcome] = []
        usage_events = list(output.usage_events)
        selector_call_diagnostics = list(output.selector_call_diagnostics)

        for field_id in implicated_field_ids:
            field_spec = field_specs_by_id[field_id]
            candidate_set = candidate_sets_by_field[field_id]
            observation = self._select_for_repair(
                field_spec=field_spec,
                candidate_set=candidate_set,
                spec=spec,
                document_view=document_view,
                retry_feedback=repair_feedback[field_id],
                runtime=runtime,
                usage_events=usage_events,
                selector_call_diagnostics=selector_call_diagnostics,
            )
            repair_observations.append(observation)

            validated, negatives = self._strategy.adapt_and_validate(
                observation=observation,
                candidate_set=candidate_set,
                field_spec=field_spec,
                document_view=document_view,
                schema_cls=schema_cls,
            )
            repair_validated.extend(validated)
            repair_negatives.extend(negatives)

        repaired_validated_fields = _replace_validated_fields(
            spec=spec,
            original=output.validated_fields,
            repaired=tuple(repair_validated),
            replaced_field_ids=implicated_field_ids,
        )
        repaired_negatives = tuple(
            negative
            for negative in output.pre_resolver_negatives
            if negative.field_id not in implicated_field_ids
        ) + tuple(repair_negatives)
        final_instances = self._strategy.resolve_instances(
            validated_fields=repaired_validated_fields,
            candidate_sets=output.candidate_sets,
            spec=spec,
            instance_plan=None,
        )
        repaired_layer3_instances = self._apply_layer3_validation(
            final_instances=final_instances,
            spec=spec,
            schema_cls=schema_cls,
        )

        repaired_output = StrategyOutput(
            candidate_sets=output.candidate_sets,
            observations=(*output.observations, *repair_observations),
            validated_fields=repaired_validated_fields,
            pre_resolver_negatives=repaired_negatives,
            final_instances=final_instances,
            instance_candidate_set=output.instance_candidate_set,
            instance_proposer_response=output.instance_proposer_response,
            instance_proposer_metadata=output.instance_proposer_metadata,
            usage_events=tuple(usage_events),
            selector_call_diagnostics=tuple(selector_call_diagnostics),
        )

        logger.info(
            "extractx.iterative_repair.completed",
            extra={
                "extractx_event": "iterative_repair.completed",
                "document_id": document_view.document_id,
                "spec_version": spec.version,
                "field_ids": implicated_field_ids,
                "repair_round": 1,
                "remaining_instance_failure_count": _object_failure_count(
                    repaired_layer3_instances,
                ),
            },
        )
        return repaired_output, repaired_layer3_instances

    def _repair_field_failures_once(
        self,
        *,
        output: StrategyOutput,
        document_view: DocumentView,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None,
        runtime: Runtime,
    ) -> StrategyOutput:
        """perform one bounded field-validation repair round.

        Layer-2 field failures are retryable because the selector may
        have chosen the wrong bounded candidate. The retry does not
        invent values, exclude the rejected candidate, or parse
        validator prose into filters; it reruns selection for the
        affected field over the original `CandidateSet` with the
        validation reason in `ContextPack.retry_feedback`.
        """

        repair_feedback = _field_repair_feedback(output.pre_resolver_negatives)
        if not repair_feedback:
            return output

        field_ids = tuple(
            field_spec.field_id
            for field_spec in spec.fields
            if field_spec.field_id in repair_feedback
        )
        if not field_ids:
            return output

        logger.info(
            "extractx.iterative_field_repair.started",
            extra={
                "extractx_event": "iterative_field_repair.started",
                "document_id": document_view.document_id,
                "spec_version": spec.version,
                "field_ids": field_ids,
                "repair_round": 1,
            },
        )

        repaired = self._retry_fields_once(
            output=output,
            document_view=document_view,
            spec=spec,
            schema_cls=schema_cls,
            runtime=runtime,
            retry_feedback=repair_feedback,
            field_ids=field_ids,
        )

        logger.info(
            "extractx.iterative_field_repair.completed",
            extra={
                "extractx_event": "iterative_field_repair.completed",
                "document_id": document_view.document_id,
                "spec_version": spec.version,
                "field_ids": field_ids,
                "repair_round": 1,
                "remaining_field_failure_count": _field_failure_count(
                    repaired.pre_resolver_negatives,
                ),
            },
        )
        return repaired

    def _select_for_repair(
        self,
        *,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        spec: ExtractionSpec,
        document_view: DocumentView,
        retry_feedback: tuple[str, ...],
        runtime: Runtime,
        usage_events: list[UsageEvent],
        selector_call_diagnostics: list[SelectorCallDiagnostic],
    ) -> Observation:
        """select a repaired observation for one implicated field."""

        return self._strategy.select_observation(
            field_spec=field_spec,
            candidate_set=candidate_set,
            spec=spec,
            document_view=document_view,
            runtime=runtime,
            usage_events=usage_events,
            selector_call_diagnostics=selector_call_diagnostics,
            instance_id="inst_0",
            retry_feedback=retry_feedback,
        )

    def _retry_fields_once(
        self,
        *,
        output: StrategyOutput,
        document_view: DocumentView,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None,
        runtime: Runtime,
        retry_feedback: Mapping[str, tuple[str, ...]],
        field_ids: tuple[str, ...],
    ) -> StrategyOutput:
        candidate_sets_by_field = {
            candidate_set.field_id: candidate_set for candidate_set in output.candidate_sets
        }
        field_specs_by_id = {field_spec.field_id: field_spec for field_spec in spec.fields}

        repair_observations: list[Observation] = []
        repair_validated: list[ValidatedField] = []
        repair_negatives: list[NegativeOutcome] = []
        usage_events = list(output.usage_events)
        selector_call_diagnostics = list(output.selector_call_diagnostics)

        for field_id in field_ids:
            field_spec = field_specs_by_id[field_id]
            candidate_set = candidate_sets_by_field[field_id]
            observation = self._select_for_repair(
                field_spec=field_spec,
                candidate_set=candidate_set,
                spec=spec,
                document_view=document_view,
                retry_feedback=retry_feedback[field_id],
                runtime=runtime,
                usage_events=usage_events,
                selector_call_diagnostics=selector_call_diagnostics,
            )
            repair_observations.append(observation)

            validated, negatives = self._strategy.adapt_and_validate(
                observation=observation,
                candidate_set=candidate_set,
                field_spec=field_spec,
                document_view=document_view,
                schema_cls=schema_cls,
            )
            repair_validated.extend(validated)
            repair_negatives.extend(negatives)

        repaired_validated_fields = _replace_validated_fields(
            spec=spec,
            original=output.validated_fields,
            repaired=tuple(repair_validated),
            replaced_field_ids=field_ids,
        )
        repaired_negatives = tuple(
            negative
            for negative in output.pre_resolver_negatives
            if negative.field_id not in field_ids
        ) + tuple(repair_negatives)
        final_instances = self._strategy.resolve_instances(
            validated_fields=repaired_validated_fields,
            candidate_sets=output.candidate_sets,
            spec=spec,
            instance_plan=None,
        )
        return StrategyOutput(
            candidate_sets=output.candidate_sets,
            observations=(*output.observations, *repair_observations),
            validated_fields=repaired_validated_fields,
            pre_resolver_negatives=repaired_negatives,
            final_instances=final_instances,
            instance_candidate_set=output.instance_candidate_set,
            instance_proposer_response=output.instance_proposer_response,
            instance_proposer_metadata=output.instance_proposer_metadata,
            usage_events=tuple(usage_events),
            selector_call_diagnostics=tuple(selector_call_diagnostics),
        )

    # ------------------------------------------------------------------
    # result assembly
    # ------------------------------------------------------------------

    def _assemble_result(
        self,
        *,
        document_view: DocumentView,
        spec: ExtractionSpec,
        strategy: Literal["independent", "iterative", "batch"],
        repair_enabled: bool,
        final_instances: tuple[Instance, ...],
        pre_resolver_negatives: tuple[NegativeOutcome, ...],
        usage_events: tuple[UsageEvent, ...],
    ) -> Extraction:
        """build the canonical `Extraction` for the run.

        attachment rule (independent strategy, document scope):

        - if `final_instances == ()` → return
          `Extraction(outcome="failed", instances=(), ...)` with
          the pre-resolver negatives surfaced in
          `ExecutionTrace.events` for diagnostic access. do not
          fabricate a negative-only `Instance`.
        - if `final_instances` has at least one instance → attach all
          pre-resolver negatives to the **single** returned instance
          (phase-1 independent strategy with `plan=None` yields at
          most one final instance), rebuilt immutably with appended
          negatives and outcome flipped to `partial` if any negatives
          land.

        the attachment rule deliberately does not consider multi-
        instance independent runs: in phase 1 with `plan=None`, the
        landed deterministic resolver returns at most one instance, so
        any future widening will surface as a `StrategyOutput`
        producing more than one instance and hit the "first instance"
        guard below.
        """

        trace_id = stable_hash(
            (document_view.document_id, spec.version, "serial", strategy, repair_enabled),
        )

        if not final_instances:
            events = self._failed_run_events(pre_resolver_negatives)
            trace = ExecutionTrace(trace_id=trace_id, events=events)
            return Extraction(
                document_id=document_view.document_id,
                spec_version=spec.version,
                outcome="failed",
                strategy=strategy,
                instances=(),
                trace=trace,
                replay_artifact_ref="",
                usage_events=usage_events,
            )

        merged_instances = self._attach_pre_resolver_negatives(
            final_instances=final_instances,
            pre_resolver_negatives=pre_resolver_negatives,
        )
        outcome = self._roll_up_outcome(merged_instances)
        trace = ExecutionTrace(trace_id=trace_id, events=())
        return Extraction(
            document_id=document_view.document_id,
            spec_version=spec.version,
            outcome=outcome,
            strategy=strategy,
            instances=merged_instances,
            trace=trace,
            replay_artifact_ref="",
            usage_events=usage_events,
        )

    def _attach_pre_resolver_negatives(
        self,
        *,
        final_instances: tuple[Instance, ...],
        pre_resolver_negatives: tuple[NegativeOutcome, ...],
    ) -> tuple[Instance, ...]:
        """attach pre-resolver negatives to the sole returned instance.

        phase-1 invariant (independent strategy, `plan=None`): the
        resolver returns at most one final `Instance`. if the
        landed resolver ever returns more than one in phase 1, we
        surface the violation loudly rather than silently fan out the
        negatives across instances.
        """

        if not pre_resolver_negatives:
            return final_instances
        if len(final_instances) != 1:
            raise InfrastructureError(
                "SerialExecutor: phase-1 independent strategy expects "
                "the resolver to return at most one final instance "
                f"with plan=None; got {len(final_instances)}. "
                "pre-resolver negative attachment is undefined for "
                "multi-instance independent runs in this slice",
            )
        sole = final_instances[0]
        merged_negatives = (*sole.negative_outcomes, *pre_resolver_negatives)
        rebuilt = Instance(
            instance_id=sole.instance_id,
            instance_key=sole.instance_key,
            outcome="partial",
            evidence=sole.evidence,
            negative_outcomes=merged_negatives,
            grouping_evidence=sole.grouping_evidence,
        )
        return (rebuilt,)

    def _roll_up_outcome(
        self,
        instances: tuple[Instance, ...],
    ) -> Literal["complete", "partial"]:
        """return the canonical non-failed extraction outcome literal.

        - `complete` iff every instance is `complete`.
        - `partial` iff any instance is `partial`.
        - `failed` is reached only when `instances == ()`, which is
          handled separately in `_assemble_result`.
        """

        for instance in instances:
            if instance.outcome != "complete":
                return "partial"
        return "complete"

    def _failed_run_events(
        self,
        pre_resolver_negatives: tuple[NegativeOutcome, ...],
    ) -> tuple[NegativeOutcome, ...]:
        """surface pre-resolver negatives in `ExecutionTrace.events`
        when the run resolved to zero instances.

        the events tuple shape is intentionally minimal: a tuple of
        `NegativeOutcome`s, in field order, so diagnostic consumers
        can read the failure cause without parsing free-form prose.
        the wider `ExecutionTrace` semantics (OTEL spans, span events,
        producer_version attributes) land with the seam-K thread.
        """

        return pre_resolver_negatives


# ---------------------------------------------------------------------------
# canonical seam-F layer 3 escalation (executor-owned)
# ---------------------------------------------------------------------------


def _escalate_layer3_failure(
    instance: Instance,
    failure: ValidationFailure,
) -> Instance:
    """map a layer-3 `ValidationFailure(layer="instance", ...)` to a
    typed `NegativeOutcome` and rebuild the affected instance immutably.

    fixed mapping (load-bearing per the brief):

    - `category="validation"`
    - `code="instance_failure"`
    - `field_id=None` (layer-3 failure is per-instance cross-field;
      the typed validator surface uses the literal sentinel
      `"<instance>"` in `ValidationFailure.field_id`, but the
      escalated `NegativeOutcome.field_id` is canonically `None` —
      no individual field is implicated)
    - `instance_key=<failure.instance_key>` (same as the resolved
      instance — layer-3 failure never reassigns or re-buckets)
    - `reason=<failure.reason>`
    - `candidate_count=None`

    the rebuilt instance preserves `instance_id`, `evidence`,
    and `grouping_evidence`. `negative_outcomes` is appended with the
    escalated negative. outcome flips `complete -> partial`; `partial`
    stays `partial` (already-degraded instances do not regress
    further).
    """

    escalated = NegativeOutcome(
        category="validation",
        code="instance_failure",
        field_id=None,
        instance_key=failure.instance_key,
        reason=failure.reason,
        candidate_count=None,
        object_issues=failure.object_issues,
    )
    next_negatives = (*instance.negative_outcomes, escalated)
    return Instance(
        instance_id=instance.instance_id,
        instance_key=instance.instance_key,
        outcome="partial",
        evidence=instance.evidence,
        negative_outcomes=next_negatives,
        grouping_evidence=instance.grouping_evidence,
    )


def _object_repair_feedback(
    instances: tuple[Instance, ...],
) -> dict[str, tuple[str, ...]]:
    feedback: dict[str, list[str]] = {}
    for instance in instances:
        for negative in instance.negative_outcomes:
            if negative.category != "validation" or negative.code != "instance_failure":
                continue
            for issue in negative.object_issues:
                if issue.severity != "error":
                    continue
                for ref in issue.implicates:
                    feedback.setdefault(ref.field_id, []).append(issue.reason)
    return {field_id: tuple(reasons) for field_id, reasons in feedback.items()}


def _field_repair_feedback(
    negatives: tuple[NegativeOutcome, ...],
) -> dict[str, tuple[str, ...]]:
    feedback: dict[str, list[str]] = {}
    for negative in negatives:
        if (
            negative.category == "validation"
            and negative.code == "field_failure"
            and negative.field_id is not None
        ):
            feedback.setdefault(negative.field_id, []).append(negative.reason)
    return {field_id: tuple(reasons) for field_id, reasons in feedback.items()}


def _object_failure_count(instances: tuple[Instance, ...]) -> int:
    total = 0
    for instance in instances:
        for negative in instance.negative_outcomes:
            if negative.category == "validation" and negative.code == "instance_failure":
                total += 1
    return total


def _field_failure_count(negatives: tuple[NegativeOutcome, ...]) -> int:
    return sum(
        1
        for negative in negatives
        if negative.category == "validation" and negative.code == "field_failure"
    )


def _replace_validated_fields(
    *,
    spec: ExtractionSpec,
    original: tuple[ValidatedField, ...],
    repaired: tuple[ValidatedField, ...],
    replaced_field_ids: tuple[str, ...],
) -> tuple[ValidatedField, ...]:
    replaced = set(replaced_field_ids)
    original_by_field: dict[str, list[ValidatedField]] = {}
    repaired_by_field: dict[str, list[ValidatedField]] = {}
    for field in original:
        if field.proposed.field_id not in replaced:
            original_by_field.setdefault(field.proposed.field_id, []).append(field)
    for field in repaired:
        repaired_by_field.setdefault(field.proposed.field_id, []).append(field)

    ordered: list[ValidatedField] = []
    for field_spec in spec.fields:
        source = repaired_by_field if field_spec.field_id in replaced else original_by_field
        ordered.extend(source.get(field_spec.field_id, ()))
    return tuple(ordered)
