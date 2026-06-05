"""generic html `DocumentAdapter` per docs/architecture.md §7 seam A.

linearizable subcontract (ADR-0006): spans carry `text_anchor_space=
"source_bytes"`. the adapter uses stdlib `html.parser.HTMLParser` with
`convert_charrefs=False` so character and entity references become
separate, position-tagged events — that lets us map every chunk of
normalized text back to its exact source-byte range honestly.

normalization policy: concatenate, in document order, the decoded text
of every `handle_data`, `handle_charref`, and `handle_entityref` event.
nothing else (tag attributes, comments, doctype, `<script>`/`<style>`
contents) contributes to `normalized_text`. there is no whitespace
collapsing, no main-content heuristic, no main-article extraction. this
is a **generic** html adapter, not a readability adapter — the brief
forbids the latter. tag structure is observed solely to know what is
text and what is not; it is not reintroduced as tokens in the output.

`metadata["parser"]` is intentionally absent: stdlib `html.parser` is
not a third-party parser library whose native metadata we are wrapping,
and principle 21 / ADR-0001 forbid inventing a synthetic parser bag.
the later, opt-in html adapter that wraps `lxml` or `markdown-it-py`
(see `docs/research/default-document-adapter.md` §7) may attach native
parser metadata; that is out of scope for phase 1.
"""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from typing import Literal

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

__all__ = ["HtmlAdapter"]


EventKind = Literal["data", "charref", "entityref"]


class _TextEvent:
    """one text-producing event collected by `_PositionedParser`.

    `text` is the decoded unicode string the event contributes to
    `normalized_text` (for `data` events this is the event's payload; for
    `charref` / `entityref` events it is the reference's decoded value).

    `char_start` is the event's start position as a 0-based code-point
    offset into the decoded source string; `char_length` is filled in
    during post-processing from the next event's `char_start` (or the
    tail of the decoded source for the last event). both are in **code
    points**; the adapter converts them to UTF-8 byte offsets once per
    adaptation.

    the class is intentionally a plain mutable object (not a pydantic
    model) because it exists only during `adapt` and is discarded before
    the `DocumentView` is returned.
    """

    __slots__ = ("char_length", "char_start", "kind", "text")

    def __init__(self, kind: EventKind, char_start: int, text: str) -> None:
        self.kind: EventKind = kind
        self.char_start: int = char_start
        self.text: str = text
        self.char_length: int = 0  # filled during post-processing


class _PositionedParser(HTMLParser):
    """collect text events from an HTML stream with source char positions.

    `convert_charrefs=False` so charrefs and entityrefs arrive as their own
    events — without that, `handle_data` receives already-decoded text whose
    length no longer matches the source fragment length and source-byte
    recoverability becomes fuzzy. the parser is otherwise stock stdlib.
    """

    def __init__(self, line_char_offsets: tuple[int, ...]) -> None:
        super().__init__(convert_charrefs=False)
        self._line_char_offsets = line_char_offsets
        self.events: list[_TextEvent] = []
        # markers of non-text events (tags, comments, declarations) so we
        # can compute the source length of text events that are followed
        # immediately by a non-text event.
        self.non_text_char_starts: list[int] = []

    def _char_pos(self) -> int:
        lineno, col = self.getpos()
        return self._line_char_offsets[lineno - 1] + col

    def handle_data(self, data: str) -> None:
        self.events.append(_TextEvent("data", self._char_pos(), data))

    def handle_charref(self, name: str) -> None:
        # `&#NN;` or `&#xNN;`. `html.unescape` handles both forms and
        # recovers the original source form by wrapping the name.
        decoded = unescape(f"&#{name};")
        self.events.append(_TextEvent("charref", self._char_pos(), decoded))

    def handle_entityref(self, name: str) -> None:
        # `&amp;`, `&lt;`, named entity refs. `html.unescape` decodes
        # recognized names and leaves unrecognized ones literal.
        decoded = unescape(f"&{name};")
        self.events.append(_TextEvent("entityref", self._char_pos(), decoded))

    # non-text events: we only need their source char position so the
    # preceding text event knows where it ends.

    def handle_starttag(self, tag: str, attrs: object) -> None:  # noqa: ARG002
        self.non_text_char_starts.append(self._char_pos())

    def handle_endtag(self, tag: str) -> None:  # noqa: ARG002
        self.non_text_char_starts.append(self._char_pos())

    def handle_startendtag(self, tag: str, attrs: object) -> None:  # noqa: ARG002
        self.non_text_char_starts.append(self._char_pos())

    def handle_comment(self, data: str) -> None:  # noqa: ARG002
        self.non_text_char_starts.append(self._char_pos())

    def handle_decl(self, decl: str) -> None:  # noqa: ARG002
        self.non_text_char_starts.append(self._char_pos())

    def handle_pi(self, data: str) -> None:  # noqa: ARG002
        self.non_text_char_starts.append(self._char_pos())

    def unknown_decl(self, data: str) -> None:  # noqa: ARG002
        self.non_text_char_starts.append(self._char_pos())


def _line_char_offsets(text: str) -> tuple[int, ...]:
    """return a tuple of 0-based code-point offsets at which each line starts.

    line 1 starts at offset 0; subsequent entries are the position of the
    character immediately after each `'\\n'`. the `HTMLParser.getpos()` api
    reports `(lineno, offset)` with `lineno` 1-based and `offset` a
    0-based code-point offset **within** the line, so
    `line_char_offsets[lineno - 1] + offset` gives the absolute code-point
    offset into the decoded string.
    """

    offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)
    return tuple(offsets)


def _char_to_byte_table(text: str) -> tuple[int, ...]:
    """return a table mapping code-point offsets to UTF-8 byte offsets.

    entry `i` is the UTF-8 byte offset of the `i`-th code point; the table
    has `len(text) + 1` entries so the tail position is addressable. the
    adapter uses this to convert `HTMLParser.getpos()` code-point
    coordinates into source-byte coordinates without re-encoding slices
    on the hot path.
    """

    table = [0]
    running = 0
    for ch in text:
        # per-character UTF-8 byte length. for well-formed unicode every
        # character has 1..4 bytes; python's encoder handles surrogates
        # per its standard rules (we do not attempt to special-case them
        # in phase 1).
        running += len(ch.encode("utf-8"))
        table.append(running)
    return tuple(table)


class HtmlAdapter:
    """generic HTML `DocumentAdapter` for seam A phase 1.

    satisfies the linearizable subcontract. stateless and safe to share.
    non-UTF-8 input fails loudly; non-text chunks (tags, comments, doctype,
    `<script>` / `<style>` contents) are skipped in `normalized_text` but
    still consume source bytes, which the anchor map records by leaving
    a gap between consecutive text segments.
    """

    def adapt(self, raw_bytes: bytes, source_ref: SourceRef) -> DocumentView:
        """parse `raw_bytes` as HTML and return a deterministic `DocumentView`.

        raises `UnicodeDecodeError` on non-UTF-8 input; HTMLParser errors
        surface as themselves (stdlib is permissive — malformed markup is
        consumed as data in most cases).
        """

        decoded = raw_bytes.decode("utf-8", errors="strict")
        line_offsets = _line_char_offsets(decoded)
        char_to_byte = _char_to_byte_table(decoded)

        parser = _PositionedParser(line_offsets)
        parser.feed(decoded)
        parser.close()

        text_events = parser.events
        non_text_starts = sorted(parser.non_text_char_starts)
        total_chars = len(decoded)

        # compute the source char length of each text event: it is the
        # distance from the event's start to the minimum of (next text
        # event's start, next non-text event's start, end of document).
        non_text_idx = 0
        for i, ev in enumerate(text_events):
            # advance non_text_idx past any non-text events at or before
            # the current event's start — those cannot be this event's
            # terminator.
            while (
                non_text_idx < len(non_text_starts)
                and non_text_starts[non_text_idx] <= ev.char_start
            ):
                non_text_idx += 1
            candidates: list[int] = [total_chars]
            if i + 1 < len(text_events):
                candidates.append(text_events[i + 1].char_start)
            if non_text_idx < len(non_text_starts):
                candidates.append(non_text_starts[non_text_idx])
            end = min(candidates)
            ev.char_length = max(0, end - ev.char_start)

        # assemble segments in document order. only text events with
        # non-empty source length contribute; empty ones are discarded
        # because they add no domain coverage and no image.
        segments: list[LinearSegment] = []
        normalized_cursor = 0
        normalized_text_parts: list[str] = []
        for ev in text_events:
            if ev.char_length == 0:
                continue
            source_byte_start = char_to_byte[ev.char_start]
            source_byte_end = char_to_byte[ev.char_start + ev.char_length]
            source_length = source_byte_end - source_byte_start
            if source_length == 0:
                continue
            event_text_bytes = ev.text.encode("utf-8")
            normalized_length = len(event_text_bytes)
            if normalized_length == 0:
                # decoded to empty (should not happen for well-formed
                # input, but we do not fabricate a zero-length segment).
                continue
            segments.append(
                LinearSegment(
                    normalized_byte_start=normalized_cursor,
                    source_byte_start=source_byte_start,
                    normalized_length=normalized_length,
                    source_length=source_length,
                ),
            )
            normalized_text_parts.append(ev.text)
            normalized_cursor += normalized_length

        normalized_text = "".join(normalized_text_parts)

        if not segments:
            anchor_map = AnchorMap()
        else:
            anchor_map = build_linearizable_anchor_map(tuple(segments), source_ref)

        anchor_validate_total(anchor_map, normalized_text)

        return DocumentView(
            document_id=derive_document_id(source_ref),
            normalized_text=normalized_text,
            anchor_map=anchor_map,
            source_ref=source_ref,
            metadata={},
        )
