"""`ReplayArtifactReader` and reconstruction helpers per the M9 phase-1
brief §6 / §8.

phase-1 reader does **not** re-execute seams during reconstruction.
the artifact carries `final_instances` already in their post-layer-3
shape; `reconstruct_extraction_result(...)` composes the canonical
`Extraction` from those captured outputs (see drift §2 of the
M9 phase-1 brief).

seam-replay re-execution — re-running captured `candidate_sets` /
`observations` / `validated_fields` through C → D → E → F → G under
pinning to verify that the captured `final_instances` are reproducible
— is deferred to a later M9-extension thread.

failure surface (`InfrastructureError` with prefixed message):

- `"replay.unknown_schema_version: ..."` — artifact carries an unknown
  `schema_version`
- `"replay.malformed: ..."` — bytes do not decode to a valid
  `ReplayArtifact`
- `"replay.truncated: ..."` — msgpack decode raised on incomplete bytes
- `"replay.incompatible_trace_payload: ..."` — decoded trace event
  payloads do not satisfy the typed phase-1 `ExecutionTrace.events`
  contract
"""

from __future__ import annotations

import json
from typing import Any, cast

import msgspec
from pydantic import ValidationError

from extractx.core.exceptions import InfrastructureError
from extractx.execution.manifest import RunManifest
from extractx.schema.summary import SpecSummary
from extractx.storage.protocol import ExtractxStore

from .artifact import ReplayArtifact
from .schema import assert_supported_replay_schema_version
from .vocabulary import Extraction
from .writer import compute_artifact_id_from_bytes

__all__ = [
    "ReplayArtifactReader",
    "compute_artifact_id_from_bytes",
    "read_manifest",
    "read_replay",
    "read_spec_summary",
    "reconstruct_extraction",
    "reconstruct_extraction_result",
]


class ReplayArtifactReader:
    """phase-1 concrete `ReplayArtifact` deserializer.

    stateless / pure. rejects unknown `schema_version` with
    `InfrastructureError("replay.unknown_schema_version: ...")`.
    """

    def deserialize(self, blob: bytes) -> ReplayArtifact:
        """decode `blob` into a `ReplayArtifact`.

        raises `InfrastructureError` on malformed bytes, truncated
        msgpack, schema validation failures, or unsupported
        `schema_version`.
        """

        try:
            payload = msgspec.msgpack.decode(blob)
        except msgspec.DecodeError as exc:
            # msgspec does not distinguish truncated-vs-malformed at
            # the api surface; the `EOF` substring in the message is
            # the documented signal for truncated input.
            prefix = (
                "replay.truncated"
                if "EOF" in str(exc) or "truncated" in str(exc).lower()
                else "replay.malformed"
            )
            raise InfrastructureError(
                f"{prefix}: msgpack decode failed: {exc!s}",
            ) from exc

        if not isinstance(payload, dict):
            raise InfrastructureError(
                "replay.malformed: decoded msgpack payload is not a mapping",
            )

        payload_dict = cast("dict[str, Any]", payload)
        schema_version = payload_dict.get("schema_version")
        assert_supported_replay_schema_version(schema_version)

        try:
            return ReplayArtifact.model_validate(payload_dict)
        except ValidationError as exc:
            if _validation_error_touches_trace_events(exc):
                raise InfrastructureError(
                    "replay.incompatible_trace_payload: "
                    "ExecutionTrace.events must deserialize as "
                    f"NegativeOutcome payloads: {exc!s}",
                ) from exc
            raise InfrastructureError(
                f"replay.malformed: pydantic validation failed: {exc!s}",
            ) from exc


# ---------------------------------------------------------------------------
# top-level helpers — the executor / proof-test surface
# ---------------------------------------------------------------------------


def read_replay(store: ExtractxStore, artifact_id: str) -> ReplayArtifact:
    """read the replay artifact stored under `artifact_id`.

    raises `InfrastructureError` from the store on absent key, and
    `InfrastructureError("replay.*: ...")` from the reader on malformed
    bytes.
    """

    blob = store.get_object("replay", artifact_id)
    return ReplayArtifactReader().deserialize(blob)


def read_manifest(store: ExtractxStore, run_id: str) -> RunManifest:
    """read the run manifest stored under `run_id`.

    manifests are persisted as JSON; pydantic decodes them via
    `model_validate_json`. parse failures surface as
    `InfrastructureError("replay.malformed: ...")` for symmetry with
    the artifact reader.
    """

    blob = store.get_manifest(run_id)
    try:
        return RunManifest.model_validate_json(blob)
    except ValidationError as exc:
        raise InfrastructureError(
            f"replay.malformed: manifest pydantic validation failed for run_id={run_id!r}: {exc!s}",
        ) from exc


def read_spec_summary(store: ExtractxStore, spec_version: str) -> SpecSummary:
    """read the persisted `SpecSummary` keyed by `spec_version`.

    `objects/spec/<spec-version>.json` carries `SpecSummary`, **not**
    `ExtractionSpec` (M9 phase-1 brief drift §3). a future thread
    rehydrates `SpecSummary → ExtractionSpec` via a class registry; in
    phase 1 the summary is a leaf object.
    """

    blob = store.get_object("spec", spec_version)
    try:
        return SpecSummary.model_validate_json(blob)
    except ValidationError as exc:
        raise InfrastructureError(
            f"replay.malformed: spec summary pydantic validation failed for "
            f"spec_version={spec_version!r}: {exc!s}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise InfrastructureError(
            f"replay.malformed: spec summary json decode failed for "
            f"spec_version={spec_version!r}: {exc!s}",
        ) from exc


def reconstruct_extraction(
    artifact: ReplayArtifact,
    *,
    artifact_id: str,
) -> Extraction:
    """compose the canonical `Extraction` from a `ReplayArtifact`.

    phase-1 reconstruction is a structural composition: every field on
    the rebuilt `Extraction` is read directly from the artifact;
    no seam is re-executed (M9 phase-1 brief drift §2). the caller
    supplies `artifact_id` because the artifact intentionally does not
    carry its own id — content addressing means `id == hash(bytes)`,
    computed at read time via `compute_artifact_id_from_bytes(...)`.

    `ExecutionTrace.events` is already typed on the artifact. no replay
    shim rehydrates trace events here.
    """

    return Extraction(
        document_id=artifact.document_id,
        spec_version=artifact.spec_version,
        outcome=artifact.outcome,
        strategy=artifact.strategy,
        instances=artifact.final_instances,
        trace=artifact.trace,
        replay_artifact_ref=artifact_id,
        usage_events=artifact.usage_events,
    )


def reconstruct_extraction_result(
    artifact: ReplayArtifact,
    *,
    artifact_id: str,
) -> Extraction:
    """Backward-compatible name for `reconstruct_extraction`."""

    return reconstruct_extraction(artifact, artifact_id=artifact_id)


def _validation_error_touches_trace_events(exc: ValidationError) -> bool:
    """return whether pydantic rejected the trace event payload shape."""

    for error in exc.errors():
        loc = tuple(str(part) for part in error.get("loc", ()))
        if len(loc) >= 2 and loc[0] == "trace" and loc[1] == "events":
            return True
    return False
