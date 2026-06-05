# Default document adapter evaluation

*Working doc. Investigation as of 2026-04-20. Evaluates `unstructured`, `docling`, `pymupdf + trafilatura + beautifulsoup`, and `marker` against seam A's `DocumentAdapter` contract (byte-addressable `SourceSpan`s, anchor_map totality, parser-metadata passthrough per principle 21).*

## Bottom line

**Ship nothing in `extras/` for v1 as a turnkey default `DocumentAdapter`.** None of the four candidates preserves byte-addressable offsets into the original source bytes — the load-bearing seam A invariant. Every candidate normalizes into cleaned text plus geometric anchors (page number + bounding box), not byte ranges. Recommending one without building a serious reverse-mapping layer would silently break seam A's contract that "every `SourceSpan` produced carries byte-addressable coordinates."

**Minimum viable alternative:** ship a thin, in-repo `pymupdf + lxml/beautifulsoup` composition in `extractx/source/adapters/` for `pdf`, `html`, `text`, and `markdown` formats, where extractx owns the byte-offset reconstruction directly against the raw bytes (not against a wrapped parser's cleaned text). This is the only path consistent with the contract. **Caveat:** PyMuPDF is AGPL-3.0 / commercial dual-licensed. That makes it unsuitable as the shipped default under extractx's provisional MIT — users who vendor `extractx[pdf]` into a closed-source product would inherit the AGPL obligation unless they pay Artifex. See §1 and §7. For the MIT baseline, ship `pypdfium2` (BSD-3-Clause) as the PDF backend instead, paying a text-extraction quality tax. Or ship no PDF default at all and let users bring their own adapter.

This finding forces two decisions:

1. **ADR-draft:** `extras/unstructured/` as provisionally listed in `docs/tasks/bootstrap-project-skeleton.md` line 40 should be removed or rescoped. Unstructured is the best-covered of the four on format breadth and has no open critical advisories, but it does not meet the byte-offset invariant and is not a viable default without a reverse-mapping layer we do not have.
2. **Clarify seam A contract scope:** is "byte-addressable" strictly offsets into the raw PDF/HTML byte stream, or is "byte-addressable coordinates" satisfied by `(page_number, bounding_region)` for visual documents plus char-offset-into-normalized-text for linearizable formats? See the pushback block in §8. If the coordinator relaxes the contract, two candidates (`docling`, `unstructured`) become viable; if the contract holds literally, only an in-repo composition qualifies.

## Candidate summary table

| Library | License | Latest release | Open critical advisories | Anchor model | Byte-addressable into source? | Torch required | Default rank |
|---|---|---|---|---|---|---|---|
| unstructured | Apache-2.0 | 0.22.22 (2026-04-20) | No (GHSA-gm8q-m8mv-jj5m fixed in 0.18.18) | page_number + CoordinatesMetadata (pixel bbox) | ❌ no byte offsets into source | No (base install) | 2nd |
| docling | MIT | v2.90.0 (2026-04-17) | None published | ProvenanceItem{page_no, bbox, charspan: tuple[int,int]} | ❌ `charspan` is length of item text, not source-byte offset | **Yes, mandatory** (`torch>=2.2.2`) | 3rd |
| pymupdf + trafilatura + bs4 | AGPL-3.0 / commercial (pymupdf); Apache-2.0 (trafilatura); MIT (bs4) | PyMuPDF 1.27.2.2 (2026-03-20); trafilatura 2.0.0 (2025-12-03) | None published | pymupdf: per-char `origin`+`bbox` via `get_text("rawdict")` | ⚠ reconstructable for PDF via char-by-char + bytes; HTML requires extractx to hand-roll byte offsets with lxml incremental parser | No | 1st (technically), blocked on AGPL for MIT shipping |
| marker | GPL-3.0 + modified OpenRAIL-M on weights | v1.10.2 (2026-01-31) | None published | per-block polygon + page; JSON output tree | ❌ no source-byte offsets | **Yes** (PyTorch + Surya) | 4th (also license-blocked) |

---

## 1. security and maintenance posture

### unstructured (Unstructured-IO/unstructured)

- **License:** Apache-2.0. Verified at `https://github.com/Unstructured-IO/unstructured/blob/main/LICENSE.md` (quoted first line: "Apache License Version 2.0, January 2004"). Compatible with extractx's provisional MIT.
- **Advisories:** one published GHSA, **GHSA-gm8q-m8mv-jj5m** — "Path Traversal via Malicious MSG Attachment Allows Arbitrary File Write." Critical (CVSS 9.8). Affected `<= 0.18.17`; **fixed in 0.18.18**. Published 2026-02-03. Source: `https://github.com/Unstructured-IO/unstructured/security/advisories/GHSA-gm8q-m8mv-jj5m`. Current 0.22.x line is clear.
- **Release cadence:** multiple releases per week; latest `0.22.22` on 2026-04-20; `0.22.20` notes "upgraded vulnerable dependencies." Active maintenance confirmed via `https://github.com/Unstructured-IO/unstructured/releases`.
- **Supply chain:** Apache-2.0 base is clean. `extras` invoke heavy system deps (`poppler-utils`, `tesseract-ocr`, `libreoffice`) not vendored in-wheel but required at runtime for non-text formats — this is a system-level ops burden on adopters.

### docling (docling-project/docling, IBM)

- **License:** MIT. Verified at `https://raw.githubusercontent.com/docling-project/docling/main/LICENSE` (first line: "MIT License"). Compatible with extractx MIT. **Note:** `docling`'s own code is MIT, but individual bundled models may carry distinct licenses (the project flags this). If docling is distributed as a hard dep, only the code license gates extractx's own license; model weights ship under their own terms at download time.
- **Advisories:** none published (`https://github.com/docling-project/docling/security/advisories`). Could-not-verify: there may be undisclosed issues in the internal IBM tracker; I only have public GHSA visibility.
- **Release cadence:** ~3-4 releases per week over the last month; latest `v2.90.0` on 2026-04-17. Very active.
- **Supply chain:** pulls `torch>=2.2.2` and `torchvision` as **mandatory** runtime deps (`https://raw.githubusercontent.com/docling-project/docling/main/pyproject.toml`). Also pulls `docling-parse`, `docling-ibm-models`, `rapidocr`, `pypdfium2`, `beautifulsoup4`, `lxml`, `python-docx`, `python-pptx`, `openpyxl`. This is a **very heavy** dep tree for a library that declares itself MIT-light.

### pymupdf + trafilatura + beautifulsoup (composition)

- **Licenses:**
  - **PyMuPDF:** **AGPL-3.0 or commercial** (Artifex). Verified at `https://pypi.org/project/PyMuPDF/` (classifier: "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License"). **AGPL is incompatible with extractx's provisional MIT** if extractx distributes a hard dep on PyMuPDF — downstream proprietary users would inherit the AGPL obligation. Users whose own code is closed-source must obtain a commercial license from Artifex.
  - **trafilatura:** Apache-2.0 since v1.8.0 (verified at `https://github.com/adbar/trafilatura/blob/master/LICENSE`). Compatible with MIT.
  - **beautifulsoup4:** MIT. Compatible.
- **Advisories (PyMuPDF):** none published (`https://github.com/pymupdf/PyMuPDF/security/advisories`). MuPDF upstream has had CVEs historically (native PDF parsing is a high-risk surface); Artifex tracks them in MuPDF release notes but not always through GHSA. Could-not-verify: full historical CVE count against MuPDF over the past 12 months. Recent PyMuPDF releases (1.26.x → 1.27.2.2) each ship an updated MuPDF vendored binary, suggesting ongoing patch flow but not visible as GHSAs on the Python wrapper.
- **Advisories (trafilatura):** none published (`https://github.com/adbar/trafilatura/security/advisories`). v2.0.0 released 2025-12-03; cadence roughly every 4–6 weeks.
- **Release cadence (PyMuPDF):** monthly-ish; latest `1.27.2.2` on 2026-03-20 (actually 2025-03-20, verified as most recent per `https://github.com/pymupdf/PyMuPDF/releases`). Could-not-verify: my release-date extraction from the GitHub page shows "March 20, 2025" for some listings — marker of the page's date rendering; the release tags themselves are correctly ordered.

### marker (datalab-to/marker, Endless Labs)

- **License:** **GPL-3.0** for code; **modified OpenRAIL-M** for model weights ("free for research, personal use, and startups under $2M funding/revenue"). Verified at `https://github.com/datalab-to/marker/blob/master/LICENSE` and repo README. **GPL-3.0 is incompatible with extractx's provisional MIT** when extractx ships it as a hard dep; downstream users would face copyleft obligations. The model-weight clause also imposes a revenue cap on commercial use. Users above that threshold must obtain a commercial license via Datalab.
- **Advisories:** none published (`https://github.com/datalab-to/marker/security/advisories`).
- **Release cadence:** ~monthly at the top of 2026; latest `v1.10.2` on 2026-01-31. Before that, the 1.9.x/1.10.x line released through Aug–Sep 2025.
- **Supply chain:** PyTorch, Surya (GPL also), Texify. Heavy; ML-model-downloading at first use.

**Key insights:**

- **unstructured** and **docling** are the only candidates whose top-level license is MIT/Apache-compatible with extractx's provisional MIT **without** a commercial license purchase.
- **PyMuPDF** and **marker** are license-blocked as shipped defaults under MIT. They remain fine for users who opt in with compatible licensing arrangements, but extractx should not vendor them into `extras/`.
- Only one unresolved critical advisory existed across the four and it is patched.

---

## 2. anchor preservation (load-bearing)

Seam A (`docs/architecture.md` §7 seam A) requires: **"every `SourceSpan` produced carries byte-addressable coordinates and may optionally carry `page_ref` and `bounding_region`."** `SourceSpan` in §9 is:

```
source_ref: SourceRef
byte_start: int
byte_end: int
page_ref: PageRef | None
bounding_region: BoundingRegion | None
```

The load-bearing fields are `byte_start` and `byte_end`, typed `int`, read against the original `(raw_bytes, SourceRef)` pair the adapter received. `page_ref` and `bounding_region` are optional supplements. So: **any adapter that only emits `(page_no, bbox)` violates the contract unless paired with a reverse-mapping layer that recovers `(byte_start, byte_end)` into the raw file bytes.**

### Ranking (best-to-worst on byte-addressability into original source)

#### 1. pymupdf + trafilatura + beautifulsoup (composition)

- **PDF side (PyMuPDF):** `page.get_text("rawdict")` returns a per-character list where each char has `c` (unicode), `origin` (x,y), `bbox` (x0,y0,x1,y1) — but **no direct byte offset into the PDF file bytes**. PDFs do not linearize into a text stream with stable byte offsets — text is laid out by a content-stream interpreter — so "byte offset into the PDF" is not naturally defined the way it is for HTML/text. In practice the right canonical anchor for PDF is `(page_no, bounding_region)` with a char-level fallback via `origin`.
  - This means to honor seam A **literally**, an adapter built on PyMuPDF must define `byte_start/byte_end` as offsets into its own `normalized_text` (the reconstructed reading-order text) and rely on `anchor_map` to carry `(page_no, bbox)` as the source-truth anchor. That's allowable under §7 if `SourceRef` resolves to the normalized text rather than the raw PDF bytes — but that reinterprets "byte-addressable" and needs coordinator sign-off (see §8 pushback).
  - Field-level source: `https://pymupdf.readthedocs.io/en/latest/textpage.html`; additional detail via deepwiki and the search summary of `extractRAWDICT()` characterization.
- **HTML side (bs4 / trafilatura):** bs4 parses HTML into a DOM but does not by default expose source byte offsets; `lxml.etree.iterparse` does, and so does `html.parser.HTMLParser.getpos()` which gives `(line, offset)`. **extractx can hand-roll byte offsets using `lxml`'s incremental parse + sourceline/sourcepos**, or by using `re` over the raw bytes once element anchors are known. This is tractable (~hundreds of lines of glue, not multi-week) because HTML is a linearizable byte stream.
- **Plain text / markdown:** trivial — offsets are the input bytes directly. Normalization (unicode NFC, whitespace) must be idempotent; a simple identity-plus-normalizer adapter is the right shape.
- **Gap to seam A:** smallest. extractx owns the reverse mapping directly against raw bytes, which is the only structurally sound way to honor "byte-addressable."

#### 2. unstructured (Unstructured-IO/unstructured)

- `ElementMetadata` (from `unstructured/documents/elements.py`) exposes `filename`, `page_number`, `coordinates: CoordinatesMetadata` (pixel/point bbox), `languages`, etc., but **no byte offset into source**. Confirmed via code inspection: `ElementMetadata` is a custom (non-pydantic) class with `to_dict()` / `from_dict()`, JSON-serializable.
- For PDF: coordinates are pixel-space polygons per element; page_number is tracked. No character-level position.
- For HTML: `partition_html` emits `Element`s with `text` but no original offset or DOM path into the input. To reverse-map back to HTML bytes, extractx would have to run its own second pass over the raw HTML matching element text. This is unreliable whenever text appears more than once.
- **Gap to seam A:** significant. Would require a dedicated reverse-mapping layer, and for HTML with repeated text the mapping is ambiguous. Multi-week project at minimum to do correctly.

#### 3. docling (docling-project/docling)

- `ProvenanceItem` in `docling-core/types/doc/document.py`:
  ```
  page_no: int
  bbox: BoundingBox
  charspan: tuple[int, int]      # "Character span (0-indexed)"
  ```
- **Critical finding:** `charspan` is **not** an offset into the original source bytes. In `docling/backend/html_backend.py`, the HTML backend sets `charspan=(0, len(text))` — i.e., it is the length of the item's own extracted text string (effectively a placeholder). Confirmed by direct inspection of `_make_prov` and `_make_text_prov` in the HTML backend. The PDF/pypdfium2 backend does not set `charspan` meaningfully either.
- So docling's `charspan` is essentially **dead metadata** at present for anchor purposes. The real anchors docling provides are `(page_no, bbox)` — good for PDF visual grounding, but not byte-addressable into the source file.
- **Gap to seam A:** moderate for PDF (visual anchors present; byte offsets absent), severe for HTML (no useful char-level provenance; `charspan` value is misleading).
- Docling also discards page information when round-tripped through its own markdown export (per maintainer in discussion #1012); only the JSON save format is lossless. This is fine for extractx (we would consume the structured form directly), but it's a signal that provenance discipline in docling is still maturing.

#### 4. marker (datalab-to/marker)

- JSON output is a tree of typed blocks: each block carries `polygon` (4-corner, page coords), `block_type`, `children`, `section_hierarchy`, TOC with `page_id` + `polygon`. No byte-offset field.
- Marker does not expose PDF char-level origin in output — it linearizes to markdown/HTML/JSON via Surya's layout + OCR models.
- **Gap to seam A:** largest. Marker is a PDF→markdown converter optimized for downstream LLM consumption, not a provenance-preserving parser. Recovering byte-addressable spans from its output would mean second-passing the original PDF text against the markdown text, which is exactly the ambiguity-prone reverse-mapping pymupdf avoids.

### Ranking summary (best → worst anchor preservation into original source bytes)

1. **pymupdf + trafilatura + bs4** — the only stack where extractx can honor byte-addressability directly, because the composition operates close enough to the raw bytes that extractx owns the mapping.
2. **unstructured** — good visual anchors (page_number + pixel-bbox coordinates); no byte offsets; would need a reverse-mapping adapter.
3. **docling** — similar visual anchors to unstructured, and structurally better-typed (pydantic), but `charspan` is misleading placeholder data.
4. **marker** — PDF-specific; output is markdown/JSON with polygons only; no way to recover source-byte offsets without a full reverse-mapping pass.

**Key insights:**

- **No candidate delivers byte-addressable source provenance out of the box.** Every one of them normalizes first and loses byte-level addressability. This is the core finding of the evaluation and it forces the "ship nothing / ship thin composition" recommendation.
- **Docling's `charspan` looks like exactly what seam A wants but is not.** Reading the field description alone ("Character span (0-indexed)") would suggest it answers our question; reading the HTML backend shows it does not. That is a classic **leaked-information smell** for any adapter we'd build on top: the wrapped parser's metadata shape *looks* contractual but is not semantically load-bearing.
- **For PDF specifically**, "byte-addressable into source bytes" may be the wrong invariant to insist on — PDF is not a linearizable byte stream. The cleaner contract is `(page_no, bounding_region)` as the canonical anchor with char-offsets into the adapter's normalized reading-order text. This should be resolved at the seam-contract level (see §8 pushback).

---

## 3. parser metadata shape (for passthrough under principle 21)

Under principle 21 / ADR-0001, a wrapping adapter attaches the parser's native metadata under `DocumentView.metadata["parser"]` unchanged. So the question is: is each candidate's metadata object serializable via msgspec/pydantic without reshape?

### unstructured

- `ElementMetadata` is a **custom class with `__getattr__`/`__setattr__`** indirection. `to_dict()` produces a JSON-serializable dict; `from_dict()` rehydrates. msgspec can serialize the dict form directly.
- **Circular/opaque blobs:** none apparent at the metadata level. Coordinate objects are plain tuples+dataclass.
- **Verdict:** serializable via `to_dict()` → dict → msgspec. extractx's passthrough is trivial.

### docling

- `DoclingDocument`, `ProvenanceItem`, `BoundingBox` are all **pydantic BaseModels** (`docling-core/types/doc/document.py`, `docling-core/types/doc/base.py`). `model_dump_json()` is the native serialization path.
- **Verdict:** directly serializable. msgspec can consume the dumped JSON. This is the cleanest shape for passthrough among the four.

### pymupdf + trafilatura + bs4

- PyMuPDF text dicts are **plain dicts** (not pydantic/dataclass). `get_text("rawdict")` returns nested dicts with tuples for bbox/origin. Fully JSON-serializable.
- trafilatura output is plain strings / JSON per request.
- bs4 parsed trees are **not** JSON-serializable out of the box (Tag objects hold references). extractx would flatten bs4 to a serializable form before passthrough.
- **Verdict:** serializable for PyMuPDF output directly; bs4 needs a flatten step (we'd own that step inside the adapter — still a single normalization site).

### marker

- Block tree is **pydantic BaseModels** (verified `class Document(BaseModel)` in `marker/schema/document.py`).
- **Verdict:** directly serializable via `model_dump_json()`.

**Key insights:**

- All four are serializable; docling and marker are cleanest because they are pydantic-native. unstructured is easy via `to_dict()`. pymupdf requires minimal glue.
- This area is **not** a differentiator in the decision. All four meet the passthrough contract cheaply.

---

## 4. format coverage

Declared priority order (from the brief): **pdf, html, plain text, markdown**. Anything else is bonus.

| Format | unstructured | docling | pymupdf+trafilatura+bs4 | marker |
|---|---|---|---|---|
| pdf | ✅ (via `unstructured[pdf]`, needs poppler+tesseract for OCR) | ✅ (primary use case, Heron layout model + pypdfium2) | ✅ (pymupdf; no OCR by default) | ✅ (primary use case; GPU-accelerated markdown conversion) |
| html | ✅ (via `unstructured` base) | ✅ (HTML/XHTML backend; optional headless-browser `htmlrender` extra) | ✅ (trafilatura for main content; bs4 for structural) | ❌ |
| plain text | ✅ | ❌ (not in the input-format list) | ✅ (identity + normalize) | ❌ |
| markdown | ✅ (via `unstructured[md]`) | ✅ (via marko) | ✅ (identity or light conversion) | ❌ (output format only) |
| docx | ✅ | ✅ | ❌ (not in scope) | ✅ (via `[full]`) |
| xlsx | ✅ | ✅ | ❌ | ❌ |
| pptx | ✅ | ✅ | ❌ | ❌ |
| rtf | ✅ | ❌ | ❌ | ❌ |
| epub | ✅ | ❌ | ❌ | ❌ |
| images + ocr | ✅ (tesseract/paddle) | ✅ (rapidocr/easyocr/tesserocr/ocrmac) | ❌ (pymupdf has OCR hook via mupdf) | ✅ (Surya OCR) |
| email (eml/msg) | ✅ | ❌ | ❌ | ❌ |

**Key insights:**

- **Unstructured wins on raw format breadth**, as advertised. For v1 priorities (pdf, html, text, markdown), it covers all four.
- **docling** covers pdf/html/markdown natively but **plain text is not an input format** per the docs. That's a gap for extractx's v1 priorities.
- **pymupdf+trafilatura+bs4** covers the four v1 priorities cleanly but nothing beyond without additional composition effort.
- **marker** is PDF-only for the priority formats. Not a general default.

---

## 5. footprint and dependency tree

| Library | Wheel size | Mandatory torch | System deps | Notes |
|---|---|---|---|---|
| unstructured (base) | 1.6 MB | No | — (extras add tesseract, poppler, libreoffice) | Base install is plain text + HTML + JSON + XML + email only; PDF/image/office require `[pdf]`, `[image]`, `[doc]` etc. |
| docling | 494 KB (wheel); **multi-GB installed** in practice | **Yes** (`torch>=2.2.2`, `torchvision`) | None system-level mandatory; OCR extras pull `tesserocr`, `rapidocr` (with onnxruntime) | Heavy mandatory transitive tree: `docling-parse`, `docling-ibm-models`, `pypdfium2`, `beautifulsoup4`, `lxml`, `python-docx/pptx`, `openpyxl`, `marko`, `rapidocr`. Pulls layout/OCR ML models on first use. |
| pymupdf | ~20–25 MB wheel (platform dependent) | No | None (mupdf vendored in-wheel) | Self-contained. |
| trafilatura | 133 KB wheel | No | None | Pure-Python + lxml. |
| beautifulsoup4 | ~80 KB | No | None | — |
| marker | 196 KB wheel; **multi-GB installed** | **Yes** (PyTorch + Surya + Texify) | GPU recommended for usable throughput | Downloads ML models at first use. |

**Key insights:**

- **docling's mandatory torch dep is load-bearing in the wrong direction.** Users who only want HTML or markdown extraction pay the full torch/torchvision install cost. For a library that declares itself lightweight-ish, this is a surprise.
- **marker's footprint is clearly opt-in territory** — it cannot be a default.
- **unstructured's base install is light** and adds weight only when format-specific extras are pulled. This is the cleanest opt-in footprint curve among the four.
- **pymupdf composition is the lightest** by a wide margin, but gated by AGPL.

---

## 6. runtime performance profile

Empirical benchmarks from published third-party comparisons (best-effort; I did not run these locally per the guardrails not to install candidates into the project).

### PDF throughput (representative; x86 CPU unless noted)

- **Docling:** 3.1 seconds/page on x86 CPU; 1.27 seconds/page on M3 Max SoC; 0.49 seconds/page on NVIDIA L4 GPU (source: `https://procycons.com/en/blogs/pdf-data-extraction-benchmark/`, `https://arxiv.org/html/2501.17887v1`). Procycons reports 6.28 seconds for 1 page and 65.12 seconds for 50 pages (sub-linear at scale; warm-up cost dominates single-page).
- **Unstructured:** 4.2 seconds/page on x86 CPU; 2.7 seconds/page on M3 Max SoC; no GPU benefit. Procycons: 51.06 seconds for 1 page; 141.02 seconds for 50. Notable warm-up cost.
- **Marker:** 16+ seconds/page on x86 CPU; 4.2 seconds/page on M3 Max; **0.18 seconds/page (122 pages/second)** on H100 in batch mode. Source: `https://github.com/datalab-to/marker#benchmarks`. Marker is GPU-batch optimized.
- **PyMuPDF:** sub-100 ms for a 10-page text-heavy PDF is routine (source: deepwiki reports `extractRAWDICT()` on 1,310-page PDF in < 5 seconds, i.e. ~3.8 ms/page). No OCR included — that's text-only throughput.

**Could not verify:** whether Procycons benchmark scored docling with models cold-started or warm; whether M3 Max numbers include Metal acceleration.

### HTML throughput (~50 KB)

- Not published head-to-head in the sources surveyed. Could-not-verify with citations. Order of magnitude:
  - **trafilatura:** designed for web crawling; ms-range per document is the expected order.
  - **bs4 + lxml:** similar ms-range.
  - **unstructured.partition_html:** reported in their own blog posts to be tens-of-ms; not quantified in a benchmark I was able to cite.
  - **docling HTML backend:** no published throughput numbers found.

### Async / streaming APIs

- **None of the four exposes an async API.** All are sync-only.
- Docling's serve package (`docling serve`) has an async HTTP surface but the core conversion is sync under the hood.
- Unstructured's Python API is sync; their `unstructured-ingest` service adds async but is a separate product.
- PyMuPDF is sync (CPython/C bindings).
- Marker is sync.
- **Consequence for extractx:** any wrapping `DocumentAdapter` runs in a thread executor when called from asyncio. That is standard and not a blocker; it's a note for `execution/runtime.py`.

**Key insights:**

- **Throughput is not the differentiator at this decision point** — anchor preservation is. PyMuPDF for text-only PDF is dramatically faster than the ML-based alternatives, but that's expected because it does less (no layout classification, no OCR).
- **If GPU is available, marker + docling both scale well**; none of the four is async-native; all must be bridged into extractx's asyncio model via `asyncio.to_thread` / `run_in_executor`.

---

## 7. recommendation

### per-format pick

- **PDF:**
  - **Recommended v1 default:** **ship nothing** in `extras/`. Require users to bring their own `DocumentAdapter`. Document the reason: PDF byte-addressability is structurally unsound as a seam-A invariant (see §8 pushback), and none of the four candidates satisfies it literally anyway.
  - **Alternative if "ship something" wins:** build a thin in-repo `extractx/source/adapters/pdf.py` on `pypdfium2` (BSD-3-Clause, already a transitive dep of docling, so the wheels are mature) + a char-level reverse map. This accepts the tradeoff of lower text-extraction quality than PyMuPDF's MuPDF in exchange for MIT-clean licensing. Document `extractx[pdf]` install that pulls `pypdfium2`.
  - **Do not** vendor PyMuPDF, marker, unstructured, or docling as the default — each fails at least one hard criterion (license, byte addressability, or weight).

- **HTML:**
  - **Recommended v1 default:** ship a thin in-repo `extractx/source/adapters/html.py` on `lxml` + optional `trafilatura` for main-content extraction. `lxml.etree.XMLParser(huge_tree=True)` plus `sourceline`/`sourcepos` give recoverable byte offsets; trafilatura can be an optional extra for users who want boilerplate stripping.
  - Extras install: `extractx[html]` pulls `lxml` and `beautifulsoup4`; `extractx[html-readable]` additionally pulls `trafilatura`.
  - **Do not** wrap unstructured or docling — they discard source byte offsets.

- **Plain text:**
  - **Recommended v1 default:** ship `extractx/source/adapters/text.py` as an identity-plus-normalize adapter in core. No `extras` needed. This is a few dozen lines of code.

- **Markdown:**
  - **Recommended v1 default:** ship `extractx/source/adapters/markdown.py` using `markdown-it-py` (MIT; pure Python; returns a token stream with source map) or a direct byte-range identity adapter (markdown text is its own `normalized_text`; `anchor_map` is identity). markdown-it-py's `Token.map: list[int]` gives line-level positions which can be converted to byte offsets cheaply.
  - Extras install: `extractx[markdown]` pulls `markdown-it-py`.
  - **Do not** wrap unstructured or docling for this — overkill.

### v1 scope decision

**Ship nothing as a turnkey default document adapter for v1.** Ship the thin compositions above in `extractx/source/adapters/{html,text,markdown,pdf}.py` directly in core (or under `extractx[format]` extras for dep-heavy cases), owning the byte-offset reconstruction. This is the only recommendation consistent with seam A's contract as written, AND it keeps extractx's license story clean.

If the coordinator decides after pushback (§8) that "byte-addressable" can be satisfied by char-offsets-into-normalized-text for paginated formats plus `(page_no, bbox)`, then **docling** becomes viable for PDF (best pydantic-native metadata shape, MIT license) with the caveat that `torch` is a mandatory dep. In that relaxed contract, the recommendation shifts to: **ship `extras/docling/adapter.py` for PDF** and keep the in-repo compositions for HTML/text/markdown.

### extras install names (if ship-something path wins after contract clarification)

- `extractx[pdf]` → `pypdfium2` (MIT-clean) or `extractx[pdf-docling]` → `docling` (relaxed contract only)
- `extractx[html]` → `lxml`, `beautifulsoup4`
- `extractx[html-readable]` → add `trafilatura`
- `extractx[markdown]` → `markdown-it-py`
- **Remove** `extractx[unstructured]` from bootstrap task provisional list
- **Do not add** `extractx[pymupdf]` or `extractx[marker]` as defaults (license)

### known gaps

- **HTML byte-offset reconstruction** via lxml is correct for single-pass documents but needs careful handling of entity references and scripts. Estimated effort: 1–2 focused days including tests.
- **PDF byte-addressability** is unresolved at the contract level (see §8). No recommendation proceeds without the coordinator's call on whether `(page_no, bounding_region)` satisfies "byte-addressable" for paginated formats.
- **OCR** is not covered by the "ship nothing" recommendation. Users who need OCR bring `tesserocr`, `paddleocr`, or `rapidocr` themselves. Flag this explicitly in `docs/architecture.md` §16 when the extras list is finalized.
- **XLSX / DOCX / PPTX** support is also not covered. `python-docx`, `openpyxl`, `python-pptx` are obvious upgrades when extractx supports office formats. Out of scope for v1 per priority list.

---

## 8. pushback on the brief and on the seam A contract

Following the pushback shape required by the worker brief:

- **current contract (seam A, `docs/architecture.md` §7):** "every `SourceSpan` produced carries byte-addressable coordinates and may optionally carry `page_ref` and `bounding_region`"; `SourceSpan.byte_start/byte_end: int` (§9).
- **observed gap or contradiction:** PDF documents are not byte-addressable at the text level — the PDF content stream is an imperative drawing program, not a linearized text buffer. There is no natural `byte_start/byte_end` into raw PDF bytes for an extracted word. Every serious PDF parser (PyMuPDF, pypdfium2, docling, marker, unstructured) converges on `(page_no, bounding_region)` as the canonical anchor for extracted text, and emits char-offsets only into its own normalized reading-order output. Insisting on byte offsets into raw PDF bytes makes seam A unsatisfiable by any upstream parser without a second, custom reverse-mapping pass — which is also ambiguous whenever the same text occurs twice.
- **consequence if implemented as written:** the "ship nothing" recommendation in §7 is the only honest output. No default adapter ships. Adopters write custom reverse-mappers and re-learn this lesson one adapter at a time. The invariant gets silently bent inside each sibling-package adapter, turning seam A into a *Policy Trapped In Consumer* anti-pattern (each adapter silently decides what "byte-addressable" means for its format).
- **proposed cleaner pattern:** formalize two subcontracts inside seam A:
  - **linearizable-format subcontract** (html, text, markdown, xml, json, docx extracted text): `byte_start/byte_end` are offsets into the original source bytes. This is the current contract as written, and it holds.
  - **paginated-visual-format subcontract** (pdf, images, scanned documents): `byte_start/byte_end` are offsets into the adapter's `normalized_text`; the canonical source anchor is `(page_ref, bounding_region)` which must be populated. The adapter must emit a deterministic `normalized_text` and carry the parser's native metadata under `metadata["parser"]`. Consumers that need raw-byte addressing for audit must use the `(page_ref, bounding_region)` pair, not `byte_start/byte_end`.
- **seam / ownership impact:** seam A stays with `DocumentAdapter`; the split is in the contract language, not the protocol. `SourceSpan` gains a docstring note that for paginated formats, `byte_start/byte_end` are normalized-text offsets and `page_ref`+`bounding_region` carry the original-bytes-equivalent anchor. `AnchorMap` contract (`docs/architecture.md` §7 seam A first invariant) is unchanged — it is still a total function from normalized-text offsets to `SourceSpan`.
- **clarification vs architecture change:** **clarification.** The code-level shape of `SourceSpan` does not change; only the semantic definition of `byte_start/byte_end` for visual-format adapters does. This is a one-line edit to §7 seam A and a two-line docstring on `SourceSpan` in §9.
- **proof target:** a contract test in `tests/contracts/test_seam_a_pdf.py` that asserts: (a) `byte_start` and `byte_end` lie within `len(document_view.normalized_text)`, (b) `page_ref` and `bounding_region` are both populated for spans derived from PDF input, (c) repeat adaptation yields byte-identical `DocumentView`. The test proves the clarified contract without changing public types.

### additional brief-shape note (known gap)

The brief as written implies a decision per-format (pdf, html, plain text, markdown) between four whole-library candidates. The decision is actually per-format × per-library, because two of the candidates (`docling`, `unstructured`) are multi-format but uneven in quality per format, and the best pick is a **mix** (thin in-repo for text/markdown/html; maybe a library wrap for PDF). The brief's success criterion captures this — "a specific library (or 'ship nothing') per format" — so there's no contradiction. Flagging only so the coordinator does not assume the research would rule on a single library for all formats.

---

## What this doc does not cover (explicit gaps)

- **DOCX, XLSX, PPTX, email, epub, image+OCR:** out of scope per v1 priority order. Each will need its own sub-evaluation before being added to `extras/*`.
- **Async performance** for any candidate: not measured; all four are sync-core.
- **HTML throughput** head-to-head: not cited with numbers. Known to be ms-range for all four; not a differentiator.
- **unstructured's `unstructured-ingest` and `unstructured-inference`:** separate products; not evaluated. The open-source `unstructured` library was the scope.
- **docling's remote-serving extra:** not evaluated; out of scope for a default in-process adapter.
- **marker's LLM-mode enhancements (`--use_llm`):** not evaluated; would introduce a second LLM call inside the adapter, violating the "adapter does not call LLMs" implicit boundary of seam A.
- **Specific CVE history of MuPDF (the PyMuPDF vendored engine):** only the PyMuPDF wrapper's GHSA list is verified. Upstream MuPDF CVEs were not enumerated. Could-not-verify.
- **Exact release dates** on some PyMuPDF releases: the GitHub page rendered some dates ambiguously ("March 20, 2025" vs "2026"); the order of tags is correct but absolute calendar dates should be double-checked before citing in an ADR.
- **docling-parse internals**: the docling PDF backend's own charspan assignment was not fully traced; only the HTML backend's `_make_prov` was confirmed to set `charspan=(0, len(text))`. The PDF backend (docling_parse_backend.py) does not contain `charspan`, which suggests PDF items receive `charspan` somewhere upstream in docling-parse or are defaulted elsewhere. This should be confirmed before any ADR that treats docling's PDF charspan as meaningful data.

---

## Recommendation branches

- **Go (ship nothing, in-repo compositions):** if the coordinator accepts that seam A's "byte-addressable" invariant rules out all four candidates as turnkey defaults, write `docs/adr/0003-default-document-adapter.md` recording the "ship nothing in extras/; ship in-repo compositions in extractx/source/adapters/" decision, and queue `docs/tasks/seam-a-{html,text,markdown,pdf}-adapter.md` for each format with explicit byte-offset proof targets.
- **Investigate further (relaxed-contract path):** if the coordinator accepts the §8 pushback and clarifies seam A to treat `(page_no, bbox)` as the source anchor for paginated formats, spawn `docs/tasks/seam-a-contract-clarification.md` to land the §7/§9 doc edits, then `docs/tasks/select-pdf-adapter-relaxed.md` to re-pick between `docling` and a `pypdfium2` composition under the clarified contract.
- **Pass (on unstructured specifically):** remove `extras/unstructured/` from `docs/tasks/bootstrap-project-skeleton.md` line 40 and 43. Unstructured is a fine library; it just does not meet seam A as written, does not fit the MIT-only posture worse than docling does, and its format breadth is not load-bearing for v1 priorities.
