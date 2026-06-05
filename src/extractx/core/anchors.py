"""source-anchor types per docs/architecture.md §9.

houses `AnchorMap`, `SourceSpan`, `SourceRef`, `PageRef`, `BoundingRegion`.

this module encodes the purely structural shape and local invariants of the
anchor types. adapter-specific behavior (encoding detection, subcontract
selection, normalization policy) lives in `source/adapters/**` and is out of
scope here.

notable invariants encoded here:

- `SourceSpan.text_anchor_space` is required at construction (no default);
  see ADR-0006 ("Format-Silent-Span-Semantics" anti-pattern).
- `byte_start` / `byte_end` are half-open byte offsets with `0 <= byte_start
  <= byte_end`.
- for `text_anchor_space="normalized_text"`, the offsets are UTF-8 byte
  offsets into `DocumentView.normalized_text.encode("utf-8")` and must be
  UTF-8 aligned (code-point boundaries). the actual upper-bound check
  against a specific `DocumentView` is seam-A's job; here we only enforce
  what we can without the document.
- for `text_anchor_space="source_bytes"`, offsets are raw byte offsets
  into `source_ref.content_hash`'s bytes and alignment is the adapter's
  responsibility — we do not assume UTF-8.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TextAnchorSpace = Literal["source_bytes", "normalized_text"]


class SourceRef(BaseModel):
    """identifies the original source artifact.

    see docs/architecture.md §9. the content hash identifies the raw source;
    adapters do not commit normalized forms under `SourceRef` (ADR-0006).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    content_hash: str


class PageRef(BaseModel):
    """page locator for paginated-visual documents.

    orthogonal to `text_anchor_space`; may be attached to any `SourceSpan`
    where visual provenance is meaningful (ADR-0006).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_number: int
    page_size: tuple[float, float] | None = None


class BoundingRegion(BaseModel):
    """visual bounding region inside a page.

    `polygon` is a tuple of `(x, y)` pairs in normalized coordinates in
    `[0, 1]`. orthogonal to `text_anchor_space`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_number: int
    polygon: tuple[tuple[float, float], ...]


class SourceSpan(BaseModel):
    """byte-addressable provenance anchor; see docs/architecture.md §9 and ADR-0006.

    `text_anchor_space` is the discriminator required at construction.
    `byte_start` / `byte_end` are half-open byte offsets in the coordinate
    space declared by `text_anchor_space`. visual locators (`page_ref`,
    `bounding_region`) are orthogonal — attachable to any `SourceSpan`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_ref: SourceRef
    text_anchor_space: TextAnchorSpace
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=0)
    page_ref: PageRef | None = None
    bounding_region: BoundingRegion | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> SourceSpan:
        if self.byte_end < self.byte_start:
            raise ValueError(
                f"SourceSpan: byte_end ({self.byte_end}) must be >= byte_start ({self.byte_start})",
            )
        return self


def is_utf8_aligned(data: bytes, offset: int) -> bool:
    """return True iff `offset` is a UTF-8 code-point boundary of `data`.

    a byte offset is UTF-8-aligned when the byte at that position is either
    the end of the buffer or not a UTF-8 continuation byte (`0b10xxxxxx`).
    offsets outside `[0, len(data)]` are never aligned.

    this helper lets downstream seams (seam C invariant check, seam F layer
    1 validator) verify `normalized_text`-space `SourceSpan`s against the
    `DocumentView.normalized_text.encode("utf-8")` buffer without inventing
    a custom alignment rule per call site (ADR-0006).
    """

    if offset < 0 or offset > len(data):
        return False
    if offset == 0 or offset == len(data):
        return True
    return (data[offset] & 0b1100_0000) != 0b1000_0000


def check_normalized_text_span(span: SourceSpan, normalized_text: str) -> None:
    """assert that `span` is a well-formed `normalized_text` span against
    `normalized_text`.

    raises `ValueError` if `span.text_anchor_space != "normalized_text"`,
    if either offset lies outside the UTF-8 byte range of `normalized_text`,
    or if either offset is not UTF-8-aligned.

    this function is the shared, core-owned utility for what seam F layer 1
    and seam C's validity invariant require for `normalized_text` spans
    (ADR-0006). it does not depend on a `DocumentView` being constructed.
    """

    if span.text_anchor_space != "normalized_text":
        raise ValueError(
            "check_normalized_text_span: span.text_anchor_space must be "
            f"'normalized_text', got {span.text_anchor_space!r}",
        )
    data = normalized_text.encode("utf-8")
    if span.byte_end > len(data):
        raise ValueError(
            f"check_normalized_text_span: byte_end ({span.byte_end}) exceeds "
            f"len(normalized_text.encode('utf-8')) ({len(data)})",
        )
    if not is_utf8_aligned(data, span.byte_start):
        raise ValueError(
            f"check_normalized_text_span: byte_start ({span.byte_start}) is not UTF-8 aligned",
        )
    if not is_utf8_aligned(data, span.byte_end):
        raise ValueError(
            f"check_normalized_text_span: byte_end ({span.byte_end}) is not UTF-8 aligned",
        )


def utf8_byte_span_to_char_range(text: str, span: SourceSpan) -> tuple[int, int]:
    """convert a `SourceSpan`'s UTF-8 byte offsets to Python string offsets.

    `SourceSpan.byte_start` / `byte_end` are always byte offsets. UI code
    that highlights a Python `str` needs code-point offsets instead. This
    helper is valid when `span.byte_*` address `text.encode("utf-8")`:

    - for `text_anchor_space="normalized_text"`, pass the corresponding
      `DocumentView.normalized_text`;
    - for `text_anchor_space="source_bytes"`, pass the UTF-8 decoded source
      text whose raw bytes are identified by `span.source_ref`.

    The helper fails loudly on out-of-range or UTF-8-misaligned offsets rather
    than silently returning a wrong character range.
    """

    data = text.encode("utf-8")
    if span.byte_end > len(data):
        raise ValueError(
            f"utf8_byte_span_to_char_range: byte_end ({span.byte_end}) exceeds "
            f"len(text.encode('utf-8')) ({len(data)})",
        )
    if not is_utf8_aligned(data, span.byte_start):
        raise ValueError(
            "utf8_byte_span_to_char_range: byte_start "
            f"({span.byte_start}) is not UTF-8 aligned",
        )
    if not is_utf8_aligned(data, span.byte_end):
        raise ValueError(
            "utf8_byte_span_to_char_range: byte_end "
            f"({span.byte_end}) is not UTF-8 aligned",
        )
    start = len(data[: span.byte_start].decode("utf-8"))
    end = len(data[: span.byte_end].decode("utf-8"))
    return (start, end)


def slice_utf8_byte_span(text: str, span: SourceSpan) -> str:
    """return the Python `str` slice addressed by a UTF-8 byte span.

    See `utf8_byte_span_to_char_range(...)` for the coordinate-space
    precondition. This is a convenience projection for UI/highlighting code;
    it does not change the canonical byte-addressed `SourceSpan` contract.
    """

    start, end = utf8_byte_span_to_char_range(text, span)
    return text[start:end]


class AnchorMap(BaseModel):
    """canonical anchor map; a total function from UTF-8 byte offsets into
    `DocumentView.normalized_text.encode("utf-8")` to `SourceSpan`s.

    see docs/architecture.md §7 seam A and ADR-0006.

    representation: a sorted, non-overlapping partition of the normalized-text
    UTF-8 byte domain into segments. each entry `(normalized_byte_offset,
    span)` anchors the start of a segment whose normalized-text byte range is
    `[normalized_byte_offset, next_entry.normalized_byte_offset)` (or
    `[..., domain_end)` for the last entry). the segment's image is the
    `SourceSpan` carried on the entry.

    segment images may be **identity** (normalized length equals source-bytes
    length, i.e., a pure copy — the common case for plain text and markdown
    adapters) or **compressed** (a multi-source-byte fragment collapses into
    fewer normalized bytes — e.g., an HTML entity `&amp;` whose 5 source
    bytes become 1 normalized byte). `anchor_lookup` returns a point span
    inside identity segments and the full image span for compressed
    segments; `anchor_invert` requires a source-bytes span to lie entirely
    inside one segment's image to be reversible.

    the minimal typed lookup / inversion api the seam-A adapters and
    downstream seams (C invariant check, F layer 1 validation) depend on
    lives as module-level functions (`anchor_lookup`, `anchor_invert`,
    `anchor_validate_total`). this keeps `AnchorMap` itself a pure data
    container rather than a mutable index object.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # every entry is a (normalized_text_byte_offset, span) pair. offsets are
    # UTF-8-aligned byte offsets into `normalized_text.encode("utf-8")` and
    # must be strictly increasing. the span on the entry describes the
    # source-bytes (or normalized_text) image of the segment that starts at
    # this offset and runs until the next entry's offset (or the domain end
    # for the last entry).
    entries: tuple[tuple[int, SourceSpan], ...] = ()

    @model_validator(mode="after")
    def _check_entry_ordering(self) -> AnchorMap:
        last_offset: int | None = None
        for offset, span in self.entries:
            if offset < 0:
                raise ValueError(
                    f"AnchorMap: entry offset ({offset}) must be >= 0",
                )
            if last_offset is not None and offset <= last_offset:
                raise ValueError(
                    f"AnchorMap: entry offsets must be strictly increasing; "
                    f"got {offset} after {last_offset}",
                )
            if span.byte_end < span.byte_start:
                # redundant with SourceSpan's own validator but kept here as
                # an early, structural signal for the anchor-map shape.
                raise ValueError(
                    f"AnchorMap: entry span has byte_end ({span.byte_end}) "
                    f"< byte_start ({span.byte_start})",
                )
            last_offset = offset
        return self


def anchor_validate_total(anchor_map: AnchorMap, normalized_text: str) -> None:
    """assert `anchor_map` covers the UTF-8 byte domain of `normalized_text`.

    the domain is `[0, len(normalized_text.encode("utf-8")))`. "cover" here
    means every UTF-8-aligned offset in that range falls inside one of the
    map's segments, where segment `i` runs `[entries[i].offset,
    entries[i + 1].offset)` (or `[..., domain_end)` for the last segment).

    raises `ValueError` when:
    - the first entry does not start at offset 0 (for a non-empty domain);
    - any entry offset is not UTF-8-aligned against `normalized_text`'s UTF-8
      encoding;
    - entry offsets are not strictly increasing (already enforced at
      `AnchorMap` construction, but re-checked defensively);
    - the last entry offset exceeds the domain end (the last segment would
      start past the end of normalized_text).

    segment "image" lengths are not constrained to equal the segment's
    normalized-text length: linearizable adapters may compress a
    multi-source-byte fragment (e.g., an HTML entity `&amp;`) into one
    normalized byte ("many-to-one" image, ADR-0006). the source-bytes
    span on an entry describes the image of the whole normalized segment;
    `anchor_lookup` returns a point inside that span when queried, and
    `anchor_invert` can reverse any source-bytes sub-span that lies
    entirely inside a single segment's image.

    for an empty `normalized_text` (zero-byte UTF-8 encoding), the only
    total map is the empty `AnchorMap`.

    this helper is the shared invariant check the seam-A adapters and the
    seam-F layer-1 validator call on a completed `DocumentView` (ADR-0006).
    """

    data = normalized_text.encode("utf-8")
    domain_end = len(data)

    if not anchor_map.entries:
        if domain_end != 0:
            raise ValueError(
                "anchor_validate_total: empty anchor_map does not cover a "
                f"non-empty normalized_text (UTF-8 length {domain_end})",
            )
        return

    first_offset, _ = anchor_map.entries[0]
    if first_offset != 0:
        raise ValueError(
            f"anchor_validate_total: first entry offset must be 0, got {first_offset}",
        )

    last_seen: int | None = None
    for offset, _span in anchor_map.entries:
        if not is_utf8_aligned(data, offset):
            raise ValueError(
                f"anchor_validate_total: entry offset {offset} is not UTF-8 aligned",
            )
        if last_seen is not None and offset <= last_seen:
            raise ValueError(
                f"anchor_validate_total: entry offsets must be strictly "
                f"increasing; got {offset} after {last_seen}",
            )
        last_seen = offset

    last_offset, _ = anchor_map.entries[-1]
    if last_offset > domain_end:
        raise ValueError(
            f"anchor_validate_total: last entry offset {last_offset} exceeds "
            f"domain end {domain_end}",
        )


def _find_segment_index(anchor_map: AnchorMap, normalized_offset: int) -> int:
    """return the index of the segment that contains `normalized_offset`.

    raises `ValueError` if `anchor_map` is empty or the offset is outside
    the covered domain. bisect-left over the entry offsets and step back one
    segment when the hit lands on a segment start.
    """

    if not anchor_map.entries:
        raise ValueError(
            "anchor_lookup: cannot look up an offset in an empty AnchorMap",
        )
    if normalized_offset < 0:
        raise ValueError(
            f"anchor_lookup: offset ({normalized_offset}) must be >= 0",
        )
    # linear scan keeps the implementation honest for small adapters;
    # downstream callers that need faster lookup can wrap this in their own
    # index. the anchor-map contract does not require sub-linear lookup.
    n = len(anchor_map.entries)
    for i in range(n):
        start, _ = anchor_map.entries[i]
        end = anchor_map.entries[i + 1][0] if i + 1 < n else None
        if normalized_offset < start:
            raise ValueError(
                f"anchor_lookup: offset ({normalized_offset}) is before "
                f"the first segment start ({start})",
            )
        if end is None or normalized_offset < end:
            return i
    # fell through the loop — offset is beyond the last segment's domain
    # (and we are on the last segment). `normalized_offset == domain_end`
    # is treated as inside the last segment by the `end is None` branch
    # above; anything past that is invalid.
    raise ValueError(
        f"anchor_lookup: offset ({normalized_offset}) is outside the anchor-map domain",
    )


def anchor_lookup(
    anchor_map: AnchorMap,
    normalized_offset: int,
    normalized_text: str,
) -> SourceSpan:
    """return the canonical `SourceSpan` for a normalized-text UTF-8 byte offset.

    `normalized_offset` must be a UTF-8-aligned byte offset into
    `normalized_text.encode("utf-8")` and within the domain (including the
    domain end). misaligned or out-of-range offsets raise `ValueError`.

    for an **identity** segment (the segment's normalized-text length equals
    the source-bytes image length — the common case for plain text and
    markdown adapters) the returned span is a zero-length point inside the
    image, linearly placed at the same offset within the segment.

    for a **non-identity** segment (normalized length differs from image
    length — e.g., an HTML entity `&amp;` whose 5 source bytes collapse
    to 1 normalized byte) the returned span is the full segment image;
    per-offset point interpolation would invent a mapping the adapter did
    not declare.

    visual locators (`page_ref`, `bounding_region`) and the segment's
    `text_anchor_space` and `source_ref` are carried through unchanged.
    the helper is deterministic and pure; there is no fuzzy matching.
    """

    data = normalized_text.encode("utf-8")
    if normalized_offset < 0 or normalized_offset > len(data):
        raise ValueError(
            f"anchor_lookup: offset ({normalized_offset}) is outside "
            f"the normalized-text UTF-8 domain [0, {len(data)}]",
        )
    if not is_utf8_aligned(data, normalized_offset):
        raise ValueError(
            f"anchor_lookup: offset ({normalized_offset}) is not UTF-8 aligned",
        )
    index = _find_segment_index(anchor_map, normalized_offset)
    segment_start, anchor_span = anchor_map.entries[index]
    n = len(anchor_map.entries)
    segment_end = anchor_map.entries[index + 1][0] if index + 1 < n else len(data)
    normalized_length = segment_end - segment_start
    span_length = anchor_span.byte_end - anchor_span.byte_start
    if normalized_length == span_length:
        delta = normalized_offset - segment_start
        point = anchor_span.byte_start + delta
        return SourceSpan(
            source_ref=anchor_span.source_ref,
            text_anchor_space=anchor_span.text_anchor_space,
            byte_start=point,
            byte_end=point,
            page_ref=anchor_span.page_ref,
            bounding_region=anchor_span.bounding_region,
        )
    # non-identity segment: return the full segment image.
    return anchor_span


def anchor_invert(
    anchor_map: AnchorMap,
    span: SourceSpan,
) -> tuple[int, int]:
    """invert a `text_anchor_space="source_bytes"` span back into the
    normalized-text UTF-8 byte offsets whose image it is.

    returns `(normalized_start, normalized_end)`, a half-open UTF-8 byte
    range into `normalized_text.encode("utf-8")`. raises `ValueError` if
    `span.text_anchor_space != "source_bytes"`, if no segment of the map
    covers the span, or if the span straddles a segment boundary (there is
    no continuous preimage in that case — the caller must split the span
    before inverting).

    the helper is the minimal inversion surface seam A produces and seam F
    layer 1 consumes for `source_bytes` spans per ADR-0006 ("the span must
    be recoverable from `anchor_map` by inversion over one or more
    normalized-text byte offsets").
    """

    if span.text_anchor_space != "source_bytes":
        raise ValueError(
            "anchor_invert: span.text_anchor_space must be 'source_bytes', "
            f"got {span.text_anchor_space!r}",
        )
    if not anchor_map.entries:
        raise ValueError(
            "anchor_invert: cannot invert against an empty AnchorMap",
        )
    n = len(anchor_map.entries)
    for i in range(n):
        segment_start, anchor_span = anchor_map.entries[i]
        if anchor_span.text_anchor_space != "source_bytes":
            raise ValueError(
                "anchor_invert: anchor_map carries non-source_bytes spans; "
                "inversion is only defined against a source_bytes anchor map",
            )
        if anchor_span.source_ref != span.source_ref:
            continue
        segment_end_source = anchor_span.byte_end
        segment_start_source = anchor_span.byte_start
        if span.byte_start >= segment_start_source and span.byte_end <= segment_end_source:
            delta_start = span.byte_start - segment_start_source
            delta_end = span.byte_end - segment_start_source
            return (segment_start + delta_start, segment_start + delta_end)
    raise ValueError(
        f"anchor_invert: span {span.byte_start}..{span.byte_end} is not "
        "covered by a single segment of the anchor map",
    )


__all__ = [
    "AnchorMap",
    "BoundingRegion",
    "PageRef",
    "SourceRef",
    "SourceSpan",
    "TextAnchorSpace",
    "anchor_invert",
    "anchor_lookup",
    "anchor_validate_total",
    "check_normalized_text_span",
    "is_utf8_aligned",
    "slice_utf8_byte_span",
    "utf8_byte_span_to_char_range",
]
