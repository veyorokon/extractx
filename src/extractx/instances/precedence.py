"""precedence rule engine for `G.resolver` per docs/architecture.md §7 seam G.resolver.

phase-1 exposes pure helpers the `DeterministicInstanceResolver` uses
to compare tentative buckets against a `ValidatedField` under each of
the four documented authorities, in order:

1. explicit `GroupingBinding`
2. source-anchor continuity
3. candidate co-occurrence
4. `InstancePlan` tentative scaffolds

ADR-0003 removed the previous "validator consistency" authority. the
helpers in this module do not invoke validators and carry no retry /
backtrack logic.

each helper returns a small, deterministic structure telling the
resolver which tentative bucket indices (if any) a given authority
picks for a proposal. the resolver consumes these in order and emits
typed ambiguity negatives when no authority leaves exactly one winner.

helpers are pure and mechanical: no reporter, no budget, no seam
invocation, no logging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from extractx.core.anchors import SourceSpan

if TYPE_CHECKING:
    from extractx.core.objects import (
        CandidateSet,
        InstanceGroupingKey,
    )
    from extractx.core.outcomes import ValidatedField

__all__ = [
    "boundary_defining_bucket",
    "candidate_cooccurrence_buckets",
    "source_anchor_continuity_buckets",
    "spans_overlap",
    "spans_share_frame",
]


def spans_share_frame(left: SourceSpan, right: SourceSpan) -> bool:
    """return True iff two spans share `source_ref` and `text_anchor_space`.

    "frame" here is the deterministic coordinate system in which byte
    offsets are directly comparable. ADR-0006 forbids coercing across
    `text_anchor_space`s, so two spans that differ in either
    `source_ref` or `text_anchor_space` are not comparable at the byte
    level — the resolver treats them as silently disjoint for
    authority-2 / authority-3 purposes.
    """

    if left.source_ref != right.source_ref:
        return False
    return left.text_anchor_space == right.text_anchor_space


def spans_overlap(left: SourceSpan, right: SourceSpan) -> bool:
    """return True iff two spans share a frame and overlap by at least one byte.

    half-open intervals: `[byte_start, byte_end)`. document-level
    classification uses synthetic zero-length points; two zero-length
    spans at the same offset and frame overlap. otherwise, a zero-length
    span has no byte interval and does not overlap.
    """

    if not spans_share_frame(left, right):
        return False
    if (
        left.byte_start == left.byte_end
        and right.byte_start == right.byte_end
        and left.byte_start == right.byte_start
    ):
        return True
    if left.byte_start >= left.byte_end or right.byte_start >= right.byte_end:
        return False
    return left.byte_start < right.byte_end and right.byte_start < left.byte_end


def _min_byte_gap(left: SourceSpan, right: SourceSpan) -> int | None:
    """return the non-negative byte gap between `left` and `right`, or `None`.

    returns `None` if the spans do not share a frame (not comparable).
    returns `0` when they overlap. otherwise returns the distance
    between the closer of the two pairs of endpoints.
    """

    if not spans_share_frame(left, right):
        return None
    if spans_overlap(left, right):
        return 0
    # half-open intervals: if `left` ends before `right` begins, the
    # gap is `right.byte_start - left.byte_end`. and symmetrically.
    if left.byte_end <= right.byte_start:
        return right.byte_start - left.byte_end
    return left.byte_start - right.byte_end


def boundary_defining_bucket(
    *,
    tentative_keys: tuple[InstanceGroupingKey, ...],
    validated_field_tentative_instance_key: InstanceGroupingKey | None,
    grouping_role: str | None,
) -> int | None:
    """authority #1: explicit `GroupingBinding.role == "boundary_defining"`.

    when a validated field comes from a `boundary_defining` field and
    already carries a `tentative_instance_key`, the planner (or
    iterative pre-plan) declared that key as the bucket the field
    belongs to. if that tentative key matches one of the resolver's
    current tentative buckets (by equality), that bucket wins.

    returns the matching bucket index, or `None` when no unique match
    exists (including when the role is not `boundary_defining`, the
    field has no tentative_instance_key, or no bucket matches the key
    by equality).

    `boundary_consuming` and `neutral` fall through to authorities #2–4
    — callers pass `grouping_role != "boundary_defining"` and receive
    `None` here.
    """

    if grouping_role != "boundary_defining":
        return None
    if validated_field_tentative_instance_key is None:
        return None
    matches: list[int] = []
    for index, key in enumerate(tentative_keys):
        if key == validated_field_tentative_instance_key:
            matches.append(index)
    if len(matches) == 1:
        return matches[0]
    return None


def source_anchor_continuity_buckets(
    *,
    tentative_keys: tuple[InstanceGroupingKey, ...],
    validated_field_source_span: SourceSpan,
) -> tuple[int, ...]:
    """authority #2: source-anchor continuity.

    a bucket "overlaps" the field when at least one of the bucket's
    `group_anchors` shares a frame with
    `validated_field_source_span` and overlaps it by at least one
    byte (see `spans_overlap`).

    returns the tuple of tentative-bucket indices that overlap the
    field's `source_span`, in tentative-key order. the resolver
    treats a length-1 tuple as a unique winner at this authority; a
    length-0 or length-2+ tuple falls through to authority #3.
    """

    hits: list[int] = []
    for index, key in enumerate(tentative_keys):
        for anchor in key.group_anchors:
            if spans_overlap(anchor, validated_field_source_span):
                hits.append(index)
                break
    return tuple(hits)


def candidate_cooccurrence_buckets(
    *,
    tentative_keys: tuple[InstanceGroupingKey, ...],
    validated_field: ValidatedField,
    candidate_sets: tuple[CandidateSet, ...],
) -> tuple[int, ...]:
    """authority #3: candidate co-occurrence.

    phase-1 heuristic (narrow and deterministic):

    1. locate the `CandidateSet` keyed by
       `(CandidateSet.field_id == ValidatedField.proposed.field_id,
        CandidateSet.instance_hint == ValidatedField.proposed.tentative_instance_key)`.
       if no such set exists, return `()` — authority #3 is silent.
    2. project to the candidates referenced by
       `ValidatedField.proposed.candidate_id_refs`. if none of the
       referenced ids exist in the set, return `()`.
    3. for each tentative bucket, compute the minimum byte-gap
       between the bucket's `group_anchors` and the referenced
       candidates' `source_span`s under `_min_byte_gap`. a bucket is a
       candidate-cooccurrence contender when at least one anchor shares
       a frame with at least one referenced candidate span.
    4. rank contenders by min byte-gap ascending (exact overlap → gap
       0 → smaller gap → larger gap). return the indices at the
       minimum observed gap, in tentative-key order.

    if more than one bucket ties at the minimum gap, the tuple has
    length ≥ 2 and the caller falls through to authority #4.

    `DistanceMetric.params` is intentionally ignored in phase 1 — the
    gap heuristic is the only comparator this authority applies.
    """

    target_field_id = validated_field.proposed.field_id
    target_instance_hint = validated_field.proposed.tentative_instance_key
    target_ids = set(validated_field.proposed.candidate_id_refs)
    if not target_ids:
        return ()

    matching_set: CandidateSet | None = None
    for candidate_set in candidate_sets:
        if candidate_set.field_id != target_field_id:
            continue
        if candidate_set.instance_hint != target_instance_hint:
            continue
        matching_set = candidate_set
        break
    if matching_set is None:
        return ()

    referenced_spans: list[SourceSpan] = []
    for candidate in matching_set.candidates:
        if candidate.candidate_id in target_ids:
            referenced_spans.append(candidate.source_span)
    if not referenced_spans:
        return ()

    contenders: list[tuple[int, int]] = []
    for index, key in enumerate(tentative_keys):
        min_gap: int | None = None
        for anchor in key.group_anchors:
            for span in referenced_spans:
                gap = _min_byte_gap(anchor, span)
                if gap is None:
                    continue
                if min_gap is None or gap < min_gap:
                    min_gap = gap
        if min_gap is not None:
            contenders.append((index, min_gap))

    if not contenders:
        return ()

    best_gap = min(gap for _index, gap in contenders)
    winners = [index for index, gap in contenders if gap == best_gap]
    return tuple(winners)
