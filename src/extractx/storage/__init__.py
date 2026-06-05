"""storage subsystem per ADR-0007 and docs/architecture.md §7 seam H invariants.

phase-1 (M9 phase 1) public surface:

- `ExtractxStore` — backend-agnostic protocol
- `LocalFilesystemStore` — concrete local-filesystem backend
- `ObjectKind` — `Literal["source", "spec", "replay"]`

phase-1 layout (per ADR-0007 minimum skeleton):

- `objects/source/<content-hash>.bin` — raw source bytes
- `objects/source/<content-hash>.meta.json` — parser metadata (`{}`)
- `objects/spec/<spec-version>.json` — `SpecSummary` json
- `objects/replay/<artifact-id>.msgpack` — `ReplayArtifact` msgpack
- `runs/<run-id>.json` — `RunManifest` json

phase-1 explicitly out of scope: `objects/result/`, `objects/interview/`,
`views/`. `result` and `interview` are not `ObjectKind`s.

these symbols are **internal** in phase 1 — they are not exported from
`extractx.__init__` and do not widen the tier-1 surface.
"""

from __future__ import annotations

from .local import LocalFilesystemStore
from .protocol import ExtractxStore, ObjectKind

__all__ = ["ExtractxStore", "LocalFilesystemStore", "ObjectKind"]
