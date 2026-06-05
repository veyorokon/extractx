"""focused proof for `replay_re_execute` per M9 phase-2 §6.

covers:

- happy path: complete / partial / failed outcomes — replay-result
  equality is satisfied (`replay_artifact_ref` excluded by the helper)
- failed-outcome replay equality includes non-empty typed trace events
- white-box: replay writes nothing to the store
- white-box: captured `replay_artifact_ref` is non-empty, reproduced
  is `""` before exclusion
- producer-version drift surface (`replay.producer_version_drift: ...`)
- missing-class surface (`spec_rehydrate.missing_class: ...`)
- manual-spec rejection (`spec_rehydrate.manual_unsupported: ...`)
- engine surface: async, two params, returns `Extraction`
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel

from extractx import (
    ExecutorPolicy,
    Extraction,
    ExtractionSpec,
    Runtime,
    ValueKind,
    extract_field,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import StrategyBinding
from extractx.execution.executor.serial import SerialExecutor
from extractx.replay import (
    ReplayArtifact,
    read_replay,
    replay_re_execute,
)
from extractx.replay.engine import (
    assert_replay_result_equal,
    check_producer_version_drift,
)
from extractx.storage import LocalFilesystemStore

# --------------------------------------------------------------------------
# multi-field schema for the partial-outcome fixture (one field succeeds,
# one emits a pre-resolver negative — yields outcome="partial" via the
# executor's sole-instance attachment rule)
# --------------------------------------------------------------------------


class _PhoneAndZip(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )
    zip_code: Annotated[str, ValueKind.PERSON] = extract_field(
        description="zip",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{5}"},
                kind="candidate",
            ),
        ),
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _list_dir(path: Path) -> list[str]:
    """recursively list relative paths under `path` for white-box checks."""

    if not path.exists():
        return []
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


# --------------------------------------------------------------------------
# happy paths — complete / partial / failed outcomes
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_re_execute_complete_outcome(
    executor_with_storage: SerialExecutor,
    pydantic_spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
    doc_complete: str,
    store: LocalFilesystemStore,
) -> None:
    """complete-outcome run → reproduced result equals captured under
    the load-bearing equality (with `replay_artifact_ref` excluded)."""

    captured = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    assert captured.outcome == "complete"
    assert captured.replay_artifact_ref != ""

    artifact = read_replay(store, captured.replay_artifact_ref)
    reproduced = await replay_re_execute(artifact, store)

    # captured carries the real id; reproduced carries `""` because
    # the engine builds a non-persisting executor — this is the
    # documented exclusion encoded in the equality helper.
    assert reproduced.replay_artifact_ref == ""

    assert_replay_result_equal(captured, reproduced)


@pytest.mark.asyncio
async def test_replay_re_execute_partial_outcome(
    tmp_path: Path,
    runtime: Runtime,
    policy: ExecutorPolicy,
) -> None:
    """partial-outcome run — multi-field spec where one field resolves
    cleanly and one emits a pre-resolver negative (singleton selector
    emits AMBIGUOUS on >1 candidates → seam E `selection.ambiguous`).

    the executor's sole-instance attachment rule attaches the negative
    to the lone resolved instance and flips its outcome to `partial`.
    """

    store = LocalFilesystemStore(tmp_path)
    spec = ExtractionSpec.from_pydantic(_PhoneAndZip)

    captured = await SerialExecutor(storage=store).execute(
        # one phone match (clean), two zip matches (ambiguous)
        document="phone 555-1234, zips 90210 and 12345",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert captured.outcome == "partial"

    artifact = read_replay(store, captured.replay_artifact_ref)
    reproduced = await replay_re_execute(artifact, store)

    assert reproduced.outcome == "partial"
    assert_replay_result_equal(captured, reproduced)


@pytest.mark.asyncio
async def test_replay_re_execute_failed_outcome(
    executor_with_storage: SerialExecutor,
    pydantic_spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
    doc_failed: str,
    store: LocalFilesystemStore,
) -> None:
    """failed-outcome run → reproduced result equals captured."""

    captured = await executor_with_storage.execute(
        document=doc_failed,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    assert captured.outcome == "failed"
    assert captured.instances == ()
    assert captured.trace.events != ()
    assert captured.replay_artifact_ref != ""

    artifact = read_replay(store, captured.replay_artifact_ref)
    reproduced = await replay_re_execute(artifact, store)

    assert reproduced.outcome == "failed"
    assert reproduced.instances == ()
    assert reproduced.trace.events == captured.trace.events
    assert_replay_result_equal(captured, reproduced)


# --------------------------------------------------------------------------
# white-box invariants
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_re_execute_does_not_persist_second_artifact(
    executor_with_storage: SerialExecutor,
    pydantic_spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
    doc_complete: str,
    store: LocalFilesystemStore,
    tmp_path: Path,
) -> None:
    """replay engine writes nothing to the store: `objects/replay/`
    and `runs/` directory listings are unchanged after replay."""

    captured = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )

    pre_files = _list_dir(tmp_path)
    pre_run_ids = store.list_run_ids()
    artifact_ids_before = sorted(p.name for p in (tmp_path / "objects" / "replay").glob("*"))

    artifact = read_replay(store, captured.replay_artifact_ref)
    await replay_re_execute(artifact, store)

    post_files = _list_dir(tmp_path)
    post_run_ids = store.list_run_ids()
    artifact_ids_after = sorted(p.name for p in (tmp_path / "objects" / "replay").glob("*"))

    assert post_files == pre_files, (
        f"replay engine must not write to the store; pre={pre_files!r} post={post_files!r}"
    )
    assert post_run_ids == pre_run_ids
    assert artifact_ids_after == artifact_ids_before


@pytest.mark.asyncio
async def test_replay_result_has_empty_replay_artifact_ref(
    executor_with_storage: SerialExecutor,
    pydantic_spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
    doc_complete: str,
    store: LocalFilesystemStore,
) -> None:
    """captured carries a non-empty id; reproduced carries `""` because
    the replay engine builds `SerialExecutor()` without storage."""

    captured = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    assert captured.replay_artifact_ref != ""

    artifact = read_replay(store, captured.replay_artifact_ref)
    reproduced = await replay_re_execute(artifact, store)

    assert reproduced.replay_artifact_ref == ""


# --------------------------------------------------------------------------
# typed failure surfaces
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_producer_version_drift_surfaces_typed_error(
    executor_with_storage: SerialExecutor,
    pydantic_spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
    doc_complete: str,
    store: LocalFilesystemStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """monkey-patch a seam class's `algorithmic_code_hash()` between
    capture and replay; assert the pinned `replay.producer_version_drift:`
    prefix surfaces and names the diverging key."""

    captured = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    artifact = read_replay(store, captured.replay_artifact_ref)

    # bump the selector's algorithmic_code_hash. the engine reads this
    # at replay time via the module-level helper.
    from extractx.selection.algorithmic import singleton as singleton_mod

    monkeypatch.setattr(
        singleton_mod,
        "algorithmic_code_hash",
        lambda: "deliberately-bumped",
    )

    with pytest.raises(InfrastructureError) as exc_info:
        await replay_re_execute(artifact, store)
    msg = str(exc_info.value)
    assert msg.startswith("replay.producer_version_drift: ")
    assert "selector" in msg


@pytest.mark.asyncio
async def test_missing_schema_class_surfaces_typed_error(
    executor_with_storage: SerialExecutor,
    pydantic_spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
    doc_complete: str,
    store: LocalFilesystemStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clear `_SCHEMA_CLS_BY_SPEC_VERSION` for the run's spec_version;
    replay surfaces `spec_rehydrate.missing_class: ...`."""

    captured = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    artifact = read_replay(store, captured.replay_artifact_ref)

    from extractx.schema import _schema_cls_registry as reg

    # monkeypatch.delitem restores the entry on teardown.
    monkeypatch.delitem(reg._SCHEMA_CLS_BY_SPEC_VERSION, captured.spec_version)

    with pytest.raises(InfrastructureError) as exc_info:
        await replay_re_execute(artifact, store)
    assert str(exc_info.value).startswith("spec_rehydrate.missing_class: ")


@pytest.mark.asyncio
async def test_manual_spec_replay_rejected(
    executor_with_storage: SerialExecutor,
    manual_spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
    doc_complete: str,
    store: LocalFilesystemStore,
) -> None:
    """build a manual spec, persist it through a real run, then replay;
    assert the pinned `spec_rehydrate.manual_unsupported: ...` prefix
    fires."""

    captured = await executor_with_storage.execute(
        document=doc_complete,
        spec=manual_spec,
        runtime=runtime,
        policy=policy,
    )
    assert captured.spec_version == manual_spec.version
    artifact = read_replay(store, captured.replay_artifact_ref)

    with pytest.raises(InfrastructureError) as exc_info:
        await replay_re_execute(artifact, store)
    assert str(exc_info.value).startswith("spec_rehydrate.manual_unsupported: ")


# --------------------------------------------------------------------------
# engine surface (proof target #10)
# --------------------------------------------------------------------------


def test_replay_re_execute_is_async_and_takes_two_positional_args() -> None:
    """`replay_re_execute(artifact, store) -> Extraction` is
    async; takes only two parameters; carries no policy / runtime /
    schema_cls knobs."""

    assert inspect.iscoroutinefunction(replay_re_execute)
    sig = inspect.signature(replay_re_execute)
    params = list(sig.parameters.values())
    assert len(params) == 2
    assert params[0].name == "artifact"
    assert params[0].annotation is ReplayArtifact or params[0].annotation == "ReplayArtifact"
    assert params[1].name == "store"
    # return annotation is `Extraction`, resolved or string under
    # `from __future__ import annotations`.
    assert sig.return_annotation in (Extraction, "Extraction")


def test_check_producer_version_drift_extra_live_keys_are_not_drift() -> None:
    """drift contract: live keys not present in `captured` are NOT
    drift (only captured keys participate). `_live_producer_versions`
    today is the same shape as captured; test the helper directly with
    a captured map that omits a key — should pass cleanly."""

    captured: dict[str, Any] = {
        "candidate_strategy": _real_live("candidate_strategy"),
        # selector and resolver omitted on purpose — live values may
        # exist for them; per contract that is not drift.
    }
    check_producer_version_drift(captured)


def _real_live(key: str) -> str:
    """resolve the current live algorithmic_code_hash for a producer
    key without depending on the engine's private alias."""

    if key == "candidate_strategy":
        from extractx.candidates.generators.regex import algorithmic_code_hash

        return algorithmic_code_hash()
    if key == "selector":
        from extractx.selection.algorithmic.singleton import algorithmic_code_hash

        return algorithmic_code_hash()
    if key == "resolver":
        from extractx.instances.resolvers.deterministic import algorithmic_code_hash

        return algorithmic_code_hash()
    if key == "validator":
        from extractx.proposals.validation import algorithmic_code_hash

        return algorithmic_code_hash()
    raise KeyError(key)


# --------------------------------------------------------------------------
# `ExecutorPolicy.from_summary` round-trip (proof target #8)
# --------------------------------------------------------------------------


def test_executor_policy_from_summary_round_trip() -> None:
    """`PolicySummary` → `ExecutorPolicy.from_summary` rebuilds a
    structurally equal policy; round-tripping back through the
    executor's `PolicySummary` constructor yields the original
    summary."""

    from extractx.execution.policy import PolicySummary

    original_summary = PolicySummary(
        strategy="independent",
        execution_mode="immediate",
        repair=True,
        on_validation_failure="fail",
        capture_interview_transcripts=False,
    )
    rebuilt_policy = ExecutorPolicy.from_summary(original_summary)
    assert rebuilt_policy == ExecutorPolicy(
        strategy="independent",
        execution_mode="immediate",
        repair=True,
        on_validation_failure="fail",
        capture_interview_transcripts=False,
    )

    # symmetric round-trip: rebuild a summary from the rebuilt policy
    # using the same shape `SerialExecutor` uses today.
    round_trip_summary = PolicySummary(
        strategy=rebuilt_policy.strategy,
        execution_mode=rebuilt_policy.execution_mode,
        repair=rebuilt_policy.repair,
        on_validation_failure=rebuilt_policy.on_validation_failure,
        capture_interview_transcripts=rebuilt_policy.capture_interview_transcripts,
    )
    assert round_trip_summary == original_summary
