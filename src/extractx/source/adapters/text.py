"""generic plain-text `DocumentAdapter` per docs/architecture.md §7 seam A.

linearizable subcontract (ADR-0006): spans carry `text_anchor_space=
"source_bytes"` and `byte_*` address the raw source bytes. normalization
policy is **identity over UTF-8 decoding**: `normalized_text = raw_bytes
.decode("utf-8")`; there is no whitespace collapsing, line-ending rewrite,
or unicode normalization. non-UTF-8 input fails loudly.

`metadata["parser"]` is intentionally absent — this adapter does not wrap
an external parser library, so there is no native parser metadata to
pass through (principle 21 / ADR-0001 forbid inventing a synthetic bag).
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

__all__ = ["TextAdapter"]


class TextAdapter:
    """plain-text `DocumentAdapter` implementation for seam A phase 1.

    satisfies the linearizable subcontract. the adapter is stateless and
    safe to share; `adapt` is pure over `(raw_bytes, source_ref)`.
    """

    def adapt(self, raw_bytes: bytes, source_ref: SourceRef) -> DocumentView:
        """decode `raw_bytes` as UTF-8 and return an identity `DocumentView`.

        raises `UnicodeDecodeError` loudly on non-UTF-8 input; phase 1 does
        not attempt heuristic fallbacks (the current `SourceRef` contract
        does not carry encoding metadata, so any fallback would be an
        invented policy).
        """

        # strict=True is the python default for `bytes.decode("utf-8")`;
        # we name it explicitly to make the loud-failure contract visible
        # to readers.
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

        # totality check on the constructed view — fails loudly if the
        # shared builder or this adapter's segment arithmetic drifts.
        anchor_validate_total(anchor_map, normalized_text)

        return DocumentView(
            document_id=derive_document_id(source_ref),
            normalized_text=normalized_text,
            anchor_map=anchor_map,
            source_ref=source_ref,
            metadata={},
        )
