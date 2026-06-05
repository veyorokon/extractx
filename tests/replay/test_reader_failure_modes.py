"""reader failure-mode tests for `ReplayArtifactReader` and the
top-level reader helpers.

per docs/tasks/m9-phase-1-replay-storage-skeleton.md §6 / §8 / §9.

failure messages carry the documented prefixes:

- `"replay.unknown_schema_version: ..."`
- `"replay.malformed: ..."`
- `"replay.truncated: ..."`
- `"replay.incompatible_trace_payload: ..."`
"""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

import extractx.replay.reader as reader_module
from extractx import ExecutorPolicy, ExtractionSpec, Runtime
from extractx.core.exceptions import InfrastructureError
from extractx.execution.executor.serial import SerialExecutor
from extractx.replay import ReplayArtifactReader
from extractx.storage import LocalFilesystemStore


def test_reader_unknown_schema_version_raises_with_prefix() -> None:
    reader = ReplayArtifactReader()
    blob = msgspec.msgpack.encode({"schema_version": "v999"})
    with pytest.raises(InfrastructureError) as exc_info:
        reader.deserialize(blob)
    assert str(exc_info.value).startswith("replay.unknown_schema_version: ")


def test_reader_malformed_raises_with_prefix() -> None:
    reader = ReplayArtifactReader()
    # decoded payload is a list, not a mapping → malformed.
    blob = msgspec.msgpack.encode([1, 2, 3])
    with pytest.raises(InfrastructureError) as exc_info:
        reader.deserialize(blob)
    assert str(exc_info.value).startswith("replay.malformed: ")


def test_reader_truncated_msgpack_raises_with_prefix() -> None:
    reader = ReplayArtifactReader()
    # bytes that are not valid msgpack at all.
    blob = b"\xc1"
    with pytest.raises(InfrastructureError) as exc_info:
        reader.deserialize(blob)
    msg = str(exc_info.value)
    assert msg.startswith(("replay.malformed: ", "replay.truncated: "))


def test_reader_validation_failure_raises_malformed(tmp_path: Path) -> None:
    """msgpack decodes to a mapping with `schema_version: "v1"` but
    other fields are missing → pydantic validation fails."""

    reader = ReplayArtifactReader()
    blob = msgspec.msgpack.encode({"schema_version": "v1"})
    with pytest.raises(InfrastructureError) as exc_info:
        reader.deserialize(blob)
    assert str(exc_info.value).startswith("replay.malformed: ")


def test_reader_v2_without_observations_raises_malformed() -> None:
    reader = ReplayArtifactReader()
    blob = msgspec.msgpack.encode({"schema_version": "v2"})
    with pytest.raises(InfrastructureError) as exc_info:
        reader.deserialize(blob)
    message = str(exc_info.value)
    assert message.startswith("replay.malformed: ")
    assert "observations" in message


def test_reader_has_no_trace_rehydration_shim() -> None:
    """seam-K phase 1: replay reader does not carry `_rehydrate_trace`."""

    assert not hasattr(reader_module, "_rehydrate_trace")


@pytest.mark.asyncio
async def test_reader_incompatible_trace_payload_raises_typed_prefix(
    executor_with_storage: SerialExecutor,
    pydantic_spec: ExtractionSpec,
    runtime: Runtime,
    policy: ExecutorPolicy,
    doc_failed: str,
    store: LocalFilesystemStore,
) -> None:
    """legacy or malformed trace-event payloads fail at the reader
    boundary with the seam-K trace-specific prefix."""

    result = await executor_with_storage.execute(
        document=doc_failed,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    blob = store.get_object("replay", result.replay_artifact_ref)
    payload = msgspec.msgpack.decode(blob)
    assert isinstance(payload, dict)
    payload_dict = dict(payload)
    trace = dict(payload_dict["trace"])
    assert trace["events"] != []
    legacy_event = dict(trace["events"][0]["payload"])
    trace["events"] = (legacy_event,)
    payload_dict["trace"] = trace

    bad_blob = msgspec.msgpack.encode(payload_dict)
    with pytest.raises(InfrastructureError) as exc_info:
        ReplayArtifactReader().deserialize(bad_blob)
    assert str(exc_info.value).startswith("replay.incompatible_trace_payload: ")


def test_read_replay_propagates_storage_missing(tmp_path: Path) -> None:
    """`read_replay` on missing key surfaces
    `InfrastructureError("storage.missing_object: ...")` from the
    backend."""

    from extractx.replay import read_replay

    store = LocalFilesystemStore(tmp_path)
    with pytest.raises(InfrastructureError) as exc_info:
        read_replay(store, "no-such-id")
    assert str(exc_info.value).startswith("storage.missing_object: ")


def test_read_manifest_missing_uses_storage_prefix(tmp_path: Path) -> None:
    from extractx.replay import read_manifest

    store = LocalFilesystemStore(tmp_path)
    with pytest.raises(InfrastructureError) as exc_info:
        read_manifest(store, "no-such-run")
    assert str(exc_info.value).startswith("storage.missing_manifest: ")
