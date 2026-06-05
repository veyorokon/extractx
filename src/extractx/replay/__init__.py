"""replay subsystem per docs/architecture.md §7 seam H.

phase-1 (M9 phase 1) public exports:

- `ReplayArtifact` — canonical replay-record (plugin-public per §10;
  not widened to tier-1 in phase 1)
- `ReplayArtifactWriter` / `ReplayArtifactReader` — concrete
  serializer / deserializer (drift §1: phase-1 lands them as concrete
  classes, not protocols)
- `read_replay` / `read_manifest` / `read_spec_summary` — top-level
  reader helpers
- `reconstruct_extraction_result` — phase-1 reconstruction (does not
  re-execute seams; drift §2)
- `compute_artifact_id_from_bytes` — content-addressing helper shared
  by writer and reader
"""

from __future__ import annotations

from .artifact import ReplayArtifact
from .diagnostics import SelectorCallDiagnostic
from .engine import assert_replay_extraction_equal, assert_replay_result_equal, replay_re_execute
from .reader import (
    ReplayArtifactReader,
    compute_artifact_id_from_bytes,
    read_manifest,
    read_replay,
    read_spec_summary,
    reconstruct_extraction,
    reconstruct_extraction_result,
)
from .schema import (
    CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION,
    REPLAY_ARTIFACT_SCHEMA_V1,
    REPLAY_ARTIFACT_SCHEMA_V2,
    REPLAY_ARTIFACT_SCHEMA_V3,
    SUPPORTED_REPLAY_ARTIFACT_SCHEMA_VERSIONS,
)
from .writer import ReplayArtifactWriter

__all__ = [
    "CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION",
    "REPLAY_ARTIFACT_SCHEMA_V1",
    "REPLAY_ARTIFACT_SCHEMA_V2",
    "REPLAY_ARTIFACT_SCHEMA_V3",
    "ReplayArtifact",
    "ReplayArtifactReader",
    "ReplayArtifactWriter",
    "SelectorCallDiagnostic",
    "SUPPORTED_REPLAY_ARTIFACT_SCHEMA_VERSIONS",
    "assert_replay_extraction_equal",
    "assert_replay_result_equal",
    "compute_artifact_id_from_bytes",
    "read_manifest",
    "read_replay",
    "read_spec_summary",
    "reconstruct_extraction",
    "reconstruct_extraction_result",
    "replay_re_execute",
]
