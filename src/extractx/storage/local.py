"""`LocalFilesystemStore` — phase-1 local-filesystem backend for `ExtractxStore`.

per ADR-0007 §3 / §8 minimum skeleton. layout:

- `objects/source/<content-hash>.bin`
- `objects/source/<content-hash>.meta.json` — parser metadata, `{}` in phase 1
- `objects/spec/<spec-version>.json`
- `objects/replay/<artifact-id>.msgpack`
- `runs/<run-id>.json`

phase-1 atomicity assumes POSIX `os.replace` semantics within a single
filesystem. cross-filesystem and Windows-specific atomicity edge cases
are out of scope (M9 phase-1 brief drift §10).

`InfrastructureError` is the sole exception class raised; failure cause
is encoded by message prefix:

- `"storage.missing_object: ..."`     — `get_object` on absent key
- `"storage.missing_manifest: ..."`   — `get_manifest` on absent key
- `"storage.collision: ..."`          — `put_object` with differing bytes
- `"storage.write_failed: ..."`       — io / permission failure
- `"storage.atomic_violation: ..."`   — `os.replace` failed
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from extractx.core.exceptions import InfrastructureError

from .protocol import ObjectKind

__all__ = ["LocalFilesystemStore"]


_OBJECT_KIND_EXTENSIONS: dict[ObjectKind, str] = {
    "source": ".bin",
    "spec": ".json",
    "replay": ".msgpack",
}
"""file extension per `ObjectKind` for the phase-1 layout."""


_OBJECTS_DIR = "objects"
_RUNS_DIR = "runs"
_MANIFEST_EXT = ".json"
_SOURCE_META_EXT = ".meta.json"


class LocalFilesystemStore:
    """phase-1 local-filesystem backend for `ExtractxStore`.

    structural `ExtractxStore` subtype — no protocol inheritance
    required. atomic-write discipline lives here:

    - every put writes to `<path>.tmp` then `os.replace(...)` to final
    - reads do not retry; absent keys surface as `InfrastructureError`
    - puts of identical bytes are idempotent; differing bytes raise
      `InfrastructureError("storage.collision: ...")`
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        # ensure the canonical layout exists; future backends may defer
        # directory creation to first-write, but a phase-1 local store
        # creates the skeleton up front so missing-directory failures
        # surface at construction rather than mid-run.
        for kind_dir in _OBJECT_KIND_EXTENSIONS:
            (self._root / _OBJECTS_DIR / kind_dir).mkdir(parents=True, exist_ok=True)
        (self._root / _RUNS_DIR).mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """return the store root path. helper for tests / diagnostics."""

        return self._root

    # ------------------------------------------------------------------
    # objects
    # ------------------------------------------------------------------

    def _object_path(self, kind: ObjectKind, content_id: str) -> Path:
        ext = _OBJECT_KIND_EXTENSIONS[kind]
        return self._root / _OBJECTS_DIR / kind / f"{content_id}{ext}"

    def put_object(self, kind: ObjectKind, content_id: str, blob: bytes) -> None:
        path = self._object_path(kind, content_id)
        if path.exists():
            existing = self._read_bytes(path)
            if existing == blob:
                # idempotent re-write: same content-hash → same bytes.
                return
            raise InfrastructureError(
                f"storage.collision: object kind={kind!r} content_id={content_id!r} "
                "already exists with different bytes",
            )
        self._atomic_write(path, blob)
        if kind == "source":
            # emit the `.meta.json` sibling per ADR-0007 §3. parser
            # metadata is empty in phase 1; the slot exists honestly so
            # future parser-aware writes can populate it without
            # re-shaping the layout.
            meta_path = path.with_suffix(_SOURCE_META_EXT)
            if not meta_path.exists():
                self._atomic_write(meta_path, b"{}")

    def get_object(self, kind: ObjectKind, content_id: str) -> bytes:
        path = self._object_path(kind, content_id)
        if not path.exists():
            raise InfrastructureError(
                f"storage.missing_object: kind={kind!r} content_id={content_id!r}",
            )
        return self._read_bytes(path)

    # ------------------------------------------------------------------
    # manifests
    # ------------------------------------------------------------------

    def _manifest_path(self, run_id: str) -> Path:
        return self._root / _RUNS_DIR / f"{run_id}{_MANIFEST_EXT}"

    def put_manifest(self, run_id: str, manifest_blob: bytes) -> None:
        path = self._manifest_path(run_id)
        # manifest writes overwrite existing manifests on the same
        # `run_id`. phase-1 callers always derive `run_id` from a fresh
        # uuid4 so collisions are not expected; the backend behavior is
        # last-write-wins for ergonomics.
        self._atomic_write(path, manifest_blob)

    def get_manifest(self, run_id: str) -> bytes:
        path = self._manifest_path(run_id)
        if not path.exists():
            raise InfrastructureError(
                f"storage.missing_manifest: run_id={run_id!r}",
            )
        return self._read_bytes(path)

    def list_run_ids(self) -> tuple[str, ...]:
        runs_dir = self._root / _RUNS_DIR
        if not runs_dir.exists():
            return ()
        run_ids: list[str] = []
        for entry in runs_dir.iterdir():
            if entry.is_file() and entry.suffix == _MANIFEST_EXT:
                run_ids.append(entry.stem)
        run_ids.sort()
        return tuple(run_ids)

    # ------------------------------------------------------------------
    # io helpers
    # ------------------------------------------------------------------

    def _atomic_write(self, path: Path, blob: bytes) -> None:
        """write `blob` to `path` atomically.

        pattern: write to `<path>.tmp` then `os.replace(...)` to final.
        POSIX semantics within a single filesystem; cross-filesystem
        and Windows-specific atomicity are out of scope (M9 phase-1
        drift §10).
        """

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp_path.open("wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise InfrastructureError(
                f"storage.write_failed: path={str(path)!r}: {exc!s}",
            ) from exc
        try:
            os.replace(tmp_path, path)
        except OSError as exc:
            # best-effort cleanup of the tmp file on replace failure.
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise InfrastructureError(
                f"storage.atomic_violation: path={str(path)!r}: {exc!s}",
            ) from exc

    def _read_bytes(self, path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise InfrastructureError(
                f"storage.write_failed: read failed path={str(path)!r}: {exc!s}",
            ) from exc
