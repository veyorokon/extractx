"""contract tests for `SourceSpan`, `SourceRef`, and UTF-8 alignment helpers.

proof targets:
- `SourceSpan` requires `text_anchor_space` (no default; ADR-0006).
- `normalized_text` spans enforce UTF-8 byte alignment via the shared
  helper used by seam C validity and seam F layer 1 (ADR-0006).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from extractx.core.anchors import (
    SourceRef,
    SourceSpan,
    check_normalized_text_span,
    is_utf8_aligned,
)


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


class TestSourceSpanConstruction:
    def test_text_anchor_space_required(self) -> None:
        with pytest.raises(ValidationError):
            # intentionally omit text_anchor_space to prove it has no
            # default. type: ignore so pyright does not enforce the kw-arg
            # at type-check time — we want to hit the runtime check.
            SourceSpan(  # type: ignore[call-arg]
                source_ref=_ref(),
                byte_start=0,
                byte_end=1,
            )

    def test_source_bytes_space_constructs(self) -> None:
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="source_bytes",
            byte_start=0,
            byte_end=5,
        )
        assert span.text_anchor_space == "source_bytes"

    def test_normalized_text_space_constructs(self) -> None:
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="normalized_text",
            byte_start=0,
            byte_end=5,
        )
        assert span.text_anchor_space == "normalized_text"

    def test_byte_end_must_not_precede_byte_start(self) -> None:
        with pytest.raises(ValidationError):
            SourceSpan(
                source_ref=_ref(),
                text_anchor_space="normalized_text",
                byte_start=5,
                byte_end=2,
            )

    def test_negative_offsets_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SourceSpan(
                source_ref=_ref(),
                text_anchor_space="normalized_text",
                byte_start=-1,
                byte_end=0,
            )


class TestUtf8Alignment:
    # "héllo" is 6 UTF-8 bytes: 68 c3 a9 6c 6c 6f
    text = "héllo"

    def test_aligned_offsets(self) -> None:
        data = self.text.encode("utf-8")
        # 0 (start), 1 (after 'h'), 3 (after 'é's two bytes), 4, 5, 6 (end)
        for offset in (0, 1, 3, 4, 5, 6):
            assert is_utf8_aligned(data, offset), offset

    def test_misaligned_offset(self) -> None:
        data = self.text.encode("utf-8")
        # offset 2 lands on the continuation byte of 'é' (0xa9).
        assert not is_utf8_aligned(data, 2)

    def test_check_normalized_text_span_accepts_aligned(self) -> None:
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="normalized_text",
            byte_start=0,
            byte_end=3,  # 'h' (1) + 'é' (2 bytes) = 3
        )
        check_normalized_text_span(span, self.text)

    def test_check_normalized_text_span_rejects_misaligned(self) -> None:
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="normalized_text",
            byte_start=0,
            byte_end=2,  # splits 'é'
        )
        with pytest.raises(ValueError, match="not UTF-8 aligned"):
            check_normalized_text_span(span, self.text)

    def test_check_normalized_text_span_rejects_out_of_range(self) -> None:
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="normalized_text",
            byte_start=0,
            byte_end=999,
        )
        with pytest.raises(ValueError, match="exceeds"):
            check_normalized_text_span(span, self.text)

    def test_check_normalized_text_span_rejects_wrong_space(self) -> None:
        span = SourceSpan(
            source_ref=_ref(),
            text_anchor_space="source_bytes",
            byte_start=0,
            byte_end=1,
        )
        with pytest.raises(ValueError, match="must be 'normalized_text'"):
            check_normalized_text_span(span, self.text)
