"""tests for projecting byte-addressed spans onto Python strings."""

from __future__ import annotations

import pytest

from extractx.core import (
    SourceRef,
    SourceSpan,
    slice_utf8_byte_span,
    utf8_byte_span_to_char_range,
)


def _span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="doc", content_hash="sha256:test"),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def test_utf8_byte_span_to_char_range_handles_multibyte_prefix() -> None:
    text = "Préface ééé. Invoice total is $42.50."
    char_start = text.index("$42.50")
    byte_start = len(text[:char_start].encode("utf-8"))
    span = _span(byte_start, byte_start + len(b"$42.50"))

    assert byte_start != char_start
    assert utf8_byte_span_to_char_range(text, span) == (
        char_start,
        char_start + len("$42.50"),
    )
    assert slice_utf8_byte_span(text, span) == "$42.50"


def test_utf8_byte_span_to_char_range_rejects_misaligned_offsets() -> None:
    text = "éx"
    span = _span(1, 2)

    with pytest.raises(ValueError, match="byte_start .* not UTF-8 aligned"):
        utf8_byte_span_to_char_range(text, span)
