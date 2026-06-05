"""`RunManifest` per docs/tasks/m9-phase-1-replay-storage-skeleton.md §4
and ADR-0007 §5.

the manifest carries `run_id` (per-execution-attempt token; uuid4 in
phase 1) and `run_fingerprint` (deterministic equivalence token;
content hash over the deterministic tuple identifying the run).

phase-1 invariant: the manifest is **derived from the artifact at
write time** — never assembled from raw run state independently. the
single allowed construction site is `RunManifest.from_artifact(...)`.
that pin guarantees manifest and artifact never drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from extractx.core.anchors import SourceRef
from extractx.core.versions import stable_hash
from extractx.replay.artifact import ReplayArtifact

from .policy import PolicySummary

__all__ = ["RunManifest", "compute_run_fingerprint"]


def compute_run_fingerprint(artifact: ReplayArtifact) -> str:
    """compose the deterministic `run_fingerprint` for `artifact`.

    tuple shape (load-bearing per the M9 phase-1 brief):

    `(source_ref.content_hash, spec_version, sorted_producer_versions_items,
      policy_summary_dump, strategy, runtime_bindings_summary)`

    `sorted_producer_versions_items` is `sorted(producer_versions.items())`
    so two runs that agree on every value but differ in the iteration
    order of the mapping still produce the same fingerprint.

    `policy_summary_dump` is `policy_summary.model_dump(mode="json")` —
    pydantic's stable mapping order keeps the hash deterministic across
    runs.

    `strategy` and `runtime_bindings_summary` are scalars copied from the
    artifact verbatim.

    no wall-clock, no `run_id`, and no serialized-artifact bytes.
    `run_id` is orthogonal to fingerprint identity; identical run-shape
    inputs produce identical fingerprint and different `run_id`s. the
    replay artifact content hash (`replay_artifact_ref`) may be stricter
    than this fingerprint when artifacts carry operational metadata.
    """

    payload = (
        artifact.source_ref.content_hash,
        artifact.spec_version,
        sorted(artifact.producer_versions.items()),
        artifact.policy_summary.model_dump(mode="json"),
        artifact.strategy,
        artifact.runtime_bindings_summary,
    )
    return stable_hash(payload)


class RunManifest(BaseModel):
    """phase-1 run manifest persisted at `runs/<run-id>.json`.

    canonical fields (per the M9 phase-1 brief):

    - `manifest_version`
    - `run_id` — fresh per execution attempt; uuid4 in phase 1
    - `run_fingerprint` — deterministic equivalence token; see
      `compute_run_fingerprint(...)` above
    - `source_ref`
    - `spec_version`
    - `replay_ref` — artifact id (content hash of serialized artifact bytes)
    - `result_ref` — phase-1 always `None`; ADR-0007 §4 reserved
    - `interview_refs` — phase-1 always empty; ADR-0007 §4 reserved
    - `runtime_bindings_summary`
    - `policy_summary`
    - `producer_versions`
    - `strategy`
    - `outcome`
    - `tags` — phase-1 default-empty
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_version: Literal["v1"] = "v1"
    run_id: str
    run_fingerprint: str
    source_ref: SourceRef
    spec_version: str
    replay_ref: str
    result_ref: str | None = None
    interview_refs: tuple[str, ...] = ()
    runtime_bindings_summary: str
    policy_summary: PolicySummary
    producer_versions: Mapping[str, str]
    strategy: Literal["independent", "iterative", "batch"]
    outcome: Literal["complete", "partial", "failed"]
    tags: Mapping[str, str] = Field(default_factory=dict)

    @classmethod
    def from_artifact(
        cls,
        artifact: ReplayArtifact,
        *,
        run_id: str,
        replay_ref: str,
    ) -> RunManifest:
        """derive a `RunManifest` from `artifact`.

        every overlapping field on the manifest is copied from the
        artifact verbatim so the two records never drift. callers
        supply the per-execution `run_id` (uuid4) and the replay
        artifact id (content hash of the serialized artifact bytes).

        this is the **single** allowed manifest-construction site at
        the executor — manual field-by-field manifest construction is
        explicitly forbidden by the brief.
        """

        return cls(
            manifest_version="v1",
            run_id=run_id,
            run_fingerprint=compute_run_fingerprint(artifact),
            source_ref=artifact.source_ref,
            spec_version=artifact.spec_version,
            replay_ref=replay_ref,
            result_ref=None,
            interview_refs=(),
            runtime_bindings_summary=artifact.runtime_bindings_summary,
            policy_summary=artifact.policy_summary,
            producer_versions=dict(artifact.producer_versions),
            strategy=artifact.strategy,
            outcome=artifact.outcome,
            tags={},
        )
