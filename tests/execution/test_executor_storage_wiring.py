"""executor wiring proof for the M9 phase-1 storage seam.

per docs/tasks/m9-phase-1-replay-storage-skeleton.md §7 / §9.

asserts:

- `SerialExecutor()` with no storage → M8 parity
  (`replay_artifact_ref == ""`, no filesystem writes)
- `SerialExecutor(storage=...)` → populated `replay_artifact_ref`,
  matches `compute_artifact_id_from_bytes(replay_blob)`
- failed runs persist artifact + manifest first-class
- `Runtime` is unchanged (no `storage` field) — seam J does not widen
- proof tests reach the executor via real `run_extraction(...)` /
  `SerialExecutor.execute(...)`, not a benchmark-only path
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel

from extractx import (
    ExecutorPolicy,
    ExtractionSpec,
    Runtime,
    ValueKind,
    extract_field,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import StrategyBinding
from extractx.execution.executor.serial import SerialExecutor
from extractx.execution.runtime import Runtime as _Runtime
from extractx.replay import compute_artifact_id_from_bytes
from extractx.storage import LocalFilesystemStore


class _Phone(BaseModel):
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


@pytest.fixture
def spec() -> ExtractionSpec:
    return ExtractionSpec.from_pydantic(_Phone)


@pytest.fixture
def runtime() -> Runtime:
    return Runtime()


@pytest.fixture
def policy() -> ExecutorPolicy:
    return ExecutorPolicy(strategy="independent")


# --------------------------------------------------------------------------
# M8 parity — no storage means no persistence
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_storage_preserves_m8_replay_ref_empty(
    spec,
    runtime,
    policy,
) -> None:
    """`SerialExecutor()` without storage yields
    `replay_artifact_ref == ""` (M8 byte-parity)."""

    result = await SerialExecutor().execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert result.replay_artifact_ref == ""


@pytest.mark.asyncio
async def test_run_extraction_does_not_persist_by_default(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    """`run_extraction(...)` constructs a `SerialExecutor()` without
    storage (M9 phase-1 hard pin #11). callers reach the persisted
    path only by constructing their own executor."""

    result = await run_extraction(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert result.replay_artifact_ref == ""
    # tmp_path remains empty — `run_extraction` cannot have written.
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# storage on the executor — populated replay_artifact_ref
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_populates_replay_artifact_ref(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    store = LocalFilesystemStore(tmp_path)
    executor = SerialExecutor(storage=store)
    result = await executor.execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert result.replay_artifact_ref != ""

    blob = store.get_object("replay", result.replay_artifact_ref)
    assert compute_artifact_id_from_bytes(blob) == result.replay_artifact_ref


@pytest.mark.asyncio
async def test_storage_persists_source_blob(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    """source bytes round-trip byte-equal under
    `objects/source/<source_hash>.bin`."""

    store = LocalFilesystemStore(tmp_path)
    executor = SerialExecutor(storage=store)
    raw = b"555-1234 hello"
    result = await executor.execute(
        document=raw,
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    # find the source content_hash via the artifact.
    from extractx.replay import read_replay

    artifact = read_replay(store, result.replay_artifact_ref)
    persisted = store.get_object("source", artifact.source_ref.content_hash)
    assert persisted == raw


@pytest.mark.asyncio
async def test_storage_persists_spec_summary_blob(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    """`objects/spec/<spec_version>.json` round-trips to a structurally
    equal `SpecSummary` (NOT `ExtractionSpec` per drift §3)."""

    from extractx.replay import read_spec_summary
    from extractx.schema import summarize_spec

    store = LocalFilesystemStore(tmp_path)
    executor = SerialExecutor(storage=store)
    await executor.execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    persisted = read_spec_summary(store, spec.version)
    expected = summarize_spec(spec)
    assert persisted == expected


# --------------------------------------------------------------------------
# failed-run persistence
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_run_persists_artifact_and_manifest(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    """failed runs (`outcome="failed"`, `instances=()`) persist
    artifact + manifest first-class (M9 phase-1 hard pin #12)."""

    store = LocalFilesystemStore(tmp_path)
    executor = SerialExecutor(storage=store)
    result = await executor.execute(
        document="no digits here",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert result.outcome == "failed"
    assert result.replay_artifact_ref != ""

    # replay blob exists.
    blob = store.get_object("replay", result.replay_artifact_ref)
    assert blob != b""

    # manifest exists.
    runs = store.list_run_ids()
    assert len(runs) == 1


# --------------------------------------------------------------------------
# Runtime is not widened with storage — storage stays executor-owned
# --------------------------------------------------------------------------


def test_runtime_has_no_storage_field() -> None:
    """white-box: `Runtime` has no `storage` field. seam J does not
    widen in this thread (M9 phase-1 hard pin #4)."""

    fields = {f.name for f in _Runtime.__dataclass_fields__.values()}
    assert "storage" not in fields, fields


def test_runtime_dataclass_fields_unchanged_from_m8() -> None:
    """`Runtime` carries capabilities only; storage stays executor-owned."""

    fields = {f.name for f in _Runtime.__dataclass_fields__.values()}
    assert fields == {
        "llm",
        "nlp",
        "fetch",
        "prompt_recorder",
        "deferred_provider",
        "selector_prompt_assets",
        "selector_prompt_policies",
        "budget",
        "reporter",
    }


# --------------------------------------------------------------------------
# authority boundaries — no `result/`, no `interview/`, no `views/`
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authority_boundaries_hold(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    """phase-1 layout omits `objects/result/`, `objects/interview/`,
    and `views/` (M9 phase-1 brief §9 authority-boundary clauses)."""

    store = LocalFilesystemStore(tmp_path)
    executor = SerialExecutor(storage=store)
    await executor.execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert not (tmp_path / "objects" / "result").exists()
    assert not (tmp_path / "objects" / "interview").exists()
    assert not (tmp_path / "views").exists()
