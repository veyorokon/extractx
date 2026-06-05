"""Replay artifact schema-version constants and helpers."""

from __future__ import annotations

from typing import Literal

from extractx.core.exceptions import InfrastructureError

REPLAY_ARTIFACT_SCHEMA_V1: Literal["v1"] = "v1"
REPLAY_ARTIFACT_SCHEMA_V2: Literal["v2"] = "v2"
REPLAY_ARTIFACT_SCHEMA_V3: Literal["v3"] = "v3"

# Current writes include typed selector-call diagnostics. The reader still
# accepts v1/v2 bytes through explicit legacy normalization paths in
# `ReplayArtifact`.
CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION: Literal["v3"] = REPLAY_ARTIFACT_SCHEMA_V3

SUPPORTED_REPLAY_ARTIFACT_SCHEMA_VERSIONS: frozenset[str] = frozenset(
    {REPLAY_ARTIFACT_SCHEMA_V1, REPLAY_ARTIFACT_SCHEMA_V2, REPLAY_ARTIFACT_SCHEMA_V3},
)

ReplayArtifactSchemaVersion = Literal["v1", "v2", "v3"]

__all__ = [
    "CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION",
    "REPLAY_ARTIFACT_SCHEMA_V1",
    "REPLAY_ARTIFACT_SCHEMA_V2",
    "REPLAY_ARTIFACT_SCHEMA_V3",
    "ReplayArtifactSchemaVersion",
    "SUPPORTED_REPLAY_ARTIFACT_SCHEMA_VERSIONS",
    "assert_supported_replay_schema_version",
]


def assert_supported_replay_schema_version(schema_version: object) -> None:
    """Raise if ``schema_version`` is outside the replay reader contract."""

    if schema_version not in SUPPORTED_REPLAY_ARTIFACT_SCHEMA_VERSIONS:
        raise InfrastructureError(
            "replay.unknown_schema_version: artifact schema_version="
            f"{schema_version!r} is not in supported set "
            f"{sorted(SUPPORTED_REPLAY_ARTIFACT_SCHEMA_VERSIONS)!r}",
        )
