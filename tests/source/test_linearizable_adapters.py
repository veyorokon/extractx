"""focused tests for seam A phase 1 linearizable adapters.

proof targets (from `docs/tasks/seam-a-linearizable-document-adapters.md`,
"Focused proof"):

- `DocumentAdapter.adapt(raw_bytes, source_ref) -> DocumentView` exists on
  the protocol surface and each concrete adapter implements it.
- repeating adaptation of identical `(raw_bytes, source_ref)` yields
  byte-identical `DocumentView`.
- `document_id` is deterministic and carries no random / clock-derived state.
- for text and markdown adapters: all emitted spans carry
  `text_anchor_space="source_bytes"`; `anchor_map` is total over the
  UTF-8-aligned byte offsets of `normalized_text.encode("utf-8")`;
  source-byte spans are recoverable by inversion.
- multibyte UTF-8 text cases: aligned offsets are in-domain; misaligned
  offsets are out-of-domain and fail loudly.
- generic HTML adaptation is deterministic and source-byte recoverable.
- `metadata["parser"]` is absent when the adapter wraps no parser library
  (no fake bag invented).
"""

from __future__ import annotations

import pytest

from extractx.core.anchors import (
    SourceRef,
    SourceSpan,
    anchor_invert,
    anchor_lookup,
    anchor_validate_total,
)
from extractx.core.contracts import DocumentAdapter
from extractx.source import HtmlAdapter, MarkdownAdapter, TextAdapter


def _ref(source_id: str = "doc-1", content_hash: str = "sha256:abc") -> SourceRef:
    return SourceRef(source_id=source_id, content_hash=content_hash)


# ---------------------------------------------------------------------------
# protocol surface
# ---------------------------------------------------------------------------


class TestDocumentAdapterProtocolSurface:
    def test_adapt_is_a_declared_protocol_member(self) -> None:
        # `adapt` is an explicit method on the protocol surface; if this
        # reference disappears, seam A has lost its callable boundary.
        assert hasattr(DocumentAdapter, "adapt")

    def test_text_adapter_satisfies_protocol_structurally(self) -> None:
        adapter: DocumentAdapter = TextAdapter()
        view = adapter.adapt(b"hello", _ref())
        assert view.normalized_text == "hello"

    def test_markdown_adapter_satisfies_protocol_structurally(self) -> None:
        adapter: DocumentAdapter = MarkdownAdapter()
        view = adapter.adapt(b"# header\n\ntext", _ref())
        assert view.normalized_text == "# header\n\ntext"

    def test_html_adapter_satisfies_protocol_structurally(self) -> None:
        adapter: DocumentAdapter = HtmlAdapter()
        view = adapter.adapt(b"<p>hi</p>", _ref())
        assert view.normalized_text == "hi"


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize(
        ("adapter_factory", "raw"),
        [
            (TextAdapter, b"plain text content"),
            (MarkdownAdapter, b"# heading\n\nparagraph\n"),
            (HtmlAdapter, b"<p>hello <b>world</b></p>"),
        ],
    )
    def test_repeat_adapt_is_byte_identical(
        self,
        adapter_factory: type[TextAdapter | MarkdownAdapter | HtmlAdapter],
        raw: bytes,
    ) -> None:
        source_ref = _ref()
        first = adapter_factory().adapt(raw, source_ref)
        second = adapter_factory().adapt(raw, source_ref)
        # model_dump is the canonical comparison point: if any field is
        # non-deterministic (random id, timestamp) the dumps differ.
        assert first.model_dump() == second.model_dump()

    def test_document_id_is_derived_purely_from_source_ref(self) -> None:
        ref = _ref(source_id="alpha", content_hash="sha256:beta")
        view = TextAdapter().adapt(b"body", ref)
        # derivation rule is `source_id@content_hash`; any hidden clock
        # or random state would break this assertion.
        assert view.document_id == "alpha@sha256:beta"

    def test_different_source_refs_produce_different_document_ids(self) -> None:
        v1 = TextAdapter().adapt(b"x", _ref(source_id="a", content_hash="h"))
        v2 = TextAdapter().adapt(b"x", _ref(source_id="b", content_hash="h"))
        assert v1.document_id != v2.document_id


# ---------------------------------------------------------------------------
# linearizable subcontract invariants (text / markdown)
# ---------------------------------------------------------------------------


class TestLinearizableSpanSemantics:
    @pytest.mark.parametrize(
        "adapter_factory",
        [TextAdapter, MarkdownAdapter],
    )
    def test_all_spans_carry_source_bytes(
        self,
        adapter_factory: type[TextAdapter | MarkdownAdapter],
    ) -> None:
        view = adapter_factory().adapt(b"abc def", _ref())
        for _offset, span in view.anchor_map.entries:
            assert span.text_anchor_space == "source_bytes"

    @pytest.mark.parametrize(
        ("adapter_factory", "raw"),
        [
            (TextAdapter, b""),
            (TextAdapter, b"ascii only"),
            (TextAdapter, "héllo 日本".encode()),
            (MarkdownAdapter, b""),
            (MarkdownAdapter, b"# title\n\nbody\n"),
            (MarkdownAdapter, "# héllo 日本\n".encode()),
        ],
    )
    def test_anchor_map_is_total_over_normalized_bytes(
        self,
        adapter_factory: type[TextAdapter | MarkdownAdapter],
        raw: bytes,
    ) -> None:
        view = adapter_factory().adapt(raw, _ref())
        # shared invariant check; asserts coverage, alignment, and
        # monotonicity against the actual normalized_text.
        anchor_validate_total(view.anchor_map, view.normalized_text)

    def test_source_byte_span_is_recoverable_by_inversion(self) -> None:
        raw = b"lorem ipsum dolor sit amet"
        view = TextAdapter().adapt(raw, _ref())
        # invent a target span on the source-bytes side.
        span = SourceSpan(
            source_ref=view.source_ref,
            text_anchor_space="source_bytes",
            byte_start=6,
            byte_end=11,  # "ipsum"
        )
        norm_start, norm_end = anchor_invert(view.anchor_map, span)
        # identity adapter — the normalized byte range equals the source
        # byte range; the slice on `normalized_text.encode("utf-8")`
        # recovers the original bytes.
        assert view.normalized_text.encode("utf-8")[norm_start:norm_end] == b"ipsum"

    def test_multibyte_aligned_lookup_is_in_domain(self) -> None:
        raw = "héllo".encode()  # 6 bytes: 68 c3 a9 6c 6c 6f
        view = TextAdapter().adapt(raw, _ref())
        # 3 is the boundary after the 2-byte 'é' — aligned.
        span = anchor_lookup(view.anchor_map, 3, view.normalized_text)
        assert span.text_anchor_space == "source_bytes"

    def test_multibyte_misaligned_lookup_fails_loudly(self) -> None:
        raw = "héllo".encode()
        view = TextAdapter().adapt(raw, _ref())
        # 2 lands on the continuation byte of 'é' (0xa9).
        with pytest.raises(ValueError, match="not UTF-8 aligned"):
            anchor_lookup(view.anchor_map, 2, view.normalized_text)


# ---------------------------------------------------------------------------
# parser metadata passthrough discipline (ADR-0001 / principle 21)
# ---------------------------------------------------------------------------


class TestParserMetadataDiscipline:
    @pytest.mark.parametrize(
        ("adapter_factory", "raw"),
        [
            (TextAdapter, b"plain"),
            (MarkdownAdapter, b"# md"),
            (HtmlAdapter, b"<p>x</p>"),
        ],
    )
    def test_no_fake_parser_metadata_bag(
        self,
        adapter_factory: type[TextAdapter | MarkdownAdapter | HtmlAdapter],
        raw: bytes,
    ) -> None:
        # phase 1 adapters wrap no parser library, so `metadata["parser"]`
        # must be absent — not an empty dict, not `None`, absent. seeing a
        # key here would mean someone invented a synthetic passthrough bag.
        view = adapter_factory().adapt(raw, _ref())
        assert "parser" not in view.metadata


# ---------------------------------------------------------------------------
# decoding discipline
# ---------------------------------------------------------------------------


class TestUtf8DecodingDiscipline:
    def test_non_utf8_input_fails_loudly(self) -> None:
        # latin-1 'é' alone (0xe9) is not valid UTF-8 on its own.
        raw = b"r\xe9sum\xe9"
        with pytest.raises(UnicodeDecodeError):
            TextAdapter().adapt(raw, _ref())
        with pytest.raises(UnicodeDecodeError):
            MarkdownAdapter().adapt(raw, _ref())
        with pytest.raises(UnicodeDecodeError):
            HtmlAdapter().adapt(raw, _ref())


# ---------------------------------------------------------------------------
# HTML adapter — source-byte recoverability and determinism
# ---------------------------------------------------------------------------


class TestHtmlAdapter:
    def test_tags_are_stripped_from_normalized_text(self) -> None:
        view = HtmlAdapter().adapt(b"<html><body><p>hello</p></body></html>", _ref())
        assert view.normalized_text == "hello"

    def test_html_normalized_text_slice_matches_source_slice(self) -> None:
        raw = b"<p>Hello <b>world</b>!</p>"
        view = HtmlAdapter().adapt(raw, _ref())
        # each text segment should round-trip through the anchor map.
        for norm_start, span in view.anchor_map.entries:
            n = len(view.anchor_map.entries)
            idx = view.anchor_map.entries.index((norm_start, span))
            next_norm_start = (
                view.anchor_map.entries[idx + 1][0]
                if idx + 1 < n
                else len(view.normalized_text.encode("utf-8"))
            )
            norm_fragment = view.normalized_text.encode("utf-8")[norm_start:next_norm_start]
            source_fragment = raw[span.byte_start : span.byte_end]
            # for pure data events fragments are byte-identical.
            assert norm_fragment == source_fragment

    def test_html_entity_is_decoded_and_anchored(self) -> None:
        raw = b"<p>A&amp;B</p>"
        view = HtmlAdapter().adapt(raw, _ref())
        assert view.normalized_text == "A&B"
        # at least three segments: 'A', '&' (entity), 'B'.
        assert len(view.anchor_map.entries) == 3
        _, entity_span = view.anchor_map.entries[1]
        # the entity segment's source image spans `&amp;` (5 bytes).
        assert entity_span.byte_end - entity_span.byte_start == 5
        # and the source slice is literally the entity form.
        assert raw[entity_span.byte_start : entity_span.byte_end] == b"&amp;"

    def test_html_charref_is_decoded_and_anchored(self) -> None:
        raw = b"<p>x&#65;y</p>"  # &#65; decodes to 'A'
        view = HtmlAdapter().adapt(raw, _ref())
        assert view.normalized_text == "xAy"
        # middle entry is the charref.
        _, charref_span = view.anchor_map.entries[1]
        assert raw[charref_span.byte_start : charref_span.byte_end] == b"&#65;"

    def test_html_adapter_is_deterministic_on_multibyte(self) -> None:
        raw = "<p>héllo 日本</p>".encode()
        source_ref = _ref()
        a = HtmlAdapter().adapt(raw, source_ref)
        b = HtmlAdapter().adapt(raw, source_ref)
        assert a.model_dump() == b.model_dump()
        anchor_validate_total(a.anchor_map, a.normalized_text)

    def test_html_adapter_handles_empty_content(self) -> None:
        view = HtmlAdapter().adapt(b"<html><body></body></html>", _ref())
        assert view.normalized_text == ""
        assert view.anchor_map.entries == ()

    def test_html_anchor_map_segments_carry_source_bytes(self) -> None:
        view = HtmlAdapter().adapt(b"<p>hello <i>world</i></p>", _ref())
        for _offset, span in view.anchor_map.entries:
            assert span.text_anchor_space == "source_bytes"


# ---------------------------------------------------------------------------
# text-vs-markdown distinction (phase 1)
# ---------------------------------------------------------------------------


class TestTextMarkdownDistinction:
    """phase 1 ships no markdown parser; the two adapters produce the
    same `DocumentView` shape for the same input. the distinction is
    nominal — class identity, future wrapping surface. we assert parity
    explicitly so anyone reading the tests sees the current state rather
    than assuming a subtle difference.
    """

    def test_text_and_markdown_agree_on_phase_1_output(self) -> None:
        raw = b"# header\n\nbody text\n"
        source_ref = _ref()
        text_view = TextAdapter().adapt(raw, source_ref)
        markdown_view = MarkdownAdapter().adapt(raw, source_ref)
        assert text_view.model_dump() == markdown_view.model_dump()
