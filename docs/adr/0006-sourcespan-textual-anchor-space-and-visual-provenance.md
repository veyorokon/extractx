# ADR-0006: SourceSpan textual anchor space and orthogonal visual provenance

**Status:** Accepted
**Date:** 2026-04-20

## Context

The document-adapter research task (`docs/tasks/select-default-document-adapter.md`, finding in `docs/research/default-document-adapter.md`) surfaced a structural problem in seam A's provenance contract.

Current `docs/architecture.md` §7 seam A invariant:
> every `SourceSpan` produced carries byte-addressable coordinates and may optionally carry `page_ref` and `bounding_region` for paginated / visual documents.

Current `SourceSpan` shape (§9): `source_ref`, `byte_start`, `byte_end`, `page_ref`, `bounding_region`. The implied model is that `byte_start` / `byte_end` address bytes identified by `source_ref.content_hash` — i.e., bytes of the original source file.

This is unsatisfiable for PDF as literally stated. PDFs are not linearizable byte streams; the underlying representation is an imperative drawing program over a content stream. Byte offsets into `source_ref.content_hash` (the PDF file's bytes) do not map to logical content positions. No PDF-parsing library meets the invariant as written.

The consequence is Policy Trapped In Consumer at the adapter boundary: every PDF adapter would invent its own interpretation of "byte-addressable" — either silently addressing normalized-text offsets under the same field names (semantic smear), or failing to implement the contract at all (PDF becomes unsupported).

T0e review surfaced a deeper issue: even for linearizable formats, the current contract doesn't explicitly state *which* bytes `byte_*` address. Adapters producing `DocumentView`s with both original source bytes and normalized text could reasonably interpret `byte_*` either way under the current wording. This ambiguity pre-exists PDF; PDF just makes it undeniable.

A further offset-unit mismatch was caught during drafting: "normalized-text offsets" (as originally used in `anchor_map`) is ambiguous — Python `str` is code-point-indexed; UTF-8 bytes of that string are a different coordinate system. Unless the two units are locked to one, seam C's "valid under `anchor_map`" and seam F layer 1 validation become under-specified for `normalized_text`-space spans.

## Decision

**`SourceSpan` carries an explicit `text_anchor_space: Literal["source_bytes", "normalized_text"]` discriminator. `byte_start` / `byte_end` are interpreted relative to the discriminator. All offsets in this contract — `anchor_map`'s domain and `SourceSpan.byte_*` under both `text_anchor_space`s — are byte offsets. Visual provenance (`page_ref`, `bounding_region`) remains orthogonal — attachable to any `SourceSpan` regardless of `text_anchor_space`.**

Specifically:

1. **`SourceSpan` shape** (plugin-public):
   ```
   SourceSpan:
     source_ref: SourceRef
     text_anchor_space: Literal["source_bytes", "normalized_text"]
     byte_start: int
     byte_end: int
     page_ref: PageRef | None
     bounding_region: BoundingRegion | None
   ```
   `text_anchor_space` is required at construction; there is no default.
2. **Coordinate semantics.** `byte_start` / `byte_end` are byte offsets under both coordinate spaces. The invariant is **unit-uniform**: byte offsets everywhere, defined relative to the coordinate space, with UTF-8 alignment where the encoding is known.
   - `text_anchor_space="source_bytes"`: raw byte offsets into the bytes identified by `source_ref.content_hash`. Alignment to the source's native encoding is the adapter's responsibility.
   - `text_anchor_space="normalized_text"`: UTF-8 byte offsets into the UTF-8 encoding of `DocumentView.normalized_text`, UTF-8-aligned (code-point boundaries). These are the **same byte offsets as `anchor_map`'s domain**.
3. **`anchor_map` domain restatement.** `anchor_map` is a total function from **UTF-8 byte offsets into the UTF-8 encoding of `DocumentView.normalized_text`** to `SourceSpan`s. Domain values are UTF-8-aligned (code-point boundaries). Misaligned offsets are outside the contract domain and must not be produced by adapters or consumers.
4. **Visual provenance is orthogonal.** `page_ref` and `bounding_region` can be attached to any `SourceSpan` regardless of `text_anchor_space`. They are not alternate meanings of `byte_*`; they are a separate locator dimension.
5. **Two named subcontracts under seam A's `DocumentAdapter` protocol:**
   - **Linearizable** (plain text, byte-preserving HTML, markdown with offset tracking): spans have `text_anchor_space="source_bytes"`.
   - **Paginated-visual** (PDF, scanned documents, image-based formats): spans have `text_anchor_space="normalized_text"`; `page_ref` / `bounding_region` attached where visually meaningful.
   An adapter's subcontract is declared implicitly by the `text_anchor_space` of its produced spans. An adapter must not mix subcontracts within one `DocumentView`.
6. **`SourceRef` unchanged.** Continues to identify the original source artifact. `content_hash` is not redefined; adapters do not commit normalized forms under `SourceRef`.
7. **Seam C validity restatement.** A `Candidate.source_span` (and every `Candidate.evidence_spans[i]`) is valid under `DocumentView.anchor_map` according to its `text_anchor_space`:
   - `normalized_text`: `byte_start` and `byte_end` are in `anchor_map`'s domain (valid UTF-8-aligned byte offsets into `normalized_text.encode('utf-8')`, with `byte_end <= len(...)`).
   - `source_bytes`: the span must be recoverable from `anchor_map` by inversion over one or more normalized-text byte offsets (i.e., the span was produced by the adapter via `anchor_map` and must be reconstructible by inversion through it).
   All spans emitted by a `CandidateStrategy` for a given `DocumentView` share the `DocumentView`'s `text_anchor_space`.
8. **Seam F layer 1 restatement.** Candidate-layer validation checks `source_span` and `evidence_spans` under `anchor_map` per their `text_anchor_space`. `normalized_text` spans require UTF-8-aligned offsets within `normalized_text.encode('utf-8')`; `source_bytes` spans require a round-trip through `anchor_map`'s image. Spans whose `text_anchor_space` is inconsistent with the `DocumentView`'s adapter subcontract fail with `NegativeOutcome("validation", "candidate.text_anchor_space_mismatch")`. UTF-8-misaligned `normalized_text` spans fail with `NegativeOutcome("validation", "candidate.utf8_alignment")`.
9. **Downstream consumer discipline.** Any code inspecting `byte_*` must either operate agnostically (hashing, equality) or dispatch on `text_anchor_space` before interpreting. Implicit assumption that `byte_*` means source bytes is forbidden.
10. **`InstanceKey.group_id` stability** includes `text_anchor_space` in its hash. Spans with identical `byte_*` but different `text_anchor_space` produce different `group_id`s — correct; they are semantically different spans.
11. **`ReplayArtifact` schema** includes `text_anchor_space` in every `SourceSpan` record. No pre-v1 replay artifacts exist; no migration concern.

## Consequences

- **Upside:** seam A is honest for both linearizable and paginated-visual sources under one `DocumentAdapter` protocol. No format-specific protocols. No silent field-semantic variation. PDF and future paginated formats are implementable within the existing contract.
- **Upside:** unit-uniform contract. `anchor_map`'s domain and `SourceSpan.byte_*` under `normalized_text` use the same byte-level coordinate system (UTF-8 byte offsets into `normalized_text.encode('utf-8')`). No implicit character-vs-byte conversions anywhere in the contract. Validation is direct; `byte_*` semantics are semantics, not a name coincidence.
- **Upside:** canonical `SourceSpan` remains one type — downstream code that handles `SourceSpan` generically (hashing, serialization, equality, replay) needs no discriminator dispatch. Only code that interprets `byte_*` as coordinates needs to dispatch, and that subset is small.
- **Upside:** visual provenance is explicitly orthogonal. PDF adapters produce spans with both `normalized_text` text offsets **and** visual locators on the same span. There's no confusion about which "means" the provenance — both do, in different dimensions.
- **Upside:** `SourceRef.content_hash` stays honest. It identifies the original file. Users who provide a PDF get back `SourceRef`s that point to their PDF, not to some adapter-committed intermediate form.
- **Upside:** the refined contract makes the `docling` `ProvenanceItem.charspan = (0, len(text))` placeholder pattern (flagged in `docs/research/default-document-adapter.md`) directly catchable at layer 1. A `SourceSpan` claiming `text_anchor_space="source_bytes"` whose `byte_*` are not reachable via `anchor_map` fails validation explicitly.
- **Tradeoff:** every `SourceSpan` construction site must provide `text_anchor_space`. Breaking change for any existing code — but no existing code exists pre-v1. Post-v1, the field is required forever; no default shields the caller.
- **Tradeoff:** per-adapter contract tests double — linearizable and paginated-visual subcontracts need separate proof lanes. Cost is a better-structured test surface.
- **Tradeoff:** validators and consumers that want to inspect `normalized_text` content at a span must either encode `normalized_text` to UTF-8 once per `DocumentView` (cheap, cachable) or track a lookup from byte offset to code-point index themselves. Most implementations cache the encoded bytes.
- **Tradeoff:** some downstream code (`CandidateSorter`, visualization, debug tooling) that previously assumed `byte_*` were source bytes must now dispatch on `text_anchor_space`. Explicit discipline replaces implicit assumption.
- **Tradeoff:** no default `text_anchor_space` — users who hand-construct `SourceSpan`s for tests must pick one. Annoying; also correct — a default would re-introduce the silent semantic this ADR forbids.

## Alternatives considered

- **Option 1: defer PDF from v1.** Ship only linearizable adapters; preserve the existing "byte-addressable into source bytes" invariant verbatim; remove `pdf.py` from §16. Rejected. PDF is a headline format; the research task was scoped precisely because PDF support is expected. Deferring is a scope retreat, not a contract fix.
- **Option 2: two subcontracts with identical field semantics.** `byte_*` means source bytes for linearizable; `byte_*` means normalized_text offsets for paginated. No discriminator field. Rejected. "Same field name, different meaning by format" is the exact hidden-semantic branching the anti-patterns list forbids. Even if documented, downstream consumers would have to know the adapter's format to interpret `byte_*` — which is Policy Trapped In Consumer moved one layer in.
- **Option 3 (unrefined): single `coordinate_space` field on `SourceSpan`.** A single discriminator collapsing text + visual into one enum. Rejected in T0e review because visual provenance (page_ref / bounding_region) is genuinely a different locator kind than text offsets, not another flavor of `byte_*`. Refined option 3 (chosen) keeps textual discriminator and visual locators as separate dimensions on the same canonical object.
- **Option 4: redefine `SourceRef.content_hash` as adapter-committed.** `byte_*` always address the adapter's committed serialized form (for PDF: the extracted text). Rejected. `content_hash` stops uniquely identifying the original file; provenance audit becomes unreliable; users' mental model (`SourceRef` = reference to their source) breaks.
- **Splitting `DocumentAdapter` into two protocols.** `LinearizableAdapter` and `PaginatedAdapter` as distinct plugin interfaces. Rejected. Plugin surface doubles for a distinction that the canonical object already captures via one field. One protocol, one discriminator is smaller.
- **Mixed offset units (char-indexed anchor_map + byte-indexed SourceSpan).** Rejected during drafting after review caught the unit mismatch. Forces implicit conversion at every validity check and re-introduces exactly the kind of hidden-unit ambiguity this ADR is meant to remove. Byte-uniform contract is non-negotiable.

## Related

- `docs/architecture.md` §2 principle 15 (provider quirks must not shape public contracts — extended here: format quirks must not shape canonical object semantics)
- `docs/architecture.md` §7 seam A (`DocumentAdapter` — restated with subcontracts)
- `docs/architecture.md` §7 seam C (invariants restated)
- `docs/architecture.md` §7 seam F layer 1 (validation restated)
- `docs/architecture.md` §7 seam G.resolver (`group_anchors`, `group_id` stability)
- `docs/architecture.md` §9 canonical objects (`SourceSpan` shape updated)
- `docs/architecture.md` §15 anti-patterns (new `Format-Silent-Span-Semantics` row)
- `docs/research/default-document-adapter.md` (the T9 finding that surfaced this)
- T0 review queue (T0e thread)

## Follow-on threads (flagged; not in this ADR's scope)

- **`DocumentAdapter` producer versioning.** Replay determinism requires that the adapter used in replay matches the one in the original run. `DocumentView` does not currently carry a `producer_version`. Pinning adapter behavior for replay becomes more visible once seam A has two subcontracts. Future thread.
- **Default sorter semantics.** T0b's proposal requires an explicit `sorter_binding` under `truncate_sorted`; no default sorter. If a future spec-level default sorter is added, it must either dispatch on `text_anchor_space` or operate only on `candidate_id` (coordinate-space-agnostic).
- **Cross-adapter span comparison.** In hypothetical multi-document extractions (not in v1 scope), comparing spans across different adapters is ambiguous. Out of scope.
