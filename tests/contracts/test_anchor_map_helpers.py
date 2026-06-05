"""contract tests for `AnchorMap` lookup, inversion, and totality helpers.

proof targets:
- `anchor_lookup` returns a `SourceSpan` whose `text_anchor_space` matches
  the anchoring segment and whose byte range lives inside the segment's
  image for identity segments (point), and equals the segment's image for
  non-identity segments.
- `anchor_invert` reverses a `source_bytes` span back into normalized-text
  UTF-8 byte offsets when the span lies inside one segment; rejects cross-
  segment spans and non-`source_bytes` spans loudly.
- `anchor_validate_total` accepts a well-formed partition, rejects
  out-of-order entries, unaligned offsets, and mismatched domain ends.
- `AnchorMap` construction rejects non-increasing entry offsets at the
  pydantic-validator layer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from extractx.core.anchors import (
    AnchorMap,
    SourceRef,
    SourceSpan,
    anchor_invert,
    anchor_lookup,
    anchor_validate_total,
)


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


class TestAnchorMapConstruction:
    def test_empty_map_constructs(self) -> None:
        m = AnchorMap()
        assert m.entries == ()

    def test_strictly_increasing_offsets_accepted(self) -> None:
        m = AnchorMap(entries=((0, _span(0, 3)), (3, _span(5, 10))))
        assert len(m.entries) == 2

    def test_duplicate_offsets_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AnchorMap(entries=((0, _span(0, 3)), (0, _span(3, 6))))

    def test_decreasing_offsets_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AnchorMap(entries=((5, _span(0, 3)), (2, _span(3, 6))))

    def test_negative_offset_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AnchorMap(entries=((-1, _span(0, 3)),))


class TestAnchorValidateTotal:
    def test_empty_map_valid_for_empty_text(self) -> None:
        anchor_validate_total(AnchorMap(), "")

    def test_empty_map_invalid_for_non_empty_text(self) -> None:
        with pytest.raises(ValueError, match="empty anchor_map"):
            anchor_validate_total(AnchorMap(), "not empty")

    def test_identity_single_segment_valid(self) -> None:
        text = "abc"
        m = AnchorMap(entries=((0, _span(0, 3)),))
        anchor_validate_total(m, text)

    def test_multi_segment_with_gaps_valid(self) -> None:
        # two text segments separated in source (e.g., HTML with a tag
        # between them), contiguous on the normalized side.
        text = "ab"
        m = AnchorMap(entries=((0, _span(3, 4)), (1, _span(7, 8))))
        anchor_validate_total(m, text)

    def test_first_offset_not_zero_rejected(self) -> None:
        m = AnchorMap(entries=((1, _span(0, 3)),))
        with pytest.raises(ValueError, match="first entry offset must be 0"):
            anchor_validate_total(m, "abc")

    def test_offset_past_domain_end_rejected(self) -> None:
        # offset 100 is also UTF-8-unaligned against "abc" (3 bytes), so
        # the validator catches the alignment failure first — that is a
        # correct loud failure for an out-of-domain offset. either message
        # is a valid rejection.
        m = AnchorMap(entries=((0, _span(0, 3)), (100, _span(5, 8))))
        with pytest.raises(ValueError):
            anchor_validate_total(m, "abc")

    def test_misaligned_offset_rejected(self) -> None:
        # "héllo" is 6 UTF-8 bytes; offset 2 lands on a continuation byte.
        text = "héllo"
        m = AnchorMap(entries=((0, _span(0, 6)), (2, _span(6, 10))))
        with pytest.raises(ValueError, match="not UTF-8 aligned"):
            anchor_validate_total(m, text)


class TestAnchorLookup:
    def test_identity_segment_returns_point_span(self) -> None:
        text = "abcdef"
        m = AnchorMap(entries=((0, _span(0, 6)),))
        out = anchor_lookup(m, 3, text)
        assert out.byte_start == 3
        assert out.byte_end == 3

    def test_non_identity_segment_returns_full_image(self) -> None:
        # one normalized byte mapping to a 5-byte source fragment (e.g.,
        # the html entity `&amp;`).
        text = "&"
        m = AnchorMap(entries=((0, _span(10, 15)),))
        out = anchor_lookup(m, 0, text)
        assert out.byte_start == 10
        assert out.byte_end == 15

    def test_offset_outside_domain_rejected(self) -> None:
        text = "abc"
        m = AnchorMap(entries=((0, _span(0, 3)),))
        with pytest.raises(ValueError, match="outside"):
            anchor_lookup(m, 10, text)

    def test_negative_offset_rejected(self) -> None:
        text = "abc"
        m = AnchorMap(entries=((0, _span(0, 3)),))
        with pytest.raises(ValueError, match="outside"):
            anchor_lookup(m, -1, text)

    def test_misaligned_offset_rejected(self) -> None:
        text = "héllo"
        m = AnchorMap(entries=((0, _span(0, 6)),))
        with pytest.raises(ValueError, match="not UTF-8 aligned"):
            anchor_lookup(m, 2, text)

    def test_empty_map_rejected(self) -> None:
        # offset 0 is technically at the (empty) domain end, but an empty
        # AnchorMap has no segments to resolve against. loud failure is
        # the contract.
        m = AnchorMap()
        with pytest.raises(ValueError, match="empty AnchorMap"):
            anchor_lookup(m, 0, "")


class TestAnchorInvert:
    def test_invert_identity_segment(self) -> None:
        m = AnchorMap(entries=((0, _span(0, 10)),))
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="source_bytes",
            byte_start=3,
            byte_end=7,
        )
        assert anchor_invert(m, span) == (3, 7)

    def test_invert_with_source_offset(self) -> None:
        # segment covers source bytes [5, 10); normalized bytes [0, 5).
        m = AnchorMap(entries=((0, _span(5, 10)),))
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="source_bytes",
            byte_start=7,
            byte_end=9,
        )
        assert anchor_invert(m, span) == (2, 4)

    def test_invert_rejects_normalized_text_span(self) -> None:
        m = AnchorMap(entries=((0, _span(0, 3)),))
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="normalized_text",
            byte_start=0,
            byte_end=3,
        )
        with pytest.raises(ValueError, match="must be 'source_bytes'"):
            anchor_invert(m, span)

    def test_invert_cross_segment_rejected(self) -> None:
        # two segments (source bytes [0, 3) and [5, 8)); a span that
        # straddles the gap is not continuously reversible.
        m = AnchorMap(entries=((0, _span(0, 3)), (3, _span(5, 8))))
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="source_bytes",
            byte_start=2,
            byte_end=6,
        )
        with pytest.raises(ValueError, match="not covered by a single segment"):
            anchor_invert(m, span)

    def test_invert_rejects_span_outside_domain(self) -> None:
        m = AnchorMap(entries=((0, _span(0, 3)),))
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="source_bytes",
            byte_start=100,
            byte_end=200,
        )
        with pytest.raises(ValueError, match="not covered by a single segment"):
            anchor_invert(m, span)

    def test_invert_empty_map_rejected(self) -> None:
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="source_bytes",
            byte_start=0,
            byte_end=1,
        )
        with pytest.raises(ValueError, match="empty AnchorMap"):
            anchor_invert(AnchorMap(), span)
