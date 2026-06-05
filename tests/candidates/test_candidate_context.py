"""tests for candidate-context builder seam."""

from __future__ import annotations

import pytest

from extractx.candidates.context import (
    ByteWindowCandidateContextBuilder,
    normalized_match_span,
)
from extractx.core import SourceRef


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def test_byte_window_context_builder_returns_bounded_context() -> None:
    builder = ByteWindowCandidateContextBuilder(window_bytes=4)

    context = builder.build(
        normalized_bytes=b"aaa 555-1234 bbb",
        match_start=4,
        match_end=12,
    )

    assert context == "aaa 555-1234 bbb"


def test_byte_window_context_builder_returns_matching_normalized_span() -> None:
    builder = ByteWindowCandidateContextBuilder(window_bytes=4)

    span = builder.span(
        normalized_bytes=b"aaa 555-1234 bbb",
        match_start=4,
        match_end=12,
        source_ref=_ref(),
    )

    assert span.source_ref == _ref()
    assert span.text_anchor_space == "normalized_text"
    assert span.byte_start == 0
    assert span.byte_end == 16


def test_normalized_match_span_points_at_primary_match() -> None:
    span = normalized_match_span(
        source_ref=_ref(),
        match_start=4,
        match_end=12,
    )

    assert span.source_ref == _ref()
    assert span.text_anchor_space == "normalized_text"
    assert span.byte_start == 4
    assert span.byte_end == 12


def test_byte_window_context_builder_rejects_negative_window() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ByteWindowCandidateContextBuilder(window_bytes=-1)
