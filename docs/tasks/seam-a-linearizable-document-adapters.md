# Task: implement seam A for linearizable document adapters

*This is seam A phase 1. Make the `DocumentAdapter` seam real for formats whose source bytes are linearly addressable: plain text, markdown, and generic HTML. Do not widen into PDF, OCR, or parser-library selection policy in this task.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; seam A summary; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam A, §9 canonical objects, §10 three-tier public surface, §15 anti-patterns, §16 project layout, and §17 proof table entries for seam A**
- [`docs/adr/0001-pass-through-operational-metadata.md`](../adr/0001-pass-through-operational-metadata.md) — parser metadata passthrough discipline
- [`docs/adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md`](../adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md) — `SourceSpan.text_anchor_space`, UTF-8 byte-domain rules, seam-A subcontracts
- [`docs/research/default-document-adapter.md`](../research/default-document-adapter.md) — why this task is intentionally limited to linearizable formats; PDF/default-adapter policy is a separate thread
- [`docs/tasks/core-contracts-and-objects.md`](core-contracts-and-objects.md) — prior thread; use the landed core layer instead of reinventing anchor/object shapes locally
- [`docs/tasks/seam-b-pydantic-spec-construction.md`](seam-b-pydantic-spec-construction.md) — prior seam brief shape; use it as a discipline reference, not as a dependency

## Goal

implement seam A so `DocumentAdapter.adapt(raw_bytes, source_ref) -> DocumentView` is real and deterministic for plain text, markdown, and generic HTML, with a total `AnchorMap`, honest `SourceSpan` semantics, and no hidden parser policy.

**"done" in one sentence:** linearizable adapters produce deterministic `DocumentView`s whose `anchor_map` is total over UTF-8 byte offsets of `normalized_text`, whose spans reverse-map meaningfully to source bytes, and whose parser metadata passthrough stays raw when a parser library is wrapped.

## Scope

numbered implementation areas. do each in order.

### 1. make the seam-A protocol explicit

implement the `DocumentAdapter` callable surface in `src/extractx/core/contracts.py`.

requirements:

- define the protocol method explicitly:
  - `adapt(raw_bytes: bytes, source_ref: SourceRef) -> DocumentView`
- keep it sync
  - seam A libraries are sync; executor/runtime can bridge to threads later
- do not add provider/runtime/executor concerns here
- do not add parser-selection policy or format sniffing policy to the protocol

implementation-shape constraints:

- one method only unless the docs already require another
- no async adapter protocol in this task
- no `producer_version` or replay hooks here; those belong to later seams

### 2. land the anchor-map lookup/inversion surface needed by seam A

extend `src/extractx/core/anchors.py` with the minimal lookup/inversion helpers implied by the current comments and downstream invariants.

requirements:

- keep `AnchorMap` as the canonical object
- add the narrowest honest helper surface needed to:
  - look up the canonical `SourceSpan` for a normalized-text UTF-8 byte offset
  - invert a `source_bytes` `SourceSpan` back to the normalized-text byte offsets whose image it is
  - validate totality over the aligned UTF-8 byte offsets of `normalized_text.encode("utf-8")`
- fail loudly on offsets outside the contract domain
- keep the helper semantics deterministic and pure

implementation-shape constraints:

- do not redesign `AnchorMap` into a mutable index class
- do not add range heuristics or fuzzy lookup behavior
- do not weaken the UTF-8 byte-domain contract from ADR-0006
- if you need a builder/helper for linearizable adapters, keep it minimal and local to seam A

### 3. implement the text adapter

implement `src/extractx/source/adapters/text.py`.

requirements:

- plain text adapter must satisfy the **linearizable subcontract**
  - all produced spans carry `text_anchor_space="source_bytes"`
  - `byte_start` / `byte_end` address raw source bytes
- build a deterministic `DocumentView`
  - `document_id` must be stable and derived honestly from `SourceRef`
  - `normalized_text` must be deterministic and idempotent for the same input
  - `anchor_map` must be total over UTF-8-aligned byte offsets in `normalized_text.encode("utf-8")`
- default text decoding is UTF-8. `SourceRef` does not carry encoding metadata in the current contract; non-UTF-8 input fails loudly in phase 1 rather than triggering heuristic detection or fallback decoding
- reject undecodable input loudly if you cannot decode it under the chosen v1 text policy
  - do not guess encodings with heuristic fallbacks unless you can do so deterministically and honestly
- if no external parser library is wrapped, do not invent fake parser metadata just to populate `metadata["parser"]`
- when parser metadata is present, `metadata["parser"]` must hold the parser's serializable native form (dict / JSON-native structure), not a live Python object; passthrough is "unchanged" at the data-shape level, but still must remain replay-serializable

implementation-shape constraints:

- keep the normalization policy minimal and explicit
- no MIME sniffing, no network, no OCR, no format auto-routing
- no parser-library dependency for plain text

### 4. implement the markdown adapter

implement `src/extractx/source/adapters/markdown.py` as another **linearizable** adapter.

requirements:

- markdown adapter must preserve source-byte addressability
- normalized text may be identity-plus-deterministic normalization; do not add readability, rendering, or semantic restructuring policy
- if you use a parser/tokenizer, its native metadata may be attached under `metadata["parser"]` unchanged; if you do not, do not invent a fake parser-metadata bag
- when parser metadata is present, `metadata["parser"]` must hold the parser's serializable native form (dict / JSON-native structure), not a live Python object

implementation-shape constraints:

- no HTML rendering pipeline
- no table-of-contents generation
- no markdown-to-HTML expansion
- keep this adapter close to the raw markdown bytes

### 5. implement the generic HTML adapter

implement `src/extractx/source/adapters/html.py` for the **linearizable subcontract** only.

requirements:

- produce deterministic `normalized_text` from the input HTML bytes
- preserve source-byte recoverability
  - emitted spans must carry `text_anchor_space="source_bytes"`
  - reverse lookup to original source bytes must be meaningful
- extract text in stable document order
- use stdlib `html.parser` for phase 1 to tokenize tags and extract text content with per-character source-byte offsets. if deterministic text extraction with source-byte recoverability is not achievable using the stdlib alone, stop and report before adding any dependency
- keep parser metadata raw if the adapter wraps a real parser library
- parser metadata, when present, must be attached in a serializable native form suitable for replay/msgspec; do not stash live parser objects
- no network access, no external fetches, no script execution

implementation-shape constraints:

- this is a **generic HTML adapter**, not a readability adapter
- do not do main-content extraction, boilerplate removal, article heuristics, or domain-specific cleanup
- do not add `trafilatura`/`readability` policy in this task
- choose the smallest honest parser approach that preserves deterministic source-byte reconstruction; if a dependency becomes necessary, stop and report with pushback rather than silently widening the task

### 6. source package wiring

implement the minimal source package surface so seam A is importable and testable.

requirements:

- `src/extractx/source/document_view.py` should hold the minimal construction/helpers that genuinely belong to seam A rather than to `core/anchors.py`
- the canonical `DocumentView` type remains in `src/extractx/core/objects.py`; `src/extractx/source/document_view.py` must not re-declare or shadow it. use that module only for adapter-side construction helpers
- wire:
  - `src/extractx/source/__init__.py`
  - `src/extractx/source/adapters/__init__.py`
- keep public/plugin-public imports honest

write-scope note:

- the only supporting edits outside `src/extractx/source/**` should be the smallest ones required in:
  - `src/extractx/core/contracts.py`
  - `src/extractx/core/anchors.py`
- do not widen top-level `extractx/__init__.py` in this task

### 7. explicit non-goals for this task

leave these out:

- `src/extractx/source/adapters/pdf.py` real implementation
- OCR or scanned-document handling
- parser-library selection in `extras/*`
- readability / article extraction for HTML
- DOCX, XLSX, PPTX, EPUB, email, or image adapters
- runtime/executor integration
- candidate generation or any downstream seam behavior
- any contract change to `SourceSpan`, `DocumentView`, or seam A docs

typed stubs may remain where needed, but do not invent behavior owned by later or separate threads.

## Guardrails

- **write scope:** `src/extractx/source/**`, focused tests, and only the smallest supporting edits in:
  - `src/extractx/core/contracts.py`
  - `src/extractx/core/anchors.py`
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly. if so, stop and report with the standard pushback shape.
- **no dependency changes** unless they are strictly required for a deterministic generic HTML or markdown adapter and you cannot complete the task honestly without them. if that happens, stop and push back before editing `pyproject.toml`.
- **no behavior from later seams.** do not implement:
  - seam B schema loading changes
  - seam C candidate logic
  - seam F validation logic beyond what seam-A helpers need for local invariants
  - runtime/executor/replay behavior
- **no fake parser metadata.** principle 21 means pass parser-native metadata through raw when it exists; it does not mean inventing a synthetic parser payload when no parser library provided one.
- **no PDF backdoor.** do not quietly implement `pdf.py` “while you’re here.” PDF is a separate thread because backend/default policy is not the same as linearizable seam-A work.
- **no commits or pushes** unless separately asked. leave the branch ready for review.

## Focused proof

add focused tests primarily under `tests/contracts/` and `tests/source/`.

minimum proof targets to cover:

- `DocumentAdapter.adapt(raw_bytes, source_ref) -> DocumentView` exists on the protocol surface
- repeating adaptation of identical `(raw_bytes, source_ref)` yields byte-identical `DocumentView`
- `document_id` is deterministic and carries no random/clock-derived state
- for text and markdown adapters:
  - all emitted spans carry `text_anchor_space="source_bytes"`
  - `anchor_map` is total over the UTF-8-aligned byte offsets of `normalized_text.encode("utf-8")`
  - source-byte spans are recoverable by inversion over one or more normalized-text byte offsets
- multibyte UTF-8 text cases work honestly
  - aligned offsets are in-domain
  - misaligned offsets are outside the domain and fail loudly
- generic HTML adaptation is deterministic and source-byte recoverable
- parser metadata, when present from a wrapped parser, is attached under `DocumentView.metadata["parser"]` unchanged
- no fake `metadata["parser"]` bag is emitted when no parser-native metadata exists

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/source/document_view.py`
- `src/extractx/source/adapters/text.py`
- `src/extractx/source/adapters/markdown.py`
- `src/extractx/source/adapters/html.py`

with only minimal supporting edits elsewhere if required by the seam-A surface.

include in your final report:

- exact files changed
- the normalization policy chosen for text, markdown, and HTML
- whether any dependency change was truly required; if not, say so explicitly
- any remaining ambiguity that should become a coordinator-owned follow-on thread rather than more code

## Success criteria

- `DocumentAdapter` has an explicit callable surface
- seam A is real for plain text, markdown, and generic HTML
- `DocumentView` construction is deterministic and idempotent for identical input
- `AnchorMap` has the minimal lookup/inversion surface needed by seam A and downstream validation contracts
- linearizable adapters emit only `text_anchor_space="source_bytes"` spans
- no parser policy, readability policy, or PDF policy is smuggled into this task
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pyright`
- top-level repo state remains coherent with the architecture/doc pact

## Downstream consequences

- gives seam C and seam F a real `DocumentView` / `AnchorMap` surface to validate against
- gives the execution/runtime thread an honest sync adapter boundary to wrap
- leaves PDF / paginated-visual work for a separate focused thread rather than mixing two subcontracts in one implementation task
- if this task exposes a real contradiction in the current seam-A contract for linearizable formats, that becomes a new coordinator-owned thread before more implementation proceeds
