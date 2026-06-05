"""adapter-side `DocumentView` construction helpers for seam A.

the canonical `DocumentView` object lives in `extractx.core.objects`. this
module holds only the small, seam-A-local helpers the linearizable adapters
share when wiring raw bytes into a deterministic `DocumentView` (anchor-map
assembly from segments, deterministic `document_id` derivation).

these helpers are intentionally narrow: one anchor-map builder and one id
deriver. richer logic (fuzzy lookup, multi-source reconciliation, visual
provenance) lives either in `core/anchors.py` (shared invariants) or in
per-adapter modules. nothing here decides between subcontracts or performs
format sniffing.
"""

from __future__ import annotations

from dataclasses import dataclass

from extractx.core.anchors import AnchorMap, SourceRef, SourceSpan, TextAnchorSpace

__all__ = [
    "LinearSegment",
    "build_linearizable_anchor_map",
    "derive_document_id",
]


@dataclass(frozen=True, slots=True)
class LinearSegment:
    """one contiguous source-byte segment of a linearizable `DocumentView`.

    carries the normalized-text byte offset at which the segment starts,
    the source-byte range the segment occupies, the segment's length on
    the normalized-text side, and its length on the source-bytes side.

    for identity adapters (text / markdown) the two lengths are equal —
    the segment is a pure copy. for adapters that expand or contract
    content inside a segment (e.g., an HTML entity `&amp;` whose 5 source
    bytes collapse to 1 normalized byte) the two lengths differ. in that
    case the anchor-map entry uses the normalized length for domain
    coverage and the source length for the span's byte range, so inversion
    returns the whole source fragment when the caller asks about any
    offset inside the segment.
    """

    normalized_byte_start: int
    source_byte_start: int
    normalized_length: int
    source_length: int


def derive_document_id(source_ref: SourceRef) -> str:
    """derive a deterministic `document_id` from `SourceRef`.

    the id is `f"{source_id}@{content_hash}"`, which is pure over the two
    fields `SourceRef` already carries and contains no wall-clock or random
    state. adapters that consume the same `(raw_bytes, source_ref)` produce
    the same id across runs — the invariant seam A phase 1 depends on for
    determinism proofs.
    """

    return f"{source_ref.source_id}@{source_ref.content_hash}"


def build_linearizable_anchor_map(
    segments: tuple[LinearSegment, ...],
    source_ref: SourceRef,
) -> AnchorMap:
    """assemble an `AnchorMap` from an ordered tuple of linearizable
    `LinearSegment`s.

    every segment becomes one `(normalized_byte_start, SourceSpan)` entry
    whose span carries `text_anchor_space="source_bytes"` and the source-byte
    range of the segment. consecutive segments must be in strictly
    increasing `normalized_byte_start` order; callers that collected
    segments out of order must sort them before calling this helper.

    the helper itself does not validate totality against a specific
    `normalized_text` — the caller invokes `anchor_validate_total` once
    the `DocumentView` is assembled. this split keeps the builder pure and
    avoids a `normalized_text` round-trip inside each adapter.
    """

    entries: list[tuple[int, SourceSpan]] = []
    for seg in segments:
        if seg.normalized_length < 0 or seg.source_length < 0:
            raise ValueError(
                "build_linearizable_anchor_map: segment lengths must be >= 0, "
                f"got normalized_length={seg.normalized_length}, "
                f"source_length={seg.source_length}",
            )
        text_anchor_space: TextAnchorSpace = "source_bytes"
        span = SourceSpan(
            source_ref=source_ref,
            text_anchor_space=text_anchor_space,
            byte_start=seg.source_byte_start,
            byte_end=seg.source_byte_start + seg.source_length,
        )
        entries.append((seg.normalized_byte_start, span))
    return AnchorMap(entries=tuple(entries))
