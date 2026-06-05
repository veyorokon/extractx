"""generic markdown `DocumentAdapter` per docs/architecture.md §7 seam A.

linearizable subcontract (ADR-0006): spans carry `text_anchor_space=
"source_bytes"`. in phase 1 the markdown adapter wraps no parser library,
so the honest normalization policy is identity over UTF-8 decoding — the
same shape as the text adapter. the adapter is named and wired separately
because:

1. callers dispatch by content type at their own layer; extractx never
   sniffs MIME. separating the classes keeps that dispatch explicit.
2. a later thread may wrap a real markdown parser (e.g., `markdown-it-py`
   with source maps per `docs/research/default-document-adapter.md` §7).
   when that lands, this adapter grows: it attaches the parser's native
   token stream under `metadata["parser"]` unchanged and may emit finer
   per-token segments inside `anchor_map`. that future growth is out of
   scope here; the brief is explicit that phase 1 stays close to raw bytes
   and must not invent fake parser metadata.

so today: `adapt` is byte-for-byte identical to `TextAdapter.adapt`.
the distinction is nominal (and future-proofing), not behavioral. proof
tests note this explicitly rather than hiding it.
"""

from __future__ import annotations

from extractx.core.anchors import (
    AnchorMap,
    SourceRef,
    anchor_validate_total,
)
from extractx.core.objects import DocumentView
from extractx.source.document_view import (
    LinearSegment,
    build_linearizable_anchor_map,
    derive_document_id,
)

__all__ = ["MarkdownAdapter"]


class MarkdownAdapter:
    """markdown `DocumentAdapter` implementation for seam A phase 1.

    satisfies the linearizable subcontract. stateless and safe to share.
    phase 1 ships no parser-library integration; `metadata["parser"]` is
    intentionally absent.
    """

    def adapt(self, raw_bytes: bytes, source_ref: SourceRef) -> DocumentView:
        """decode `raw_bytes` as UTF-8 and return an identity `DocumentView`.

        raises `UnicodeDecodeError` loudly on non-UTF-8 input; phase 1
        does not attempt heuristic fallbacks.
        """

        normalized_text = raw_bytes.decode("utf-8", errors="strict")

        if len(raw_bytes) == 0:
            anchor_map = AnchorMap()
        else:
            segment = LinearSegment(
                normalized_byte_start=0,
                source_byte_start=0,
                normalized_length=len(raw_bytes),
                source_length=len(raw_bytes),
            )
            anchor_map = build_linearizable_anchor_map((segment,), source_ref)

        anchor_validate_total(anchor_map, normalized_text)

        return DocumentView(
            document_id=derive_document_id(source_ref),
            normalized_text=normalized_text,
            anchor_map=anchor_map,
            source_ref=source_ref,
            metadata={},
        )
