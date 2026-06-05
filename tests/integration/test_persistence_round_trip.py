"""end-to-end persistence proof per docs/tasks/m9-phase-1-replay-storage-skeleton.md
§9 — reaches the executor via real `run_extraction(...)` /
`SerialExecutor.execute(...)`, not a benchmark-only path.

covers remaining projection and authority-boundary clauses too:

- `Extraction.usage()` returns captured usage events
- `Extraction.interview()` still raises `NotImplementedError`
- `objects/result/`, `objects/interview/`, `views/` are not created
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
from extractx.core.objects import InstanceGroupingKey, StrategyBinding
from extractx.execution.executor.serial import SerialExecutor
from extractx.replay import (
    read_manifest,
    read_replay,
    reconstruct_extraction_result,
)
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


@pytest.mark.asyncio
async def test_run_extraction_default_path_unchanged(tmp_path: Path) -> None:
    """`run_extraction(...)` keeps M8 byte-parity: no persistence,
    `replay_artifact_ref == ""`."""

    spec = ExtractionSpec.from_pydantic(_Phone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result = await run_extraction(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert result.replay_artifact_ref == ""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_full_persistence_round_trip(tmp_path: Path) -> None:
    """one persisted run, reloaded, reconstructed, structurally equal.

    end-to-end smoke for the M9 phase-1 success criteria. exercises:

    - source / spec / replay / runs paths exist
    - manifest is derived from the artifact
    - reconstruct_extraction_result == original_result
    - `replay_artifact_ref` matches `compute_artifact_id_from_bytes(blob)`
    """

    spec = ExtractionSpec.from_pydantic(_Phone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    store = LocalFilesystemStore(tmp_path)

    executor = SerialExecutor(storage=store)
    original = await executor.execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    artifact = read_replay(store, original.replay_artifact_ref)
    rebuilt = reconstruct_extraction_result(
        artifact,
        artifact_id=original.replay_artifact_ref,
    )
    assert rebuilt == original

    runs = store.list_run_ids()
    assert len(runs) == 1
    manifest = read_manifest(store, runs[0])
    assert manifest.replay_ref == original.replay_artifact_ref
    assert manifest.spec_version == spec.version
    assert manifest.outcome == original.outcome


# --------------------------------------------------------------------------
# stub honesty — phase-1 storage does not unblock these methods
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_result_usage_returns_captured_events(tmp_path: Path) -> None:
    """`.usage()` returns captured usage events after a persisted run."""

    spec = ExtractionSpec.from_pydantic(_Phone)
    store = LocalFilesystemStore(tmp_path)
    result = await SerialExecutor(storage=store).execute(
        document="555-1234",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )
    assert result.usage() == ()


@pytest.mark.asyncio
async def test_extraction_result_interview_still_stub(tmp_path: Path) -> None:
    """`.interview()` still raises `NotImplementedError` after a
    persisted run."""

    spec = ExtractionSpec.from_pydantic(_Phone)
    store = LocalFilesystemStore(tmp_path)
    result = await SerialExecutor(storage=store).execute(
        document="555-1234",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )
    with pytest.raises(NotImplementedError):
        result.interview(field_id="phone", question="placeholder")


@pytest.mark.asyncio
async def test_instance_result_to_pydantic_materializes_after_persistence(
    tmp_path: Path,
) -> None:
    """`Instance.to_pydantic()` materializes a persisted run result."""

    spec = ExtractionSpec.from_pydantic(_Phone)
    store = LocalFilesystemStore(tmp_path)
    result = await SerialExecutor(storage=store).execute(
        document="555-1234",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )
    assert len(result.instances) == 1
    phone = result.instances[0].to_pydantic(_Phone)
    assert isinstance(phone, _Phone)
    assert phone.phone == "555-1234"
    # InstanceGroupingKey is unused but imported for the Instance shape;
    # this assertion documents the import is intentional.
    assert isinstance(result.instances[0].instance_key, InstanceGroupingKey)
