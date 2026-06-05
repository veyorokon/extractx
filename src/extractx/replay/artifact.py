"""`ReplayArtifact` canonical object per docs/architecture.md §9 and §7 seam H.

phase-1 (M9 phase 1) shape per
docs/tasks/m9-phase-1-replay-storage-skeleton.md §1.

invariants:

- frozen / `extra="forbid"`. the field list is load-bearing; do **not**
  widen silently.
- `InterviewTranscript` is a sibling artifact, never embedded here per
  anti-pattern §15 `Transcripts-In-Default-Replay-Artifact` and
  ADR-0004.
- prompt text and raw llm response bodies are **not** embedded;
  `UsageEvent.raw_usage` rides through unchanged per ADR-0001.
- `replay_artifact_ref` does **not** appear on the artifact (would cycle
  with `Extraction.replay_artifact_ref`).
- `pydantic_schema_hash` does **not** appear (would duplicate
  `spec_version`; the architecture's "self-describing" claim is
  satisfied by `schema_version` + `extractx_version` + `spec_version`).
    - no embedded `Extraction` — reconstruction composes the result
      from the canonical fields below.
    - `replay_artifact_ref` is a content hash of serialized artifact
      bytes, not a semantic idempotency key. use `RunManifest.run_fingerprint`
      for run-equivalence queries.

the artifact is plugin-public per architecture §10 (already listed in
the §10 plugin-public table) but is **not** widened to tier-1 in phase
1. `ReplayArtifact` does not appear in `extractx.__init__`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, model_validator

from extractx.core.anchors import SourceRef
from extractx.core.objects import (
    CandidateSet,
    InstanceCandidateSet,
    InstanceProposerResponse,
    Observation,
    UsageEvent,
)
from extractx.core.outcomes import (
    ExecutionTrace,
    NegativeOutcome,
    ValidatedField,
)
from extractx.execution.policy import PolicySummary

from .diagnostics import SelectorCallDiagnostic
from .schema import (
    CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION,
    REPLAY_ARTIFACT_SCHEMA_V1,
    REPLAY_ARTIFACT_SCHEMA_V2,
    REPLAY_ARTIFACT_SCHEMA_V3,
    ReplayArtifactSchemaVersion,
)
from .vocabulary import Instance

__all__ = ["ReplayArtifact"]


class ReplayArtifact(BaseModel):
    """canonical replay-record for one execution.

    canonical fields (per the M9 phase-1 brief):

    - `schema_version` — artifact format version pin
    - `extractx_version` — package version at write time
    - `source_ref` — input identity
    - `document_id`
    - `spec_version` — content-hash identity
    - `strategy` — `"independent"` / `"iterative"` / `"batch"`
    - `outcome` — `"complete"` / `"partial"` / `"failed"`
    - `producer_versions` — phase-1 keys: `"candidate_strategy"`,
      `"selector"`, `"resolver"`, plus `"validator"` (added by the
      replay drift-gate phase-1 thread covering
      `LayeredProposalValidator`). no `"planner"` / `"strategy"` /
      `"executor"` keys.
    - `policy_summary`
    - `runtime_bindings_summary` — `stable_hash` over a deterministic
      tuple describing the bound capabilities
    - `candidate_sets` — full per-field, in `spec.fields` declaration
      order
    - `observations` — one per consumed `CandidateSet` (or empty when
      seam D was not invoked)
    - `validated_fields` — final layer-2 outputs in seam-F call order
    - `pre_resolver_negatives` — produced before `G.resolver`
    - `final_instances` — exactly the post-layer-3 instances on
      `Extraction.instances`
    - `usage_events` — ordered operational metadata emitted by soft-compute
      seams; empty when a run uses only algorithmic producers
    - `trace` — same `ExecutionTrace` carried by the emitted result

    ### schema_version evolution

    `schema_version` `"v3"` is the current write schema and carries
    `selector_call_diagnostics`. v2 bytes deserialize with
    `selector_call_diagnostics=()`. v1 bytes are read by translating
    legacy `selections` into `observations` at the replay boundary;
    current writes must not emit `selections`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: ReplayArtifactSchemaVersion = CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION
    extractx_version: str
    source_ref: SourceRef
    document_id: str
    spec_version: str
    strategy: Literal["independent", "iterative", "batch"]
    outcome: Literal["complete", "partial", "failed"]
    producer_versions: Mapping[str, str]
    policy_summary: PolicySummary
    runtime_bindings_summary: str
    candidate_sets: tuple[CandidateSet, ...]
    instance_candidate_set: InstanceCandidateSet | None = None
    instance_proposer_response: InstanceProposerResponse | None = None
    instance_proposer_metadata: Mapping[str, object] | None = None
    observations: tuple[Observation, ...]
    selector_call_diagnostics: tuple[SelectorCallDiagnostic, ...] = ()
    validated_fields: tuple[ValidatedField, ...]
    pre_resolver_negatives: tuple[NegativeOutcome, ...]
    final_instances: tuple[Instance, ...]
    usage_events: tuple[UsageEvent, ...]
    trace: ExecutionTrace

    @model_validator(mode="before")
    @classmethod
    def _validate_versioned_fields(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = cast("Mapping[str, object]", value)
        schema_version = data.get("schema_version", REPLAY_ARTIFACT_SCHEMA_V1)
        if schema_version in {REPLAY_ARTIFACT_SCHEMA_V2, REPLAY_ARTIFACT_SCHEMA_V3} and (
            "observations" not in data
        ):
            raise ValueError("ReplayArtifact v2/v3 requires observations")
        if schema_version == REPLAY_ARTIFACT_SCHEMA_V1:
            normalized = dict(data)
            legacy_selections = normalized.pop("selections", None)
            if "observations" in normalized:
                observations = normalized["observations"]
                if observations not in ((), [], None):
                    raise ValueError(
                        "ReplayArtifact v1 must not carry observations; "
                        "use legacy selections or schema_version='v2'",
                    )
            normalized["observations"] = legacy_selections if legacy_selections is not None else ()
            normalized.setdefault("selector_call_diagnostics", ())
            return normalized
        if schema_version == REPLAY_ARTIFACT_SCHEMA_V2:
            normalized = dict(data)
            normalized.setdefault("selector_call_diagnostics", ())
            return normalized
        return data
