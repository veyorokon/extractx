"""lifecycle outcome objects per docs/architecture.md §9.

houses `ProposedField`, `ValidatedField`, `Evidence`,
`NegativeOutcome`, `ValidationFailure`, `Instance`,
`Extraction`, plus `ProposalProvenance`, `ArtifactRef`, and
`ExecutionTrace`.

lifecycle invariant (§9 / §15 "Lifecycle-Object Conflation"):

- `ProposedField`         post-selection, pre-normalization
- `ValidatedField`        post-normalization, pre-resolution
- `Evidence`              post-resolution, public canonical

all three are immutable. post-construction mutation is rejected by the
pydantic frozen config.

`Extraction.instances` is canonical; `.evidence()`, `.negatives()`, `.stream()`,
`.to_pydantic()`, and `.usage()` are derived projections. `.interview()`
requires transcript capture that has not landed yet, so it remains a typed stub.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator, model_validator

from .anchors import SourceSpan
from .objects import (
    ContextPack,
    FieldId,
    GroupingEvidence,
    InstanceGroupingKey,
    InstanceState,
    UsageEvent,
)

__all__ = [
    "ArtifactRef",
    "ExecutionTrace",
    "Evidence",
    "Extraction",
    "FieldRef",
    "Instance",
    "NegativeOutcome",
    "ObjectIssue",
    "ProposalProvenance",
    "ProposedField",
    "ValidatedField",
    "ValidationFailure",
]


# ---------------------------------------------------------------------------
# replay / trace support objects
# ---------------------------------------------------------------------------


type ArtifactRef = str
"""opaque reference to a `ReplayArtifact`.

shaped by the replay task. today this is a bare alias for the reference
string that `Extraction.replay_artifact_ref` carries."""


class FieldRef(BaseModel):
    """field-level reference used by object validation issues."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: FieldId
    candidate_id_refs: tuple[str, ...] = ()


class ObjectIssue(BaseModel):
    """structured cross-field issue within one extracted object."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: Literal["warning", "error"] = "error"
    code: str = "object_validation_failed"
    reason: str
    implicates: tuple[FieldRef, ...] = ()


class NegativeOutcome(BaseModel):
    """see docs/architecture.md §9.

    category enum matches the documented surface across seams; `code` is a
    free-form stable identifier under the category (e.g.
    `"resolution.ambiguous_grouping"` uses category `"resolution"` + code
    `"ambiguous_grouping"`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: Literal[
        "selection",
        "validation",
        "budget",
        "resolution",
        "adaptation",
        "planning",
    ]
    code: str
    field_id: FieldId | None = None
    instance_key: InstanceGroupingKey | None = None
    reason: str
    candidate_count: int | None = None
    object_issues: tuple[ObjectIssue, ...] = ()


class ExecutionTrace(BaseModel):
    """minimal run trace carried on `Extraction.trace`.

    seam-K phase 1 types `events` to the only supported-path event shape
    emitted today: deterministic `NegativeOutcome` payloads surfaced by
    the executor when a run resolves to zero instances. richer OTEL span
    semantics remain reporter-owned future work.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: str
    events: tuple[NegativeOutcome, ...] = ()

    @field_validator("events", mode="before")
    @classmethod
    def _validate_events_wire_payload(cls, value: Any) -> tuple[NegativeOutcome, ...] | Any:
        if value is None:
            return ()
        if not isinstance(value, (tuple, list)):
            return value

        events: list[NegativeOutcome] = []
        raw_events = cast("tuple[object, ...] | list[object]", value)
        for raw in raw_events:
            if isinstance(raw, NegativeOutcome):
                events.append(raw)
                continue
            if isinstance(raw, Mapping):
                raw_mapping = cast("Mapping[str, object]", raw)
                kind = raw_mapping.get("kind")
                payload = raw_mapping.get("payload")
                if kind == "negative_outcome" and isinstance(payload, Mapping):
                    events.append(NegativeOutcome.model_validate(payload))
                    continue
                raise ValueError(
                    "ExecutionTrace.events entries must be typed negative_outcome payloads",
                )
            return cast("Any", value)
        return tuple(events)

    @field_serializer("events")
    def _serialize_events(
        self,
        events: tuple[NegativeOutcome, ...],
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "kind": "negative_outcome",
                "payload": event.model_dump(mode="python"),
            }
            for event in events
        )


class ProposalProvenance(BaseModel):
    """v1 provenance contract for sealed `Evidence`.

    This captures the producer identities needed to trace a sealed fact
    back through the extraction layer without embedding replay payloads.
    It is intentionally minimal: field validation and instance grouping
    provenance live on their own lifecycle objects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    candidate_id_refs: tuple[str, ...] = ()
    selector_producer_version: str | None = None
    grounded_producer_version: str | None = None


# ---------------------------------------------------------------------------
# lifecycle objects
# ---------------------------------------------------------------------------


class ProposedField(BaseModel):
    """see docs/architecture.md §9 (post-selection, pre-normalization)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: FieldId
    tentative_instance_key: InstanceGroupingKey | None = None
    raw_value: str
    evidence_text: str
    source_span: SourceSpan
    evidence_spans: tuple[SourceSpan, ...] = ()
    normalized_hint: Any | None = None
    candidate_id_refs: tuple[str, ...] = ()
    strategy_id: str
    selector_producer_version: str | None = None
    grounded_producer_version: str | None = None


class ValidatedField(BaseModel):
    """see docs/architecture.md §9 (post-normalization)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposed: ProposedField
    normalized_value: Any
    field_validation_version: str


class Evidence(BaseModel):
    """post-validation sealed evidence for one extracted field.

    `instance_id` is the public extraction-level instance handle.
    `instance_key` is the internal resolver/planner grouping key when that
    diagnostic context is available.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: FieldId
    instance_id: str
    instance_key: InstanceGroupingKey | None = None
    raw_value: str
    evidence_text: str
    source_span: SourceSpan
    evidence_spans: tuple[SourceSpan, ...] = ()
    normalized_value: Any
    proposal_provenance: ProposalProvenance

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_instance_key(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        raw = cast("Mapping[str, object]", value)
        data: dict[str, object] = dict(raw)
        key = data.get("instance_key")
        if data.get("instance_id") is None and key is not None:
            if isinstance(key, InstanceGroupingKey):
                data["instance_id"] = key.group_id
            elif isinstance(key, Mapping):
                key_mapping = cast("Mapping[str, object]", key)
                raw_group_id = key_mapping.get("group_id")
                if raw_group_id is not None:
                    data["instance_id"] = str(raw_group_id)
        return data

# ---------------------------------------------------------------------------
# typed failures
# ---------------------------------------------------------------------------


class ValidationFailure(BaseModel):
    """see docs/architecture.md §9.

    short-lived typed failure routed through `ExecutorPolicy`; never
    surfaced to the caller as a raised exception.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: Literal["candidate", "field", "instance"]
    field_id: FieldId
    instance_key: InstanceGroupingKey | None = None
    reason: str
    producer_version: str | None = None
    object_issues: tuple[ObjectIssue, ...] = ()


# ---------------------------------------------------------------------------
# result objects
# ---------------------------------------------------------------------------


class Instance(BaseModel):
    """canonical extraction instance.

    `evidence` is authoritative.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str
    instance_key: InstanceGroupingKey | None = None
    outcome: Literal["complete", "partial"]
    evidence: tuple[Evidence, ...] = ()
    negative_outcomes: tuple[NegativeOutcome, ...] = ()
    grouping_evidence: GroupingEvidence

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_instance_shape(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        raw = cast("Mapping[str, object]", value)
        data: dict[str, object] = dict(raw)
        key = data.get("instance_key")
        if data.get("instance_id") is None and key is not None:
            if isinstance(key, InstanceGroupingKey):
                data["instance_id"] = key.group_id
            elif isinstance(key, Mapping):
                key_mapping = cast("Mapping[str, object]", key)
                raw_group_id = key_mapping.get("group_id")
                if raw_group_id is not None:
                    data["instance_id"] = str(raw_group_id)
        return data

    def to_pydantic(self, cls: type[Any]) -> Any:
        """materialize this instance into a user-facing pydantic instance.

        implementation lives in `schema/to_pydantic.py`; import lazily to
        keep core independent of schema import order.
        """

        from ..schema.to_pydantic import instance_to_pydantic

        return instance_to_pydantic(self, cls)

class Extraction(BaseModel):
    """see docs/architecture.md §9 and §13.

    `instances` is canonical; the other methods are derived projections.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    spec_version: str
    outcome: Literal["complete", "partial", "failed"]
    strategy: Literal["independent", "iterative", "batch"]
    instances: tuple[Instance, ...]
    trace: ExecutionTrace
    replay_artifact_ref: ArtifactRef
    usage_events: tuple[UsageEvent, ...] = ()

    def evidence(self) -> tuple[Evidence, ...]:
        """flatten `instances[*].evidence` in order.

        derived projection; `instances` remains canonical.
        """

        out: list[Evidence] = []
        for instance in self.instances:
            out.extend(instance.evidence)
        return tuple(out)

    def negatives(self) -> tuple[NegativeOutcome, ...]:
        """flatten `instances[*].negative_outcomes` in order.

        derived projection; `instances` remains canonical.
        """

        out: list[NegativeOutcome] = []
        for instance in self.instances:
            out.extend(instance.negative_outcomes)
        return tuple(out)

    async def stream(self) -> AsyncIterator[Instance]:
        """post-hoc async iterator over `self.instances`.

        real-time streaming during execution is executor-owned and out of
        scope for core (see docs/architecture.md §13 streaming semantics).
        this method exists for symmetry with the public api surface and
        yields instances in declaration order.
        """

        for instance in self.instances:
            yield instance

    def to_pydantic(self, cls: type[Any]) -> list[Any]:
        """materialize every instance as a user-facing pydantic instance.

        implementation lives in `schema/to_pydantic.py`; import lazily to
        keep core independent of schema import order.
        """

        from ..schema.to_pydantic import result_to_pydantic

        return result_to_pydantic(self, cls)

    def usage(self) -> tuple[UsageEvent, ...]:
        """return the ordered stream of `UsageEvent`s emitted during the run.

        usage is operational metadata, not evidence. provider-native
        details remain available via `UsageEvent.raw_usage` /
        `raw_response_metadata`; extractx does not compute cost.
        """

        return self.usage_events

    def interview(
        self,
        *,
        field_id: FieldId,
        instance_key: InstanceGroupingKey | None = None,
        attempt_index: int | None = None,
        question: str,
    ) -> str:
        """rehydrate a captured pydantic-ai transcript and ask a follow-up.

        stub: interview implementation is owned by
        `extras/pydantic_ai/interview.py` (ADR-0002 / ADR-0004).
        """

        raise NotImplementedError(
            "Extraction.interview is a stub; implementation lands in "
            "extras/pydantic_ai/interview.py (see ADR-0002 / ADR-0004).",
        )

# ---------------------------------------------------------------------------
# forward-reference resolution
# ---------------------------------------------------------------------------
#
# `ContextPack.prior_proposals` and `InstanceState.accepted_proposals` /
# `InstanceState.negatives_so_far` carry string forward references to
# `ValidatedField` / `NegativeOutcome` defined above. rebuild those models
# now that the referenced classes exist so pydantic resolves the annotations.
_NAMESPACE: dict[str, Any] = {
    "ValidatedField": ValidatedField,
    "NegativeOutcome": NegativeOutcome,
}

ContextPack.model_rebuild(_types_namespace=_NAMESPACE)
InstanceState.model_rebuild(_types_namespace=_NAMESPACE)
