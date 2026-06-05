"""`ReplayArtifactWriter` per docs/architecture.md §7 seam H and the M9
phase-1 brief §6.

deterministic encoding pin: msgspec defaults with **no custom enc/dec
hooks**. concrete pattern:

```
blob = msgspec.msgpack.encode(artifact.model_dump(mode="python"))
artifact = ReplayArtifact.model_validate(msgspec.msgpack.decode(blob))
```

mappings serialize in pydantic's documented stable order; tuples
preserve order. determinism depends on pydantic's stable mapping
ordering inside `model_dump(mode="python")` — if a future pydantic
release perturbs that order, the artifact-bytes round-trip proof
surfaces the regression loudly.

phase-1 drift §1 (acknowledged in the brief): seam H names
`ReplayArtifactWriter` as a contract surface; phase-1 lands it as a
**concrete class**, not a `Protocol`. promotion to protocol-typed
indirection is deferred to a later thread.
"""

from __future__ import annotations

import hashlib

import msgspec

from .artifact import ReplayArtifact
from .schema import REPLAY_ARTIFACT_SCHEMA_V1, REPLAY_ARTIFACT_SCHEMA_V2

__all__ = ["ReplayArtifactWriter", "compute_artifact_id_from_bytes"]


def compute_artifact_id_from_bytes(blob: bytes) -> str:
    """return the canonical artifact id for serialized artifact bytes.

    `id == sha256(blob).hexdigest()`. same bytes produce same id;
    callers (writer + reader) share this helper so id computation is a
    single source of truth.

    `core.versions.stable_hash` expects json-safe values, not raw
    bytes. we use `hashlib.sha256` directly so the artifact id is a
    deterministic content hash of the serialized bytes — that mirrors
    the spirit of `stable_hash` (sha256 hex digest) without forcing
    `bytes` through json canonicalization.
    """

    return hashlib.sha256(blob).hexdigest()


class ReplayArtifactWriter:
    """phase-1 concrete `ReplayArtifact` serializer.

    stateless / pure. `serialize` produces deterministic bytes;
    `compute_artifact_id` produces the canonical id for those bytes.
    """

    def serialize(self, artifact: ReplayArtifact) -> bytes:
        """return the deterministic msgpack-encoded bytes for `artifact`."""

        # `model_dump(mode="python")` keeps tuples as tuples and pydantic
        # models as dicts with stable key ordering; msgspec defaults
        # encode that into a deterministic msgpack representation. no
        # custom enc_hook is supplied — that is the determinism pin.
        payload = artifact.model_dump(mode="python")
        if artifact.schema_version == REPLAY_ARTIFACT_SCHEMA_V2:
            payload.pop("selector_call_diagnostics", None)
        if artifact.schema_version == REPLAY_ARTIFACT_SCHEMA_V1:
            # Legacy v1 bytes named the seam-D tuples `selections`.
            # Current executor writes v3, but preserving this mapping
            # lets read -> write stay byte-stable for supported v1
            # artifacts.
            payload.pop("selector_call_diagnostics", None)
            payload = {
                ("selections" if key == "observations" else key): value
                for key, value in payload.items()
            }
        return msgspec.msgpack.encode(payload)

    def compute_artifact_id(self, blob: bytes) -> str:
        """return the canonical artifact id for `blob`. delegates to
        the module-level helper so reader and writer share the same id
        composition rule."""

        return compute_artifact_id_from_bytes(blob)
