"""`LocalFilesystemStore` shape and failure-prefix tests.

per docs/tasks/m9-phase-1-replay-storage-skeleton.md §3 / §9.

these tests exercise the storage protocol concretely and assert the
documented `InfrastructureError` message-prefix conventions:

- `"storage.missing_object: ..."`
- `"storage.missing_manifest: ..."`
- `"storage.collision: ..."`
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extractx.core.exceptions import InfrastructureError
from extractx.storage import ExtractxStore, LocalFilesystemStore


def test_store_satisfies_protocol(tmp_path: Path) -> None:
    """`LocalFilesystemStore` is a structural `ExtractxStore`."""

    store = LocalFilesystemStore(tmp_path)
    assert isinstance(store, ExtractxStore)


def test_layout_directories_exist(tmp_path: Path) -> None:
    """phase-1 layout creates `objects/{source,spec,replay}` and `runs/`.

    asserts authority-boundary clauses from the M9 phase-1 brief §9:
    `objects/result/`, `objects/interview/`, and `views/` are NOT
    created in phase 1.
    """

    LocalFilesystemStore(tmp_path)
    assert (tmp_path / "objects" / "source").exists()
    assert (tmp_path / "objects" / "spec").exists()
    assert (tmp_path / "objects" / "replay").exists()
    assert (tmp_path / "runs").exists()
    # not implemented in phase 1
    assert not (tmp_path / "objects" / "result").exists()
    assert not (tmp_path / "objects" / "interview").exists()
    assert not (tmp_path / "views").exists()


def test_put_get_object_round_trip(tmp_path: Path) -> None:
    store = LocalFilesystemStore(tmp_path)
    store.put_object("source", "abc123", b"hello world")
    assert store.get_object("source", "abc123") == b"hello world"


def test_put_object_idempotent_on_identical_bytes(tmp_path: Path) -> None:
    """re-writing identical bytes under the same key is a no-op."""

    store = LocalFilesystemStore(tmp_path)
    store.put_object("source", "abc", b"hello")
    # second put with same bytes does not raise
    store.put_object("source", "abc", b"hello")
    assert store.get_object("source", "abc") == b"hello"


def test_put_object_collision_raises_with_prefix(tmp_path: Path) -> None:
    """different bytes under the same key raise with the documented prefix."""

    store = LocalFilesystemStore(tmp_path)
    store.put_object("source", "abc", b"hello")
    with pytest.raises(InfrastructureError) as exc_info:
        store.put_object("source", "abc", b"goodbye")
    assert str(exc_info.value).startswith("storage.collision: ")


def test_get_object_missing_raises_with_prefix(tmp_path: Path) -> None:
    store = LocalFilesystemStore(tmp_path)
    with pytest.raises(InfrastructureError) as exc_info:
        store.get_object("replay", "does-not-exist")
    assert str(exc_info.value).startswith("storage.missing_object: ")


def test_put_get_manifest_round_trip(tmp_path: Path) -> None:
    store = LocalFilesystemStore(tmp_path)
    store.put_manifest("run-1", b'{"ok": true}')
    assert store.get_manifest("run-1") == b'{"ok": true}'


def test_get_manifest_missing_raises_with_prefix(tmp_path: Path) -> None:
    store = LocalFilesystemStore(tmp_path)
    with pytest.raises(InfrastructureError) as exc_info:
        store.get_manifest("nope")
    assert str(exc_info.value).startswith("storage.missing_manifest: ")


def test_list_run_ids_deterministic_alphanumeric(tmp_path: Path) -> None:
    """`list_run_ids()` returns deterministic alphanumeric ordering."""

    store = LocalFilesystemStore(tmp_path)
    store.put_manifest("zeta", b"{}")
    store.put_manifest("alpha", b"{}")
    store.put_manifest("mu", b"{}")
    assert store.list_run_ids() == ("alpha", "mu", "zeta")


def test_source_meta_json_sibling_emitted(tmp_path: Path) -> None:
    """`objects/source/<id>.bin` is paired with a `.meta.json` sibling
    holding `{}` per ADR-0007 §3."""

    store = LocalFilesystemStore(tmp_path)
    store.put_object("source", "deadbeef", b"raw")
    bin_path = tmp_path / "objects" / "source" / "deadbeef.bin"
    meta_path = tmp_path / "objects" / "source" / "deadbeef.meta.json"
    assert bin_path.exists()
    assert meta_path.exists()
    assert meta_path.read_bytes() == b"{}"


def test_spec_blob_extension_is_json(tmp_path: Path) -> None:
    """`objects/spec/<spec-version>.json` carries the spec summary."""

    store = LocalFilesystemStore(tmp_path)
    store.put_object("spec", "spec-v1", b'{"summary_version": "v1"}')
    assert (tmp_path / "objects" / "spec" / "spec-v1.json").exists()


def test_replay_blob_extension_is_msgpack(tmp_path: Path) -> None:
    """`objects/replay/<artifact-id>.msgpack` carries the artifact bytes."""

    store = LocalFilesystemStore(tmp_path)
    store.put_object("replay", "abc", b"\x80")
    assert (tmp_path / "objects" / "replay" / "abc.msgpack").exists()
