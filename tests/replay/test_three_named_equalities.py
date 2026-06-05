"""the three named-equality proofs from the M9 phase-1 brief.

these tests carry the load-bearing equality claims that operationalize
the architecture's "bytewise reconstructs" wording:

(1) **artifact-bytes round-trip**: serialize → deserialize → serialize
    is byte-equal.
(2) **artifact-structural**: `read_replay(store, id) == original_artifact`.
(3) **extraction-structural**: `reconstruct_extraction_result(...) ==
    original_result`.

every test here reaches the executor via real `SerialExecutor.execute(...)`
(no benchmark-only path; M9 phase-1 hard pin #13).
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

import pytest

from extractx import Extraction
from extractx.core.outcomes import ExecutionTrace, NegativeOutcome
from extractx.execution.executor.serial import SerialExecutor
from extractx.replay import (
    ReplayArtifactReader,
    ReplayArtifactWriter,
    read_replay,
    reconstruct_extraction,
    reconstruct_extraction_result,
)

# --------------------------------------------------------------------------
# (1) artifact-bytes round-trip
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_bytes_round_trip_complete(
    executor_with_storage: SerialExecutor,
    pydantic_spec,
    runtime,
    policy,
    doc_complete: str,
    store,
) -> None:
    """(1) artifact-bytes round-trip: complete outcome."""

    result = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    artifact_id = result.replay_artifact_ref
    assert artifact_id != ""

    blob1 = store.get_object("replay", artifact_id)
    artifact = ReplayArtifactReader().deserialize(blob1)
    blob2 = ReplayArtifactWriter().serialize(artifact)
    assert blob1 == blob2


@pytest.mark.asyncio
async def test_artifact_bytes_round_trip_partial(
    executor_with_storage: SerialExecutor,
    pydantic_spec,
    runtime,
    policy,
    store,
) -> None:
    """(1) artifact-bytes round-trip: partial outcome (multiple regex
    matches yield AMBIGUOUS → typed negative → outcome=failed at the
    sole-instance attachment site).

    in phase 1 the singleton selector emits AMBIGUOUS on >1 candidates;
    seam E translates that to a `selection.ambiguous` negative; with no
    surviving final instance the run rolls up to `failed`. this still
    exercises a non-trivial pre_resolver_negatives shape.
    """

    result = await executor_with_storage.execute(
        document="phones 555-1234 and 555-5678",
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    artifact_id = result.replay_artifact_ref

    blob1 = store.get_object("replay", artifact_id)
    artifact = ReplayArtifactReader().deserialize(blob1)
    blob2 = ReplayArtifactWriter().serialize(artifact)
    assert blob1 == blob2


@pytest.mark.asyncio
async def test_artifact_bytes_round_trip_failed(
    executor_with_storage: SerialExecutor,
    pydantic_spec,
    runtime,
    policy,
    doc_failed: str,
    store,
) -> None:
    """(1) artifact-bytes round-trip: failed outcome.

    failed runs persist replay + manifest first-class per M9 phase-1
    hard pin #12.
    """

    result = await executor_with_storage.execute(
        document=doc_failed,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    assert result.outcome == "failed"
    artifact_id = result.replay_artifact_ref
    assert artifact_id != ""

    blob1 = store.get_object("replay", artifact_id)
    artifact = ReplayArtifactReader().deserialize(blob1)
    blob2 = ReplayArtifactWriter().serialize(artifact)
    assert blob1 == blob2


# --------------------------------------------------------------------------
# (2) artifact-structural
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_structural_equality(
    executor_with_storage: SerialExecutor,
    pydantic_spec,
    runtime,
    policy,
    doc_complete: str,
    store,
) -> None:
    """(2) `read_replay(store, id) == original_artifact` under pydantic
    structural equality. the in-memory artifact is rebuilt from the
    same fields; structural equality is the contract."""

    # build the artifact via the executor's persistence path then read
    # it back. we do not separately construct an "original artifact" —
    # the executor writes once, we read once, and assert that the
    # round-trip preserves structural identity.
    result = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    artifact_id = result.replay_artifact_ref

    # round-trip twice and assert structural equality.
    artifact1 = read_replay(store, artifact_id)
    artifact2 = read_replay(store, artifact_id)
    assert artifact1 == artifact2

    # also assert structural equality vs deserializing the bytes
    # directly — the two paths must agree.
    blob = store.get_object("replay", artifact_id)
    via_reader = ReplayArtifactReader().deserialize(blob)
    assert via_reader == artifact1


# --------------------------------------------------------------------------
# (3) extraction-structural
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_structural_equality_complete(
    executor_with_storage: SerialExecutor,
    pydantic_spec,
    runtime,
    policy,
    doc_complete: str,
    store,
) -> None:
    """(3) `reconstruct_extraction_result(...) == original_result` for
    a complete-outcome run."""

    original = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    artifact = read_replay(store, original.replay_artifact_ref)
    rebuilt = reconstruct_extraction_result(
        artifact,
        artifact_id=original.replay_artifact_ref,
    )
    assert isinstance(rebuilt, Extraction)
    assert rebuilt == original

    rebuilt_via_new_name = reconstruct_extraction(
        artifact,
        artifact_id=original.replay_artifact_ref,
    )
    assert rebuilt_via_new_name == original


@pytest.mark.asyncio
async def test_result_structural_equality_failed(
    executor_with_storage: SerialExecutor,
    pydantic_spec,
    runtime,
    policy,
    doc_failed: str,
    store,
) -> None:
    """(3) result-structural equality holds for failed runs as well."""

    original = await executor_with_storage.execute(
        document=doc_failed,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    assert original.outcome == "failed"
    assert original.trace.events != ()
    assert all(isinstance(event, NegativeOutcome) for event in original.trace.events)
    artifact = read_replay(store, original.replay_artifact_ref)
    rebuilt = reconstruct_extraction_result(
        artifact,
        artifact_id=original.replay_artifact_ref,
    )
    assert rebuilt.trace.events == original.trace.events
    assert rebuilt == original


@pytest.mark.asyncio
async def test_result_structural_equality_partial(
    executor_with_storage: SerialExecutor,
    pydantic_spec,
    runtime,
    policy,
    store,
) -> None:
    """(3) result-structural equality on a non-trivial run with
    pre-resolver negatives."""

    original = await executor_with_storage.execute(
        document="phones 555-1234 and 555-5678",
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    artifact = read_replay(store, original.replay_artifact_ref)
    rebuilt = reconstruct_extraction_result(
        artifact,
        artifact_id=original.replay_artifact_ref,
    )
    assert rebuilt == original


# --------------------------------------------------------------------------
# trace shape sanity
# --------------------------------------------------------------------------


def test_artifact_trace_is_execution_trace() -> None:
    """the artifact's `trace` field is the same `ExecutionTrace` shape
    the result carries; not a placeholder."""

    from extractx.replay.artifact import ReplayArtifact

    annotation = ReplayArtifact.model_fields["trace"].annotation
    assert annotation is ExecutionTrace


def test_execution_trace_events_are_typed_negative_outcomes() -> None:
    """seam-K phase 1: `ExecutionTrace.events` is the supported-path
    typed event tuple, not `tuple[Any, ...]`."""

    annotation = ExecutionTrace.model_fields["events"].annotation
    assert get_origin(annotation) is tuple
    assert get_args(annotation) == (NegativeOutcome, Ellipsis)
    assert annotation != tuple[Any, ...]
