"""Instance proposer seam helpers per ADR-0009."""

from __future__ import annotations

from extractx.core.anchors import SourceSpan
from extractx.core.objects import (
    CandidateSet,
    DocumentView,
    ExtractionSpec,
    GroupingEvidence,
    InstanceCandidate,
    InstanceCandidateSet,
    InstanceGroupingKey,
    InstancePlan,
    InstanceProposerResponse,
)
from extractx.core.versions import stable_hash

__all__ = [
    "InstanceProposerContractError",
    "build_instance_candidate_set",
    "enforce_instance_proposer_contract",
    "instance_candidate_set_hash",
    "instance_plan_from_response",
    "candidate_set_for_instance",
]


class InstanceProposerContractError(ValueError):
    """raised when an instance proposer violates the bounded-id contract."""


def build_instance_candidate_set(
    *,
    document_view: DocumentView,
    spec: ExtractionSpec,
    candidate_sets: tuple[CandidateSet, ...],
) -> InstanceCandidateSet:
    """Build deterministic document-local instance candidates.

    Phase 2's first bounded source is intentionally simple: group field
    candidates by normalized-text line. This gives the LLM candidate
    instance IDs with local anchors/context while keeping domain identity
    outside extractx.
    """

    grouped: dict[tuple[int, int], list[tuple[str, SourceSpan]]] = {}
    text_bytes = document_view.normalized_text.encode("utf-8")
    for candidate_set in candidate_sets:
        for candidate in candidate_set.candidates:
            key = _line_bounds(text_bytes, candidate.source_span.byte_start)
            grouped.setdefault(key, []).append((candidate.candidate_id, candidate.source_span))

    candidates: list[InstanceCandidate] = []
    for ordinal, (bounds, members) in enumerate(sorted(grouped.items())):
        byte_start, byte_end = bounds
        anchor_candidate_ids = tuple(cid for cid, _span in members)
        anchor_spans = tuple(span for _cid, span in members)
        candidates.append(
            InstanceCandidate(
                instance_id=f"inst_{ordinal}",
                instance_type=spec.instance_type,
                label=f"{spec.instance_type} {ordinal}",
                anchor_candidate_ids=anchor_candidate_ids,
                anchor_spans=anchor_spans,
                context=text_bytes[byte_start:byte_end].decode("utf-8", errors="replace"),
            ),
        )

    return InstanceCandidateSet(
        document_id=document_view.document_id,
        instance_type=spec.instance_type,
        candidates=tuple(candidates),
    )


def enforce_instance_proposer_contract(
    response: InstanceProposerResponse,
    candidate_set: InstanceCandidateSet,
) -> InstanceProposerResponse:
    """Validate a raw proposer response against the bounded candidate set."""

    allowed = {candidate.instance_id for candidate in candidate_set.candidates}
    seen: set[str] = set()
    duplicates: list[str] = []
    unknown: list[str] = []
    for instance_id in response.selected_instance_ids:
        if instance_id in seen:
            duplicates.append(instance_id)
        seen.add(instance_id)
        if instance_id not in allowed:
            unknown.append(instance_id)

    if unknown:
        raise InstanceProposerContractError(
            "instance_proposer.conflicting: selected ids outside candidate set "
            f"{unknown!r}; allowed={sorted(allowed)!r}",
        )
    if duplicates:
        raise InstanceProposerContractError(
            "instance_proposer.conflicting: duplicate selected ids "
            f"{duplicates!r}",
        )
    if not response.selected_instance_ids:
        raise InstanceProposerContractError(
            "instance_proposer.insufficient: selected_instance_ids is empty",
        )
    return response


def instance_candidate_set_hash(candidate_set: InstanceCandidateSet) -> str:
    return stable_hash(candidate_set.model_dump(mode="json"))


def instance_plan_from_response(
    *,
    candidate_set: InstanceCandidateSet,
    response: InstanceProposerResponse,
    producer_version: str | None,
) -> InstancePlan:
    by_id = {candidate.instance_id: candidate for candidate in candidate_set.candidates}
    tentative_keys: list[InstanceGroupingKey] = []
    all_anchors: list[SourceSpan] = []
    for ordinal, instance_id in enumerate(response.selected_instance_ids):
        candidate = by_id[instance_id]
        anchors = candidate.anchor_spans
        all_anchors.extend(anchors)
        tentative_keys.append(
            InstanceGroupingKey(
                group_id=instance_id,
                ordinal=ordinal,
                group_anchors=anchors,
            ),
        )
    return InstancePlan(
        tentative_keys=tuple(tentative_keys),
        grouping_evidence=GroupingEvidence(
            stage="planned",
            anchor_spans=tuple(all_anchors),
            clustering_signals={
                "mode": "instance_proposer",
                "instance_candidate_set_hash": instance_candidate_set_hash(candidate_set),
            },
            confidence=None,
            producer_version=producer_version or "code:instance_proposer",
        ),
        producer_version=producer_version,
    )


def candidate_set_for_instance(
    *,
    candidate_set: CandidateSet,
    instance_candidate: InstanceCandidate,
    instance_key: InstanceGroupingKey,
) -> CandidateSet:
    allowed = set(instance_candidate.anchor_candidate_ids)
    candidates = tuple(
        candidate for candidate in candidate_set.candidates if candidate.candidate_id in allowed
    )
    return candidate_set.model_copy(
        update={
            "instance_hint": instance_key,
            "candidates": candidates,
        },
    )


def _line_bounds(text_bytes: bytes, byte_offset: int) -> tuple[int, int]:
    start = text_bytes.rfind(b"\n", 0, byte_offset) + 1
    end = text_bytes.find(b"\n", byte_offset)
    if end == -1:
        end = len(text_bytes)
    return start, end
