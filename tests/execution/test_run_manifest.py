"""`RunManifest` shape, derivation, and fingerprint tests.

per docs/tasks/m9-phase-1-replay-storage-skeleton.md §4 / §9.

asserts:

- `RunManifest.from_artifact(...)` is the only manifest-construction
  call site exercised by the executor (white-box: text-search
  `serial.py` for any other constructor)
- every manifest field overlapping with the artifact is identical
- identical inputs produce identical `run_fingerprint` (different
  `run_id`)
- `result_ref is None`, `interview_refs == ()`, `tags == {}`
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
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import StrategyBinding
from extractx.execution import RunManifest
from extractx.execution.executor.serial import SerialExecutor
from extractx.execution.manifest import compute_run_fingerprint
from extractx.replay import read_manifest, read_replay
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
# manifest-derived-from-artifact
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_overlaps_artifact_field_by_field(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    """every manifest field that also appears on the artifact has an
    identical value."""

    store = LocalFilesystemStore(tmp_path)
    executor = SerialExecutor(storage=store)
    result = await executor.execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    artifact = read_replay(store, result.replay_artifact_ref)
    run_id = store.list_run_ids()[0]
    manifest = read_manifest(store, run_id)

    assert manifest.source_ref == artifact.source_ref
    assert manifest.spec_version == artifact.spec_version
    assert manifest.runtime_bindings_summary == artifact.runtime_bindings_summary
    assert manifest.policy_summary == artifact.policy_summary
    assert dict(manifest.producer_versions) == dict(artifact.producer_versions)
    assert manifest.strategy == artifact.strategy
    assert manifest.outcome == artifact.outcome
    assert manifest.replay_ref == result.replay_artifact_ref


@pytest.mark.asyncio
async def test_manifest_phase1_reserved_fields(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    """phase-1 reserves `result_ref`, `interview_refs`, `tags`."""

    store = LocalFilesystemStore(tmp_path)
    executor = SerialExecutor(storage=store)
    await executor.execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    manifest = read_manifest(store, store.list_run_ids()[0])
    assert manifest.result_ref is None
    assert manifest.interview_refs == ()
    assert dict(manifest.tags) == {}
    assert manifest.manifest_version == "v1"


# --------------------------------------------------------------------------
# run-fingerprint determinism
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_inputs_produce_identical_fingerprint(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    """two runs against identical `(document, spec, policy)` produce
    identical `run_fingerprint` even though `run_id` differs."""

    store_a = LocalFilesystemStore(tmp_path / "a")
    store_b = LocalFilesystemStore(tmp_path / "b")
    res_a = await SerialExecutor(storage=store_a).execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    res_b = await SerialExecutor(storage=store_b).execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    manifest_a = read_manifest(store_a, store_a.list_run_ids()[0])
    manifest_b = read_manifest(store_b, store_b.list_run_ids()[0])

    assert manifest_a.run_fingerprint == manifest_b.run_fingerprint
    assert manifest_a.run_id != manifest_b.run_id

    # also: identical artifact bytes for identical inputs.
    blob_a = store_a.get_object("replay", res_a.replay_artifact_ref)
    blob_b = store_b.get_object("replay", res_b.replay_artifact_ref)
    assert blob_a == blob_b
    assert res_a.replay_artifact_ref == res_b.replay_artifact_ref


# --------------------------------------------------------------------------
# from_artifact is the only constructor used by the executor
# --------------------------------------------------------------------------


def test_from_artifact_is_only_manifest_constructor_in_serial_py() -> None:
    """white-box: `serial.py` constructs `RunManifest` only via
    `RunManifest.from_artifact(...)`. M9 phase-1 hard pin #3."""

    import ast

    serial_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "extractx"
        / "execution"
        / "executor"
        / "serial.py"
    )
    tree = ast.parse(serial_py.read_text(encoding="utf-8"))

    direct_calls = 0
    from_artifact_calls = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # `RunManifest(...)` — direct constructor (forbidden in this file)
        if isinstance(func, ast.Name) and func.id == "RunManifest":
            direct_calls += 1
        # `RunManifest.from_artifact(...)` — the only allowed path
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "RunManifest"
            and func.attr == "from_artifact"
        ):
            from_artifact_calls += 1

    assert direct_calls == 0, (
        f"serial.py uses direct RunManifest(...) construction "
        f"({direct_calls} occurrence(s)); only "
        f"RunManifest.from_artifact(...) is allowed (M9 phase-1 hard pin #3)"
    )
    assert from_artifact_calls == 1, (
        f"expected exactly one RunManifest.from_artifact(...) call in "
        f"serial.py; got {from_artifact_calls}"
    )


# --------------------------------------------------------------------------
# compute_run_fingerprint signature / shape
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_run_fingerprint_is_deterministic(
    tmp_path: Path,
    spec,
    runtime,
    policy,
) -> None:
    """`compute_run_fingerprint(artifact)` is pure — same artifact,
    same fingerprint."""

    store = LocalFilesystemStore(tmp_path)
    res = await SerialExecutor(storage=store).execute(
        document="555-1234",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    artifact = read_replay(store, res.replay_artifact_ref)
    assert compute_run_fingerprint(artifact) == compute_run_fingerprint(artifact)


def test_run_manifest_has_expected_fields() -> None:
    """the manifest field list matches the M9 phase-1 brief §4 list."""

    expected = {
        "manifest_version",
        "run_id",
        "run_fingerprint",
        "source_ref",
        "spec_version",
        "replay_ref",
        "result_ref",
        "interview_refs",
        "runtime_bindings_summary",
        "policy_summary",
        "producer_versions",
        "strategy",
        "outcome",
        "tags",
    }
    assert set(RunManifest.model_fields.keys()) == expected
