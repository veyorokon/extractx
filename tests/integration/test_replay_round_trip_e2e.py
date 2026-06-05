"""end-to-end source-driven replay integration proof per M9 phase-2 §6.

reaches the executor via real `SerialExecutor.execute(...)` for capture
and `replay_re_execute(...)` for replay (which itself calls real
`SerialExecutor.execute(...)`). no benchmark-only path.

covers:

- multi-field pydantic-backed run, persisted, replayed, asserted
  equal under pydantic equality modulo `replay_artifact_ref`; typed
  `trace.events` participates in equality
- no second store entry written during replay
- registry-extension scope (proof target #9): `_CLASS_BY_QUALNAME` is
  populated alongside `_SCHEMA_CLS_BY_SPEC_VERSION` at `from_pydantic`
  time
- result materialization after replay: `.to_pydantic(...)` works on the
  replay-reproduced supported path while `.usage()` / `.interview()` remain
  stubs
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
from extractx.execution.executor.serial import SerialExecutor
from extractx.replay import (
    read_replay,
    replay_re_execute,
)
from extractx.replay.engine import assert_replay_result_equal
from extractx.schema._schema_cls_registry import (
    _CLASS_BY_QUALNAME,
    _SCHEMA_CLS_BY_SPEC_VERSION,
    lookup_class_by_qualname,
)
from extractx.storage import LocalFilesystemStore


class _Contact(BaseModel):
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


@pytest.mark.asyncio
async def test_e2e_multi_field_replay_round_trip(tmp_path: Path) -> None:
    """one persisted run, replayed; reproduced result byte-equal under
    the load-bearing equality."""

    spec = ExtractionSpec.from_pydantic(_Contact)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    store = LocalFilesystemStore(tmp_path)

    captured = await SerialExecutor(storage=store).execute(
        document="phone 555-1234, zip 90210",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    assert captured.outcome == "complete"
    assert len(captured.instances) == 1
    assert captured.replay_artifact_ref != ""

    artifact = read_replay(store, captured.replay_artifact_ref)
    reproduced = await replay_re_execute(artifact, store)

    assert_replay_result_equal(captured, reproduced)
    assert reproduced.replay_artifact_ref == ""


@pytest.mark.asyncio
async def test_e2e_replay_does_not_write_to_store(tmp_path: Path) -> None:
    """replay produces no new store entries — no second artifact, no
    second manifest, no second source blob."""

    spec = ExtractionSpec.from_pydantic(_Contact)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    store = LocalFilesystemStore(tmp_path)

    captured = await SerialExecutor(storage=store).execute(
        document="phone 555-1234, zip 90210",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    pre_files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    pre_run_ids = store.list_run_ids()
    assert len(pre_run_ids) == 1

    artifact = read_replay(store, captured.replay_artifact_ref)
    await replay_re_execute(artifact, store)

    post_files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    post_run_ids = store.list_run_ids()

    assert post_files == pre_files
    assert post_run_ids == pre_run_ids


def test_class_registry_extends_with_qualname_map() -> None:
    """`from_pydantic(_Contact)` registers the schema class in both
    `_SCHEMA_CLS_BY_SPEC_VERSION` and `_CLASS_BY_QUALNAME`. binding
    `cls` references (`StrategyBinding.cls`) are also registered in
    the qualname map."""

    spec = ExtractionSpec.from_pydantic(_Contact)
    schema_qualname = f"{_Contact.__module__}.{_Contact.__qualname__}"

    assert _SCHEMA_CLS_BY_SPEC_VERSION.get(spec.version) is _Contact
    assert _CLASS_BY_QUALNAME.get(schema_qualname) is _Contact
    assert lookup_class_by_qualname(schema_qualname) is _Contact

    # binding cls also registered defensively
    binding_qualname = f"{RegexCandidateStrategy.__module__}.{RegexCandidateStrategy.__qualname__}"
    assert lookup_class_by_qualname(binding_qualname) is RegexCandidateStrategy


def test_class_registry_collision_raises() -> None:
    """re-registering a different class under the same qualname raises
    `RuntimeError` (mirrors `_SCHEMA_CLS_BY_SPEC_VERSION` semantics)."""

    from extractx.schema._schema_cls_registry import register_class_by_qualname

    class _A:
        pass

    class _B:
        pass

    # rename _B's qualname so it collides with _A's qualname.
    _B.__qualname__ = _A.__qualname__
    _B.__module__ = _A.__module__

    register_class_by_qualname(_A)
    # idempotent on identical (qualname, cls)
    register_class_by_qualname(_A)
    # collision under a different class
    with pytest.raises(RuntimeError):
        register_class_by_qualname(_B)


# --------------------------------------------------------------------------
# materialization / remaining stub honesty
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replayed_result_materializes_and_remaining_stubs_raise(
    tmp_path: Path,
) -> None:
    """replay-reproduced results can materialize through the public schema
    projection; usage is available and interview remains stubbed."""

    spec = ExtractionSpec.from_pydantic(_Contact)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    store = LocalFilesystemStore(tmp_path)

    captured = await SerialExecutor(storage=store).execute(
        document="phone 555-1234, zip 90210",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )
    artifact = read_replay(store, captured.replay_artifact_ref)
    reproduced = await replay_re_execute(artifact, store)

    assert reproduced.usage() == ()
    with pytest.raises(NotImplementedError):
        reproduced.interview(field_id="phone", question="placeholder")
    assert len(reproduced.instances) == 1
    contact = reproduced.instances[0].to_pydantic(_Contact)
    assert isinstance(contact, _Contact)
    assert contact.phone == "555-1234"
    assert contact.zip_code == "90210"
