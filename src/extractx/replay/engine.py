"""`replay_re_execute` — source-driven replay engine per M9 phase 2.

per docs/tasks/m9-phase-2-replay-re-execution.md and docs/architecture.md
§7 seam H ("replay mode determinism: given pinned selector, planner,
and resolver `producer_version`s, replay reconstructs `Extraction`
bytewise"). this module operationalizes that promise on the supported
algorithmic path.

execution flow (load-bearing per M9 phase-2 §3):

1. read source bytes from the store
2. read the persisted `SpecSummary`
3. look up the live `schema_cls` from the in-process registry
4. rehydrate the `ExtractionSpec` via `rehydrate_spec`
5. assert producer-version pinning — captured == live, else raise
6. rebuild `ExecutorPolicy` from the persisted `PolicySummary`
7. construct a fresh `Runtime()` and a fresh non-persisting
   `SerialExecutor()` (no `storage` parameter; replay does not
   write to the store)
8. run the real `SerialExecutor.execute(...)` pipeline
9. return the reproduced `Extraction` (its `replay_artifact_ref`
   is `""` because the executor is non-persisting)

failure surface (typed `InfrastructureError` with pinned prefixes):

- `"replay.producer_version_drift: ..."` — captured `producer_versions`
  diverges from live class-level values (hard failure, not a warning;
  no soft replay mode in phase 2)
- `"spec_rehydrate.missing_class: ..."` — pydantic-backed spec with no
  live class registered in this process
- `"spec_rehydrate.version_mismatch: ..."` — `from_pydantic`'s hash
  composition diverged at replay time
- `"spec_rehydrate.field_drift: ..."` — `from_pydantic`'s field
  extraction diverged at replay time
- `"spec_rehydrate.manual_unsupported: ..."` — manual specs are not
  supported in phase 2 (deferred to a follow-on thread)

drift acknowledgements:

- equality treatment: `replay_artifact_ref` is excluded (captured
  carries the real id; reproduced carries `""`). seam-K phase 1 makes
  `trace.events` a typed deterministic tuple of `NegativeOutcome`s, so
  event content participates in replay equality.
- `Budget` / `Reporter` identity is not part of replay equality —
  replay constructs a fresh `Runtime()` whose `budget` and `reporter`
  are fresh defaults. on the algorithmic slice this is a non-issue
  (no `UsageEvent`s emitted, no reporter events threaded)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import BaseModel

from extractx.candidates.generators import regex as _regex_module
from extractx.core.exceptions import InfrastructureError
from extractx.core.outcomes import Extraction
from extractx.instances.resolvers import deterministic as _deterministic_module
from extractx.proposals import validation as _validation_module
from extractx.selection.algorithmic import singleton as _singleton_module

from .reader import read_spec_summary

if TYPE_CHECKING:
    from extractx.storage.protocol import ExtractxStore

    from .artifact import ReplayArtifact
    from .vocabulary import Extraction

__all__ = [
    "assert_replay_extraction_equal",
    "assert_replay_result_equal",
    "replay_re_execute",
]


# canonical key set for live producer-version values. these mirror the
# `producer_versions` map written by `SerialExecutor`: `"candidate_strategy"`,
# `"selector"`, `"resolver"` (M9 phase-1) plus `"validator"` (replay
# drift-gate phase 1, covering `LayeredProposalValidator`).
#
# load-bearing legacy-compat invariant: live keys not present in the
# *captured* map are NOT drift. legacy artifacts written before a
# given drift-gate widening (e.g. M9 phase-1 / phase-2 artifacts that
# carry only the three seam-C/D/G.resolver keys, no `"validator"`)
# replay through this gate without raising. this is the contract that
# makes drift-gate widening forward-compatible across artifact
# generations — see `check_producer_version_drift` below for the
# iteration shape that enforces it.
def _live_producer_versions() -> Mapping[str, str]:
    # call through the module attribute (rather than a binding alias)
    # so monkey-patches against the seam-class module's
    # `algorithmic_code_hash` symbol are visible at replay time. tests
    # rely on this for the drift-surface proof.
    return {
        "candidate_strategy": _regex_module.algorithmic_code_hash(),
        "selector": _singleton_module.algorithmic_code_hash(),
        "resolver": _deterministic_module.algorithmic_code_hash(),
        "validator": _validation_module.algorithmic_code_hash(),
    }


def check_producer_version_drift(captured: Mapping[str, str]) -> None:
    """raise `InfrastructureError` on any captured-vs-live divergence.

    drift is binary: either every captured key matches its live value
    or the function raises `InfrastructureError("replay.producer_version_drift:
    ...")` listing every diverging entry. there is no "best-effort
    replay" mode and no `NegativeOutcome` rollup — the architecture's
    replay-under-pinning promise is binary.

    load-bearing legacy-compat invariant: iteration is over
    `captured.items()`, NOT `live.items()`. live keys not present in
    the captured map are NOT drift — the captured map is the canonical
    key set for the run. this is what allows legacy artifacts (from
    pre-widening generations of the gate, e.g. pre-`"validator"` M9
    phase-1 / phase-2 artifacts) to replay through unchanged when the
    live key set widens. inverting iteration here would silently
    break legacy replay; do not change the iteration shape without a
    coordinated artifact-version bump.
    """

    live = _live_producer_versions()
    diverging: list[str] = []
    # captured-keyed iteration is load-bearing for legacy compat — see
    # docstring above. live keys with no captured counterpart are
    # silently skipped (phase-1 silent-skip; no Reporter / log emission
    # for legacy artifacts — that surface is parked behind seam K).
    for key, captured_value in captured.items():
        live_value = live.get(key)
        if live_value is None:
            diverging.append(
                f"{key}: captured={captured_value!r} live=<missing>",
            )
            continue
        if live_value != captured_value:
            diverging.append(
                f"{key}: captured={captured_value!r} live={live_value!r}",
            )
    if diverging:
        raise InfrastructureError(
            "replay.producer_version_drift: " + "; ".join(diverging),
        )


async def replay_re_execute(
    artifact: ReplayArtifact,
    store: ExtractxStore,
) -> Extraction:
    """re-execute the captured run from `(artifact, store)` and return
    the reproduced `Extraction`.

    pre-run gates (raise `InfrastructureError` before any seam runs):

    - missing live `schema_cls` for `artifact.spec_version`
    - rehydrated `spec.version` mismatch with `summary.spec_version`
    - rehydrated `spec.fields` field-id drift vs `summary.field_summaries`
    - manual spec (`summary.source_schema_ref is None`)
    - producer-version drift (`captured != live`)

    after the gates accept, the engine constructs a fresh `Runtime()`
    and a fresh non-persisting `SerialExecutor()` (no `storage`
    parameter) and delegates to `SerialExecutor.execute(...)`. replay
    does **not** persist a second artifact — `objects/replay/` and
    `runs/` are unchanged after replay completes.
    """

    # local imports break the import cycle:
    # `extractx.execution.executor.serial` imports `extractx.replay`
    # (for `ReplayArtifact` / `ReplayArtifactWriter`); importing the
    # executor at module-import time here would close that cycle. the
    # call-site import is fine — the engine is only invoked after the
    # full package is imported.
    from extractx.execution.executor.serial import SerialExecutor
    from extractx.execution.policy import ExecutorPolicy
    from extractx.execution.runtime import Runtime
    from extractx.schema._schema_cls_registry import lookup_schema_cls
    from extractx.schema.rehydrate import rehydrate_spec

    # 1. read source bytes (content-addressed under the artifact's
    #    captured `source_ref.content_hash`).
    source_bytes = store.get_object("source", artifact.source_ref.content_hash)

    # 2. read the persisted `SpecSummary` (keyed by spec_version).
    summary = read_spec_summary(store, artifact.spec_version)

    # 3. resolve the live schema class from the in-process registry.
    #    pydantic-backed specs require a live class; absence is a
    #    typed `InfrastructureError("spec_rehydrate.missing_class:
    #    ...")`. manual specs surface from inside `rehydrate_spec`
    #    with `"spec_rehydrate.manual_unsupported: ..."`.
    schema_cls_any = lookup_schema_cls(artifact.spec_version)
    if schema_cls_any is None and summary.source_schema_ref is not None:
        raise InfrastructureError(
            "spec_rehydrate.missing_class: spec_version="
            f"{artifact.spec_version!r} has no live schema class "
            "registered in this process; build the spec via "
            "ExtractionSpec.from_pydantic(...) before calling "
            "replay_re_execute",
        )

    # 4. rehydrate the `ExtractionSpec`. for manual specs this raises
    #    `spec_rehydrate.manual_unsupported`; for pydantic-backed
    #    specs it asserts version- and field-shape stability.
    if summary.source_schema_ref is None:
        # manual spec — let `rehydrate_spec` surface the typed
        # rejection. we don't try to look up a class.
        spec = rehydrate_spec(summary, schema_cls=_DummyManualPlaceholder)
    else:
        # narrow `Any` from the registry back to `type[BaseModel]`.
        # `from_pydantic` only registers `BaseModel` subclasses, so
        # the runtime check is defense in depth.
        if not (isinstance(schema_cls_any, type) and issubclass(schema_cls_any, BaseModel)):
            raise InfrastructureError(
                "spec_rehydrate.missing_class: registered schema "
                f"class for spec_version={artifact.spec_version!r} "
                f"is not a BaseModel subclass: {schema_cls_any!r}",
            )
        spec = rehydrate_spec(summary, schema_cls=schema_cls_any)

    # 5. producer-version drift check — fires before constructing
    #    the executor so the run does not begin under a pinning
    #    violation.
    check_producer_version_drift(artifact.producer_versions)

    # 6. rebuild the executor policy from the persisted summary.
    policy = ExecutorPolicy.from_summary(artifact.policy_summary)

    # 7. fresh `Runtime()` and non-persisting `SerialExecutor()`.
    #    the executor is constructed *without* `storage` so replay
    #    writes nothing to the store and the reproduced result
    #    carries `replay_artifact_ref=""`.
    runtime = Runtime()
    executor = SerialExecutor()

    # 8. delegate to the real executor pipeline. no replay-specific
    #    code path inside `SerialExecutor` or `IndependentStrategy` —
    #    the engine is purely a caller composition (M9 phase-2 hard
    #    pin #7; architecture §15 anti-pattern `Benchmark-Only
    #    Execution Path`).
    result = await executor.execute(
        document=source_bytes,
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    if not isinstance(result, Extraction):
        raise InfrastructureError("replay.deferred_unsupported: replay expected Extraction")
    return result


def assert_replay_extraction_equal(
    captured: Extraction,
    reproduced: Extraction,
) -> None:
    """assert replay `Extraction` equality per the M9 phase-2 brief.

    required equality fields (load-bearing):

    - `instances` (canonical authority — every `Instance` and nested
      `Evidence` byte-equal)
    - `outcome`
    - `document_id`
    - `spec_version`
    - `strategy`
    additional required trace equality:

    - `trace.trace_id`
    - `trace.events`

    excluded from required equality:

    - `replay_artifact_ref` — captured carries the real id; reproduced
      carries `""` (replay engine builds a non-persisting executor)

    raises `AssertionError` on any required-field divergence.

    note: `ReplayArtifact.final_instances` and `Extraction.instances`
    carry the same `Instance` objects. Seam-D `Observation`s live on
    the artifact and are the write-side replay record for selector
    decisions; reproduced `Extraction` equality is proven through the
    resulting `Instance` / `Evidence` structure.
    """

    assert captured.instances == reproduced.instances, (
        "replay equality: Extraction.instances differ"
    )
    assert captured.outcome == reproduced.outcome, "replay equality: outcome differs"
    assert captured.document_id == reproduced.document_id, "replay equality: document_id differs"
    assert captured.spec_version == reproduced.spec_version, "replay equality: spec_version differs"
    assert captured.strategy == reproduced.strategy, "replay equality: strategy differs"
    assert captured.trace.trace_id == reproduced.trace.trace_id, (
        "replay equality: trace.trace_id differs"
    )
    assert captured.trace.events == reproduced.trace.events, "replay equality: trace.events differ"


def assert_replay_result_equal(
    captured: Extraction,
    reproduced: Extraction,
) -> None:
    """Backward-compatible name for `assert_replay_extraction_equal`."""

    assert_replay_extraction_equal(captured, reproduced)


class _DummyManualPlaceholder(BaseModel):
    """unreachable placeholder used only to satisfy `rehydrate_spec`'s
    type signature on the manual-spec rejection path.

    the rejection fires before `schema_cls` is touched. defining a
    real concrete `BaseModel` subclass keeps pyright happy without
    inventing a public registration api.
    """
