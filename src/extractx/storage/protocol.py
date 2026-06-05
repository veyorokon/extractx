"""`ExtractxStore` protocol per ADR-0007 §3 / §8 and the M9 phase-1 brief.

phase-1 surface is intentionally narrow:

- per-object put/get keyed by `(kind, content_id)`
- per-run put/get manifest keyed by `run_id`
- deterministic alphanumeric `list_run_ids()`

`ObjectKind = Literal["source", "spec", "replay"]` only. `result` and
`interview` are **not** kinds in phase 1; the result cache is deferred
per ADR-0007 §1, interview storage is deferred per ADR-0004.

the protocol exists so a future thread can drop in a second backend
(s3, gcs, db, …) without re-shaping callers — same wire api, same
exception surface, same idempotence semantics.

**failure semantics** (phase-1 contract):

- `get_object` / `get_manifest` on absent keys raise `InfrastructureError`
  with the documented message prefixes. they do **not** return `None`;
  callers must know the key.
- `put_object` is idempotent on identical bytes (same content-hash =
  same bytes); a write of *different* bytes under an already-occupied
  content-id raises `InfrastructureError("storage.collision: ...")`.
- io / permission failures raise `InfrastructureError("storage.write_failed: ...")`.
- `os.replace` failures raise `InfrastructureError("storage.atomic_violation: ...")`.

`InfrastructureError` is the **sole** public exception class for storage
failures in phase 1; no `StorageError` sibling is introduced. callers
that need to distinguish causes pattern-match on the message prefix.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

__all__ = ["ExtractxStore", "ObjectKind"]


type ObjectKind = Literal["source", "spec", "replay"]
"""the phase-1 set of canonical object kinds.

`result` and `interview` are reserved at the layout level (ADR-0007 §4)
but are not implemented as `ObjectKind`s in phase 1.
"""


@runtime_checkable
class ExtractxStore(Protocol):
    """backend-agnostic storage seam for phase-1 replay/manifest persistence.

    structural protocol — implementations need not subclass. concrete
    backends register against this protocol via duck-typing.
    """

    def put_object(self, kind: ObjectKind, content_id: str, blob: bytes) -> None:
        """write `blob` under `(kind, content_id)`.

        idempotent on identical bytes; raises `InfrastructureError`
        with prefix `"storage.collision: "` on differing bytes under the
        same key.
        """
        ...

    def get_object(self, kind: ObjectKind, content_id: str) -> bytes:
        """return the blob stored under `(kind, content_id)`.

        raises `InfrastructureError` with prefix
        `"storage.missing_object: "` on absent key.
        """
        ...

    def put_manifest(self, run_id: str, manifest_blob: bytes) -> None:
        """write `manifest_blob` under `run_id`.

        manifest writes are atomic per the local-filesystem backend
        contract; a future backend may relax that to provider-native
        atomicity.
        """
        ...

    def get_manifest(self, run_id: str) -> bytes:
        """return the manifest blob stored under `run_id`.

        raises `InfrastructureError` with prefix
        `"storage.missing_manifest: "` on absent key.
        """
        ...

    def list_run_ids(self) -> tuple[str, ...]:
        """return the set of known `run_id`s in deterministic alphanumeric order."""
        ...
