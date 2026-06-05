# Task: select the default document adapter(s) for extractx extras

*Research task. Output is a decision-ready finding, not code. Unblocks picking which adapter library (if any) extractx ships as its default `DocumentAdapter` impl in `extras/`. Informs a downstream ADR.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic working doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; forbidden shortcuts
- [`docs/architecture.md`](../architecture.md) — specifically:
  - §7 seam A — `DocumentAdapter` contract (invariants: anchor_map totality, byte-addressable SourceSpans, idempotent normalization, byte-identical repeat adaptation, parser metadata passthrough under `metadata["parser"]`)
  - §9 `SourceSpan`, `PageRef`, `BoundingRegion` — the multi-layered source anchor model
  - §2 principle 21 — pass-through operational metadata (parser metadata passthrough is load-bearing)
- [`docs/adr/0001-pass-through-operational-metadata.md`](../adr/0001-pass-through-operational-metadata.md) — the pass-through decision; informs how a wrapping adapter should attach parser metadata
- [`docs/thread-orchestration.md`](../thread-orchestration.md) — you are a bounded worker on this task

## Goal

produce a decision-ready recommendation for what extractx ships in `extras/` as default document adapters. options under evaluation:

- `unstructured.io` (open source; wide format coverage)
- `docling` (IBM; newer; modular)
- `pymupdf` + `trafilatura` + `beautifulsoup` (light composition; more code we write)
- `marker` (pdf-specific; high-quality markdown conversion)

output is a finding in `docs/research/default-document-adapter.md`. a downstream ADR will be written by the coordinator based on your recommendation.

**"done" in one sentence:** a single research doc that names a specific recommendation per format (pdf, html, docx, plain text), cites security / maintenance / licensing evidence, and either confirms the recommendation is actionable for v1 or specifies what blocks it.

## Scope

numbered investigation areas. each a specific question. complete each before moving to the next.

### 1. security and maintenance posture

for each of the four candidates, investigate:

- last release date and release cadence over the past 12 months
- issue tracker activity (open vs closed; average time to close; any open critical issues)
- published CVEs / security advisories (GitHub Security Advisories, GHSA, or the project's own advisory process)
- known supply-chain or transitive-dependency issues (e.g., vendored binaries, unpinned large deps, deprecated sub-deps)
- license terms (extractx is provisionally MIT; flag any GPL / AGPL / commercial-clause blockers)

name the specific commit hashes, release tags, or advisory IDs you cite. if you cannot verify a claim, say so — do not extrapolate.

### 2. anchor preservation (load-bearing)

seam A requires byte-addressable `SourceSpan`s. this is the hardest-to-satisfy invariant for a wrapping adapter. for each candidate:

- does the library expose byte-level offsets into the original source document? or does it normalize into cleaned text without a reverse map?
- for pdf-first libraries (pymupdf, marker), does the library expose page-level geometry (bounding boxes, polygons) that maps to a `BoundingRegion`?
- what is the gap between the library's native anchor model and what extractx needs? specifically: can we reconstruct `(byte_start, byte_end)` into the original file bytes after the library has parsed and possibly rewritten the text?
- if not, how much glue code would we need to write to preserve byte-level anchor fidelity? is it tractable or a multi-week project?

this is the hardest criterion. rank the candidates by how well they meet it.

### 3. parser metadata shape (for passthrough)

per principle 21, a wrapping adapter attaches the parser's native metadata under `DocumentView.metadata["parser"]` unchanged. for each candidate:

- what does the library return as structural metadata? (element trees, layout analysis, reading order, table structure, font info)
- is it serializable via msgspec / pydantic? (msgspec is our default replay serializer — see architecture §16)
- are there circular references, non-picklable objects, or large binary blobs that would complicate passthrough?

### 4. format coverage

for each candidate, list formats supported out of the box:
- pdf
- html
- docx
- xlsx / xls
- pptx / ppt
- markdown
- rtf
- epub
- images (png, jpg, tiff) with OCR
- email formats (eml, msg)

our v1 priorities (in order): **pdf, html, plain text, markdown**. others are bonus.

### 5. footprint and dependency tree

for each candidate:
- install size (wheel size, transitive deps count)
- heaviest transitive deps (torch? tensorflow? onnxruntime? opencv? tesseract? system-level deps like `libreoffice` or `poppler`?)
- does the library pull in ML models on install / first use? (extractx users should not have multi-GB models unless they opt in)
- is there a "lite" install variant that excludes OCR / ML models?

### 6. runtime performance profile

quick empirical check (best-effort; full benchmarking is out of scope):
- approximate throughput for a ~10-page PDF
- approximate throughput for a ~50kb HTML document
- does the library expose async / streaming APIs, or is it sync-only? (extractx is async-first; a sync-only adapter runs in a thread executor)

### 7. recommendation

synthesize findings into:

- **recommended pick per format** (pdf, html, plain text, markdown, others). the recommendation may be different per format — e.g., `marker` for pdf, a lighter composition for html.
- **v1 scope decision:** should extractx ship any of these in `extras/` as v1, or ship nothing and require users to provide their own `DocumentAdapter` impls? (it's a legitimate answer to ship nothing and say "bring your own.")
- **if ship something:** which extras install names? (`extractx[pdf]`, `extractx[html]`, `extractx[parser-unstructured]`, etc.)
- **known gaps:** what does the recommendation not cover, and what would unblock coverage?

each branch of the recommendation should be specific enough that the next action is obvious.

## Guardrails

- **research only — no code.** do not add dependencies, do not write adapter implementations, do not modify `pyproject.toml`. the output is a markdown finding.
- **cite specific evidence.** release dates, advisory IDs, repo urls, license files, benchmark numbers. if you extrapolate or guess, label it clearly.
- **no endorsements of libraries with unresolved critical security issues** at the time of the research, regardless of how good their parsing is.
- **do not modify `docs/adr/`, `docs/architecture.md`, `CODEX.md`, `AGENTS.md`, or `CLAUDE.md`** — those are coordinator-owned. if a finding should reshape one of those, flag it in your recommendation.
- **no git push, no commits on behalf of the task other than the one that lands the research doc.** commit to the current branch with message `add research: default document adapter evaluation`. no AI attribution per `CLAUDE.md` git rules.
- **do not install any of the candidate libraries into the project** while researching. use `uv run --isolated` if you need to probe a library at runtime; clean up after.

## Deliverable

- `docs/research/default-document-adapter.md` following [`docs/research/0000-template.md`](../research/0000-template.md)

include in your final output to the coordinator:

- the commands / queries you used for each investigation area
- any gaps you flagged explicitly (what you could not verify and why)
- a one-paragraph summary of the strongest and weakest candidates per the anchor-preservation criterion (the load-bearing one)

## Success criteria

each is testable.

- `docs/research/default-document-adapter.md` exists with the template structure
- investigation areas 1–6 are each addressed with cited evidence; #7 recommendation is concrete
- the recommendation names a specific library (or "ship nothing") per format (pdf, html, plain text, markdown)
- every cited fact has a verifiable source (URL, commit hash, release tag, advisory ID, or explicit "could not verify" label)
- no cited library with known unresolved critical advisories is recommended
- the anchor preservation analysis (area 2) ranks all four candidates
- license terms are verified against extractx's provisional MIT

## Downstream consequences

- informs a new ADR (`docs/adr/0002-default-document-adapter.md`) once the recommendation is reviewed
- updates `docs/architecture.md` §16 project layout (specifically `source/adapters/` and `extras/*`) to reflect the decision
- updates `docs/tasks/bootstrap-project-skeleton.md` if the recommendation changes the extras list (currently `unstructured` is provisionally listed — may be replaced)
- may surface new tasks for implementation (e.g., `docs/tasks/seam-a-pdf-adapter.md` once the library is chosen)
