"""candidate-context builders for seam C candidate grounding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from extractx.core.anchors import SourceRef, SourceSpan

__all__ = [
    "DEFAULT_CONTEXT_WINDOW_BYTES",
    "ByteWindowCandidateContextBuilder",
    "CandidateContextBuilder",
]


DEFAULT_CONTEXT_WINDOW_BYTES = 160
"""default bounded normalized-text context around a candidate match."""


class CandidateContextBuilder(Protocol):
    """build bounded candidate context from normalized document bytes."""

    def build(
        self,
        *,
        normalized_bytes: bytes,
        match_start: int,
        match_end: int,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ByteWindowCandidateContextBuilder:
    """byte-window context around the matched normalized-text range."""

    window_bytes: int = DEFAULT_CONTEXT_WINDOW_BYTES

    def __post_init__(self) -> None:
        if isinstance(self.window_bytes, bool) or self.window_bytes < 0:
            raise ValueError(
                "ByteWindowCandidateContextBuilder.window_bytes must be a non-negative int",
            )

    def build(
        self,
        *,
        normalized_bytes: bytes,
        match_start: int,
        match_end: int,
    ) -> str:
        start = max(0, match_start - self.window_bytes)
        end = min(len(normalized_bytes), match_end + self.window_bytes)
        return normalized_bytes[start:end].decode("utf-8", errors="replace")

    def span(
        self,
        *,
        normalized_bytes: bytes,
        match_start: int,
        match_end: int,
        source_ref: SourceRef,
    ) -> SourceSpan:
        start = max(0, match_start - self.window_bytes)
        end = min(len(normalized_bytes), match_end + self.window_bytes)
        return SourceSpan(
            source_ref=source_ref,
            text_anchor_space="normalized_text",
            byte_start=start,
            byte_end=end,
        )


def normalized_match_span(
    *,
    source_ref: SourceRef,
    match_start: int,
    match_end: int,
) -> SourceSpan:
    return SourceSpan(
        source_ref=source_ref,
        text_anchor_space="normalized_text",
        byte_start=match_start,
        byte_end=match_end,
    )
