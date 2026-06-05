"""public tier-1 entry points per docs/architecture.md §13.

this module hosts the user-facing surface of the engine:

- `run_extraction(...)` — explicit four-line engine api. accepts a
  document, an `ExtractionSpec`, a `Runtime`, and an `ExecutorPolicy`,
  and returns an `Extraction` for immediate execution or a
  `DeferredSubmission` for deferred execution. used by plugin authors, advanced
  callers, and tests that need full control over the engine inputs.
- `extract(...)` — schema-first sugar for the common case. accepts a
  document and a pydantic `BaseModel` subclass; compiles the internal
  `ExtractionSpec` / `ExecutorPolicy` / `SerialExecutor` construction
  away from the caller. callers may pass `runtime=` when the schema
  needs capabilities such as an LLM provider. opt-in persistence via `store`;
  honest opt-in interview-capture via `capture_interviews` (which
  surfaces the executor's pre-run-gate `InfrastructureError` until the
  capture thread lands).
- `extract_one(...)` — materializing helper for the single-object common
  case. it calls `extract(...)`, then returns the one pydantic object or
  raises `ExtractionFailed(result=...)`.

phase-1 (M8 vertical slice) wires the engine and result entry points to the runnable
`SerialExecutor` + `IndependentStrategy` path. `strategy="iterative"`
is available through `run_extraction(...)` as a bounded single-instance
object-repair path. unsupported execution shapes (grounded_proposal
binding, non-`str` / non-`bytes` document, multi-instance iterative
planning, etc.) raise `InfrastructureError` *before the run begins* per
the brief's section 2.

after the pre-run gate accepts inputs, nothing else is raised to the
caller. step failures, validation errors, and budget exhaustion become
typed `NegativeOutcome` or `ValidationFailure` (the latter escalated to
`NegativeOutcome` under `ExecutorPolicy.on_validation_failure="fail"`)
routed through the executor.

`extract(...)` and `run_extraction(...)` deliberately construct their
own `SerialExecutor` instances rather than chaining (calling
`run_extraction` from inside `extract` would require widening
`run_extraction`'s signature with `store=`, which is out of scope for
this thread). the divergence is bounded to one extra construction line
per function and preserves both signatures byte-identically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from .core.exceptions import ExtractionFailed, InfrastructureError
from .core.objects import ExtractionSpec
from .core.outcomes import Extraction
from .execution.deferred import (
    DeferredResults,
    DeferredSubmission,
    DeferredSubmissionManifest,
    RenderedDeferredSubmission,
)
from .execution.executor.serial import SerialExecutor
from .execution.policy import ExecutorPolicy
from .execution.runtime import Runtime

if TYPE_CHECKING:
    from .storage.protocol import ExtractxStore

__all__ = [
    "collect_deferred_submission",
    "extract",
    "extract_one",
    "render_deferred_submission",
    "run_extraction",
]


async def run_extraction(
    document: bytes | str,
    spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
) -> Extraction | DeferredSubmission:
    """run a single extraction. contract lives in docs/architecture.md §13.

    phase-1 supported document surface: `str` (UTF-8 encoded) and
    `bytes`. both are adapted via the landed `TextAdapter`. HTML,
    Markdown, PDF, image, and paginated-visual inputs are out of scope
    for this slice.

    `spec` may be either pydantic-backed (built via
    `ExtractionSpec.from_pydantic(...)`) or manual (constructed
    directly). pydantic-backed specs require the live class to have
    been registered in this process by `from_pydantic(...)`; the
    executor performs the schema_cls lookup and threads it into seam
    F. manual specs run on the manual `ValidationBinding` path.

    raises:

    - `InfrastructureError` — any unsupported execution shape (see
      `SerialExecutor` for the full list).

    returns:

    - `Extraction` when `policy.execution_mode == "immediate"`.
    - `DeferredSubmission` when `policy.execution_mode == "deferred"`.
    """

    return await SerialExecutor().execute(
        document=document,
        spec=spec,
        runtime=runtime,
        policy=policy,
    )


async def collect_deferred_submission(
    document: bytes | str,
    spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
    manifest: DeferredSubmissionManifest,
    results: DeferredResults,
) -> Extraction:
    """Resolve completed deferred soft-call results into an `Extraction`.

    This is the collect-phase sibling of `run_extraction(...)` for callers that
    opted into `ExecutorPolicy.execution_mode == "deferred"`.
    """

    return await SerialExecutor().collect_deferred_submission(
        document=document,
        spec=spec,
        runtime=runtime,
        policy=policy,
        manifest=manifest,
        results=results,
    )


def render_deferred_submission(
    document: bytes | str,
    spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
) -> RenderedDeferredSubmission:
    """Render one document's deferred requests without provider submission."""

    return SerialExecutor().render_deferred_submission(
        document=document,
        spec=spec,
        runtime=runtime,
        policy=policy,
    )


async def extract(
    document: str | bytes,
    schema: type[BaseModel],
    *,
    runtime: Runtime | None = None,
    store: ExtractxStore | None = None,
    capture_interviews: bool = False,
) -> Extraction:
    """schema-first sugar over the engine api. contract per
    docs/architecture.md §10 / §13.

    accepts a document and a pydantic `BaseModel` subclass and compiles
    the internal `ExtractionSpec` / `ExecutorPolicy` / `SerialExecutor`
    construction away from the caller. callers can provide `runtime=`
    for capability-bound schemas; otherwise `Runtime()` is used.
    phase-1 behavior:

    - `spec = ExtractionSpec.from_pydantic(schema)` — pydantic-backed
      only. non-pydantic inputs surface `SpecError` (or whatever
      `from_pydantic` raises today) as-is; `extract` does not catch or
      rewrite.
    - `runtime = runtime or Runtime()` — bare construction still
      supports deterministic-only schemas; LLM-bound schemas should
      pass a `Runtime(llm=...)` or fail loudly at the executor
      capability gate.
    - `policy = ExecutorPolicy(strategy="independent",
      capture_interview_transcripts=capture_interviews)` — phase-1
      hard-codes `strategy="independent"`; the only landed strategy.
      a future thread that lands `IterativeStrategy` will introduce a
      knob with real semantics.
    - `executor = SerialExecutor(storage=store)` — opt-in M9 phase-1
      persistence. when `store is None`, behavior is byte-identical to
      `run_extraction(...)` (no filesystem writes,
      `replay_artifact_ref == ""`). when `store` is bound, the
      executor persists source / spec / replay / manifest and returns
      a result with a populated `replay_artifact_ref`.

    `capture_interviews=True` propagates the executor's pre-run-gate
    `InfrastructureError` verbatim — the capture path is owned by a
    later thread, and the gate is honest phase-1 behavior. callers see
    the executor's pinned message.

    `store` accepts an `ExtractxStore` instance or `None`. there is no
    `str → LocalFilesystemStore` polymorphism; constructing the store
    with a path is the caller's responsibility.

    raises:

    - `SpecError` — `schema` is not a `BaseModel` subclass, or the
      pydantic class violates a §12 spec-load rule (cycles, missing
      `ValueKind`, …). propagated from `ExtractionSpec.from_pydantic`.
    - `InfrastructureError` — any unsupported execution shape (see
      `SerialExecutor` for the full list), including
      missing runtime capabilities for LLM-bound schemas and
      `capture_interviews=True` until the capture thread lands.

    note on construction parallelism with `run_extraction(...)`:
    `extract(...)` constructs `SerialExecutor(storage=store)` directly
    rather than calling `run_extraction(...)`, which constructs
    `SerialExecutor()` without a `storage` parameter. threading
    `store` through `run_extraction` would widen its signature, which
    is forbidden in this thread. the divergence is bounded to one
    extra construction line in each function.
    """

    spec = ExtractionSpec.from_pydantic(schema)
    runtime = runtime or Runtime()
    policy = ExecutorPolicy(
        strategy="independent",
        capture_interview_transcripts=capture_interviews,
    )
    executor = SerialExecutor(storage=store)
    result = await executor.execute(
        document=document,
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    if not isinstance(result, Extraction):
        raise InfrastructureError(
            "extract.deferred_unsupported: schema-first extract(...) does not "
            "support execution_mode='deferred'",
        )
    return result


async def extract_one(
    document: str | bytes,
    schema: type[BaseModel],
    *,
    runtime: Runtime | None = None,
    store: ExtractxStore | None = None,
    capture_interviews: bool = False,
) -> BaseModel:
    """materialize exactly one pydantic object through `extract(...)`.

    `SpecError`, `InfrastructureError`, and materialization `SpecError`
    propagate unchanged. `ExtractionFailed` is reserved for cases where
    the run returned an `Extraction`, but the stricter one-object
    promise cannot be met.
    """

    result = await extract(
        document,
        schema,
        runtime=runtime,
        store=store,
        capture_interviews=capture_interviews,
    )
    incomplete_instances = tuple(
        instance for instance in result.instances if instance.outcome != "complete"
    )
    if result.outcome != "complete" or incomplete_instances:
        raise ExtractionFailed(
            "extract_one.failed: extraction outcome was "
            f"{result.outcome}; incomplete instances={len(incomplete_instances)}",
            result=result,
        )

    items = result.to_pydantic(schema)
    if len(items) != 1:
        raise ExtractionFailed(
            "extract_one.failed: expected exactly one materialized instance; "
            f"got {len(items)}",
            result=result,
        )
    return cast("BaseModel", items[0])
