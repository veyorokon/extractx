"""source subsystem per docs/architecture.md §7 seam A.

houses `DocumentAdapter` implementations and the small adapter-side
construction helpers they share. canonical data types (`DocumentView`,
`AnchorMap`, `SourceSpan`, `SourceRef`) live in `extractx.core` and are
imported from there.
"""

from __future__ import annotations

from .adapters import HtmlAdapter, MarkdownAdapter, TextAdapter
from .document_view import (
    LinearSegment,
    build_linearizable_anchor_map,
    derive_document_id,
)

__all__ = [
    "HtmlAdapter",
    "LinearSegment",
    "MarkdownAdapter",
    "TextAdapter",
    "build_linearizable_anchor_map",
    "derive_document_id",
]
