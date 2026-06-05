# extractx architecture

a schema-first grounded extraction engine. given one already-scoped document and an extraction spec, produce typed, grounded, replayable observations and evidence with byte-addressable source provenance, typed negative outcomes where grounded observation is not possible, and replay artifacts sufficient to reconstruct every decision.

extractx is an **extraction engine**. verification, truth, and materialization live elsewhere.

extractx is not a domain correlation engine. it does not decide that an extraction instance is an invoice, tax return, account, patient, matter, or other business entity. core emits `Extraction` objects containing `Instance`s, `Evidence`, and `Observation`s. each `Instance` has an extraction-level `instance_id`; consumers map those extraction instances to their own domain identifiers outside core.

---

## 1. goal

desired state:

> `(DocumentView, ExtractionSpec, Runtime)` → `Extraction`
> where every `Observation` is bounded to known ids, every sealed `Evidence` carries byte-addressable source evidence (plus optional layout geometry and multi-span context), every absence is a typed outcome, every decision is replayable under pinned producers, and cardinality semantics are contractually enforced end to end.

stopping condition for the rebuild:
- every seam has a contract test enforcing its invariants
- replay artifact bytes round-trip deterministically and source-driven replay reproduces the captured `Extraction` under the replay equality helper
- same spec runs on serial and async executors with identical results
- multi-instance documents produce per-instance evidence without id collision, cardinality smear, or normalized-value dedup loss
- budget exhaustion mid-run yields `partial` result with typed negatives, not an exception
- iterative extraction preserves accepted evidence when a later field in the same instance fails
- `extract(document, schema, *, runtime=None, store=None, capture_interviews=False)` is the schema-first end-user entrypoint; `run_extraction(document, spec, runtime, policy)` remains the explicit engine path
- a pydantic schema class is the primary user-facing declaration surface

---

## 2. first principles

posture axioms. every seam invariant below derives from one of these.

1. understand before changing — locate the seam first, then the contract, then the code
2. simplify — prefer one canonical path per concern
3. verification owns truth; soft compute proposes
4. schema declares *what*, never *how*
5. deterministic producers enumerate; soft compute classifies among grounded candidates *or* proposes grounded candidates directly under soft-compute discipline
6. candidate generation ≠ observation ≠ adaptation ≠ validation ≠ resolution
7. observation returns ids only, never values
8. public extraction lifecycle is explicit: `Candidate` → `Observation` → `Evidence` → `Instance` → `Extraction`; internal sealing may still use `ProposedField` and `ValidatedField`
9. normalized value ≠ evidence text ≠ source span
10. cleaned-text offsets are an internal coordinate; every resolved evidence carries a byte-addressable source anchor and may carry multi-span evidence and layout geometry
11. audit trail ≠ runtime bus
12. soft compute lives at one or more named producer seams, never smeared across consumers
13. canonical objects and derived projections are explicitly classified; canonical wins on conflict
14. every extension point is one protocol; no runtime registries, no ambient discovery
15. provider quirks must not shape public contracts
16. fail loudly when contracts are violated — produce typed negative outcomes, not silent absence
17. execution strategy is orthogonal to seam contracts — strategies compose seams, they do not replace them
18. reuse the ecosystem where it has already converged — pydantic for schemas, opentelemetry for tracing, pydantic-ai for llm-backed agents with state (message history, resume, interview); do not reinvent
19. cardinality is contract, not convention — `Cardinality.ONE` creates exactly one synthetic instance with no instance candidate strategy or proposer, while `Cardinality.MANY` requires instance candidate strategy and instance proposer bindings before extraction begins
20. final instance assignment has a single named owner (`G.resolver`) and a documented precedence rule; `F.layer3` runs only after final assignment
21. **pass through operational metadata; do not reshape it.** if a subsystem (provider SDK, parser, model response) emits a usable structured shape for usage, cost signals, finish reasons, or parser metadata, carry it through raw or as a minimal typed projection with an untranslated passthrough field. do not build abstractions that try to normalize pricing, layout, or provider quirks — consumers who want derived facts (cost in dollars, time-to-first-token, page structure stats) compute them from the raw shape. this rule applies to **operational metadata only**. semantic public types (`Candidate`, `Observation`, `Evidence`, `Instance`, `Extraction`, etc.) remain fully typed and provider-agnostic — provider quirks stay behind their seam (see principle 15).

---

## 3. product boundary

| owns | does not own |
|---|---|
| document adaptation with source-anchor preservation (plus optional layout geometry) | canonical truth |
| spec declaration (ExtractionSpec + FieldSpec, built from pydantic classes) | benchmark labels |
| candidate generation (deterministic) | downstream materialization |
| grounded evidence generation (soft compute, optional alternate path) | workflow orchestration beyond one run |
| grounded observation (soft compute, id-only) | acceptance lifecycle (optional external plugin) |
| evidence adaptation | domain-specific source adapters |
| three-layer evidence validation | cost / pricing (users bring their own pricing source against `UsageEvent`) |
| instance planning + resolution with pluggable grouping | downstream business state |
| assignment of `Instance.instance_id` within one extraction run | domain entity identity (`return_id`, account id, case id, etc.) |
| iterative and independent extraction strategies | the output shape itself (pydantic owns it) |
| replay artifacts | exemplar acceptance / promotion |
| usage-signal emission via `Budget` seam (raw passthrough) | |
| inspectable extraction plans / dry-run JSON as derived previews | execution plans as authority |

extractx starts once the **source unit is already known**. one run operates on one already-scoped source unit (for example: one pdf, one html document, one image, or one explicitly ordered image/page series that already constitutes one logical document). **source formation is upstream**: grouping raw assets into a source unit, deduplicating them, and deciding their ordering when it is not intrinsic are not extractx responsibilities. extractx owns adaptation, localization, extraction, validation, and resolution **within** that scoped source unit.

extractx also stops before **domain correlation**. `Instance.instance_id` is an extraction-level grouping handle, not a business identifier. a consumer may map one `Instance` to a `returnId`, account id, case id, or any other domain identity, but that mapping is consumer-owned. natural-key rules, cross-document merge, entity lifecycle, dedup across source units, and business-specific conflict resolution are downstream responsibilities.

**core v1 defers to sibling packages** for:
- domain source adapters (web_forms, clinical, invoices) — implement `DocumentAdapter` in sibling packages
- exemplar emission — sibling `extractx_exemplars` reads `Extraction` + `ReplayArtifact`; core does not emit
- remote executors (modal, ray, dask) — available in `extras/*` but not part of the v1 end-user public pact
- pricing / cost-in-dollars computation — users bring their own pricing source (provider invoice, tokencost, internal table) against the `UsageEvent` stream

user-facing schema classes are **pydantic `BaseModel` subclasses**. extractx owns the extraction machinery; pydantic owns the output shape, coercion, and serialization.

---

## 4. canonical vocabulary

the only nouns. no synonyms, no aliases.

**data objects**
`DocumentView`, `AnchorMap`, `SourceSpan`, `SourceRef`, `PageRef`, `BoundingRegion`
`ExtractionSpec`, `FieldSpec`, `StrategyBinding`, `ValidationBinding`, `GroupingBinding`, `PromptBinding`, `FilterBinding`, `InstanceCandidateStrategyBinding`, `InstanceProposerBinding`, `GroupingPolicy`
`Candidate`, `CandidateSet`, `Observation`, `ContextPack`, `RenderedPrompt`
`ProposedField`, `ValidatedField`, `Evidence`
`NegativeOutcome`, `ValidationFailure`
`Instance`, `InstanceHint`, `InstanceState`, `InstancePlan`, `GroupingEvidence`
`Extraction`
`UsageEvent`, `InterviewTranscript`, `ReplayArtifact`, `ExecutionTrace`

**execution objects**
`Executor`, `Runtime`, `ExecutorPolicy`, `ExtractionPlan`, `ExtractionPlanStep`
`IndependentStrategy`, `IterativeStrategy`
`Reporter`, `Budget`

**protocol nouns**
`DocumentAdapter`, `CandidateStrategy`, `CandidateFilter`, `InstanceCandidateStrategy`, `GroundedProposalGenerator` (optional), `Observer`, `Prompt`, `InstanceProposer`, `InstancePlanner`, `InstanceResolver`, `Normalizer`, `FieldValidator`, `InstanceValidator`, `CandidateSorter` (optional), `AcceptanceLifecycle` (optional, outer)
`LLM`, `NLP`, `Fetch` (capabilities)

**schema surface (pydantic-native)**
`schema class` — the user's `pydantic.BaseModel` subclass declaring extraction targets
`extract_field()` — extractx's thin wrapper over `pydantic.Field` that carries typed extractx metadata
`ValueKind` — semantic tag attached to python types via `Annotated[pytype, ValueKind.X]`
branded types in `extractx.types` — `Money`, `Percent`, `Date`, `Org`, `Person`, `Gpe`, `Cardinal`, `Ordinal`, `Bool`, etc.

**vocabulary glossary** (distinguishing overlapping terms)
- *schema class* = user's pydantic BaseModel with `extract_field` metadata (what the output looks like)
- *ExtractionSpec* = extractx run configuration derived from the schema class (how extraction runs)
- *field_id* = the pydantic schema attribute name. pydantic aliases, downstream enum names, and domain identifiers do not change `FieldSpec.field_id`. consumers that maintain their own field vocabulary map at their adapter seam.
- *json schema* = pydantic's `.model_json_schema()` output, used for LLM tool-call prompts
- *candidate* ≠ observation ≠ evidence ≠ truth
- *observation* ≠ resolution ≠ materialization
- *InstanceHint* is an internal extraction-level instance id used as input to seam C under iterative fill to scope candidate generation to one tentative instance
- *tentative instance id* (during iterative fill or from planner) ≠ *final `Instance.instance_id`* (after resolution)
- *GroupingPolicy* = spec-level policy; *GroupingBinding* = field-level override; *GroupingEvidence* = stage-tagged evidence object for either planning or resolution
- *NO_CANDIDATES* = `CandidateSet` had zero candidates for the observer to choose from; *ABSTAINED* = `CandidateSet` had ≥1 candidate but the observer declined to pick — these are semantically distinct outcomes
- *producer_version* = `{model}|{prompt_template_hash}|{code_hash}` for soft producers; `code:{code_hash}` with null model/prompt for algorithmic producers
- *UsageEvent* = a typed passthrough envelope carrying the provider's raw usage object plus a minimal typed projection (input/output tokens, model_id, producer_version). extractx does not own pricing — it passes usage through; consumers derive cost
- *InterviewTranscript* = a sibling artifact (not embedded in `ReplayArtifact`) carrying the full pydantic-ai message history for one soft-compute call, per field, per instance, per attempt. enables `.interview()` on `Extraction`. captured only when `ExecutorPolicy.capture_interview_transcripts=True`
- *ExemplarCandidate* is outside core v1; truth acceptance lives in outer systems

---

## 5. seam map

twelve named seams (A–K), where C has a generation subphase and a filter subphase, and G is a two-phase seam: `G.planner` (early, optional) and `G.resolver` (late, always). two optional extension seams (`C.alt` grounded evidence, `M` acceptance lifecycle) are defined at the end of §7.

```
[A] DocumentAdapter          raw bytes + SourceRef → DocumentView
[B] Spec declaration         schema class (pydantic) → ExtractionSpec
[C] CandidateGenerator       FieldSpec + DocumentView [+ InstanceHint] → CandidateSet   (pure, deterministic)
[C.filter] CandidateFilter   CandidateSet + FieldSpec.filter_binding → CandidateSet    (pure, deterministic)
[D] Observer                 CandidateSet + ContextPack [+ InstanceState] → Observation    (soft compute, id-only)
[E] ObservationAdapter         Observation + CandidateSet + FieldSpec → ProposedField[]     (cardinality-aware)
[F] ProposalValidator        ProposedField → ValidatedField | NegativeOutcome | ValidationFailure  (layers 1, 2)
                             set[Evidence] within Instance.instance_id → layer 3    (runs after G.resolver)
[G.candidates] InstanceCandidateStrategy DocumentView + ExtractionSpec + CandidateSet[] → InstanceCandidateSet (required for `Cardinality.MANY`)
[G.proposer] InstanceProposer DocumentView + ExtractionSpec + InstanceCandidateSet → selected instance_id[] (required for `Cardinality.MANY`)
[G.planner] InstancePlanner  DocumentView + ExtractionSpec → tentative instance_id[]    (optional, may be soft)
[G.resolver] InstanceResolver all ValidatedField + all CandidateSet + InstancePlan → Instance[] + Evidence[]
[H] ReplayArtifactWriter     run outputs → ReplayArtifact                               (bytewise-reversible under pinning)
[I] Execution                Executor × Runtime × Strategy                              (internal machinery; graph is internal)
[J] Capability injection     Runtime → bound Protocols (incl. Budget, receives UsageEvents)
[K] Reporting                steps → Reporter (OTEL-semantic, write-only)

optional:
[C.alt] GroundedProposalGenerator  FieldSpec + DocumentView → ProposedField[]           (soft compute alternate to C+D)
[M]     AcceptanceLifecycle        Extraction → AcceptanceStates                  (outer integration seam)
```

soft-compute seams: **D** (always), **G.proposer** (when `Cardinality.MANY` uses `LLMInstanceProposer`), **G.planner** (when neural or soft), **G.resolver** (when neural or soft), **C.alt** (always, if used).

execution strategies are orthogonal — they compose the seams above in different orders. see §11.

`ExtractionPlan` is a derived inspection surface over these seams. static dry-run
compiles spec/runtime bindings and required capabilities; grounded dry-run also
adapts the document and runs deterministic candidate generation. dry-run never
calls soft producers, writes replay, mutates storage, or infers domain identity.
completed `Extraction` + `ReplayArtifact` remain canonical.

Observability has four distinct layers:

- typed run records are canonical facts: `Extraction.trace`,
  `Extraction.usage_events`, `ReplayArtifact`, and `RunManifest`
- extraction plans are typed derived projections answering what a run will
  attempt
- stdlib logging under the `extractx` logger tree is operational visibility at
  seam boundaries
- CLI output, including `--json`, is presentation of typed objects

Library code must not configure logging handlers, formatters, or levels.
Default logs must not include raw document text, candidate text, prompt bodies,
model outputs, secrets, or provider credentials. Log records that are intended
for consumers should carry a stable `extractx_event` extra key; human messages
may change. See ADR-0016.

---

## 6. separation rules (contract axioms)

hard boundaries no seam may cross:

- schema declares *what*, never *how*
- deterministic producers enumerate; soft compute proposes; verification owns truth
- candidate generation is not observation
- observation is not truth
- observation returns ids only, never values
- evidence is not materialization
- normalized value is not evidence text
- cleaned-text span is not source anchor
- audit trail is not runtime bus
- provider quirks may not shape public contracts
- soft compute must emit structured proposed objects at a named producer seam, never diffuse across consumers
- canonical and derived are explicitly classified
- extraction strategy selection lives in `ExecutorPolicy`, never inferred implicitly
- iterative fill never rolls back already-validated evidence; retries are per-step
- pydantic validators run on already-normalized values only; they never process raw text, candidates, or observer outputs
- `F.layer3` runs only after `G.resolver` has assigned final `Instance.instance_id`s
- cardinality semantics are enforced at `E` — the multiplicity of `ProposedField`s emitted from one `Observation` is determined by `FieldSpec.cardinality` (see §7 seam E)
- final instance assignment follows the precedence rule stated in seam `G.resolver` (§7); no other seam may make final instance decisions
- core does not mint exemplars, acceptance states, or truth labels; those live in outer systems
- **core does not own pricing.** `Budget` receives `UsageEvent`s with raw provider usage passthrough; cost-in-dollars is computed by consumers against their own pricing source, not by extractx

---

## 7. contracts

each seam uses the audit shape: **owner / producer / consumer / inputs / outputs / invariants / hidden**.
"hidden" answers: what stays behind the seam and must not leak to consumers.

### seam A — DocumentAdapter

- **producer:** `DocumentAdapter` impl
- **consumer:** `CandidateGenerator`, `GroundedProposalGenerator`, `InstancePlanner`, `Reporter`
- **in:** raw bytes, `SourceRef`
- **out:** `DocumentView{document_id, normalized_text, anchor_map, source_ref, metadata}`
- **invariants:**
  - seam A adapts **one already-scoped source unit**. it does not decide which raw assets belong together as a document; if multiple assets (pages, images, rendered sheets) are presented here, their grouping and ordering into one logical source unit was decided upstream
  - `anchor_map` is a total function from **UTF-8 byte offsets into the UTF-8 encoding of `DocumentView.normalized_text`** to `SourceSpan`s. domain values are UTF-8-aligned (code-point boundaries); misaligned offsets are outside the contract domain and must not be produced by adapters or consumers. all offsets in this contract — `anchor_map`'s domain and `SourceSpan.byte_*` under both `text_anchor_space`s — are byte offsets; for `normalized_text`-space spans, they are the *same* byte offsets as `anchor_map`'s domain. the returned `SourceSpan`s' `text_anchor_space` is determined by the adapter and consistent across all spans produced by that adapter instance. mapping may be many-to-one; one-to-many is not supported — adapters pick a canonical representative when normalization expands (ADR-0006)
  - adapters declare one of two subcontracts under this protocol:
    - **linearizable** (plain text, byte-preserving HTML, markdown with offset tracking): spans carry `text_anchor_space="source_bytes"`; `byte_*` address the raw source bytes identified by `source_ref.content_hash`; reverse lookup to source bytes is meaningful.
    - **paginated-visual** (PDF, scanned documents, image-based formats): spans carry `text_anchor_space="normalized_text"`; `byte_*` address the UTF-8 encoding of `DocumentView.normalized_text`; source bytes are not meaningfully addressable per logical content; `page_ref` and `bounding_region` carry visual provenance where applicable.
  - an adapter's subcontract is declared implicitly by the `text_anchor_space` of its produced spans; an adapter must not mix subcontracts within one `DocumentView`
  - every `SourceSpan` produced carries `byte_start` / `byte_end` in the coordinate space declared by `text_anchor_space`. `page_ref` and `bounding_region` are orthogonal visual locators, attachable to any `SourceSpan` where visual provenance is meaningful. forensic addressability is format-appropriate: linearizable spans reverse-map to source bytes; paginated-visual spans locate via normalized-text byte offset plus page / bounding region.
  - adapter never loses forensic addressability
  - normalization of the same source is deterministic and idempotent
  - repeated adaptation of identical `(raw_bytes, SourceRef)` yields byte-identical `DocumentView`
  - `DocumentView` contains no non-deterministic fields — no wall-clock timestamps, no random uuids; `document_id` is either explicit in `SourceRef` or derived from a content hash
  - **parser metadata passthrough:** if the adapter wraps a parser library (unstructured, docling, pymupdf, marker, etc.), the parser's native metadata object (or its serializable form) is attached under `DocumentView.metadata["parser"]` unchanged — not reshaped. consumers who need layout statistics or page structure read it from there (principle 21)
- **hidden:** parser library choice, whitespace heuristics, encoding detection, boilerplate rules

### seam B — Spec declaration

- **producer:** user / spec loader, typically via `ExtractionSpec.from_pydantic(SchemaCls)`
- **consumer:** executor, generators, observer, validator, planner, resolver
- **in:** a pydantic schema class (`type[BaseModel]` with `extract_field` metadata), optional policy overrides
- **out:** `ExtractionSpec{fields, prompt_policy, validation_policy, grouping_policy, budget, version, source_schema_ref}`
- **invariants:**
  - spec is immutable at run time
  - `version` is a content hash of all field declarations + policies + dependency graph + pydantic schema shape
  - spec is portable: same spec runs against any runtime
  - no runtime configuration leaks into spec (spec is about *what*, runtime is about *how*)
  - dependency graph over `FieldSpec.depends_on` is acyclic — spec loader rejects cyclic specs with `SpecError` at construction time
  - field priority and dependency order are explicit; iterative fill order is derived from the spec, never from ad hoc heuristics
  - `from_pydantic` is a pure function of the schema class; the same class always produces the same `ExtractionSpec`
  - `instance_type` is schema/spec-owned. `from_pydantic(SchemaCls)` defaults it to `SchemaCls.__name__`; callers may override it when the extraction vocabulary should differ from the class name. llm producers receive it as bounded schema context and never author it.
  - `Cardinality.ONE` uses exactly one synthetic extraction instance and no instance candidate strategy or proposer. `Cardinality.MANY` requires `instance_candidate_strategy_binding` and `instance_proposer_binding`; unsupported executor paths fail loudly before extraction.
  - manual construction of `ExtractionSpec` remains available for users who do not use pydantic
  - `SpecError` triggers: cyclic `depends_on`, pydantic `field_validator` that attempts to parse raw text (see anti-pattern §15 "Pydantic-as-Extractor"), invalid `ValueKind`s, missing required bindings, manual `FieldSpec` with `validation_binding=None` and no pydantic class to fall back on, `PromptPolicy.candidate_overflow_policy == "truncate_sorted"` with any `FieldSpec.sorter_binding is None` (ADR-0005)
- **hidden:** introspection mechanics, binding resolution, `extract_field` metadata extraction

### seam C — CandidateGenerator

- **producer:** `CandidateStrategy` impl (regex | ner | clause | table | hybrid)
- **consumer:** `Observer`, `InstanceResolver`
- **in:** `FieldSpec`, `DocumentView`, optional `InstanceHint`
- **out:** `CandidateSet{field_id, document_id, instance_hint?, candidates, strategy_id}`
- **invariants:**
  - referentially transparent: same `(FieldSpec, DocumentView, InstanceHint)` → same `CandidateSet`
  - no network, no llm, no hidden external effects
  - no mutable cross-run state that changes outputs for the same inputs (compiled regex and loaded nlp pipelines are allowed)
  - every `Candidate.source_span` is valid under `DocumentView.anchor_map` **according to its `text_anchor_space`** (ADR-0006):
    - for `text_anchor_space="normalized_text"`: `byte_start` and `byte_end` are in `anchor_map`'s domain (UTF-8-aligned byte offsets into `normalized_text.encode('utf-8')`, with `byte_end <= len(...)`).
    - for `text_anchor_space="source_bytes"`: the span must be recoverable from `anchor_map` by inversion over one or more normalized-text byte offsets.
  - every `Candidate.evidence_spans[i]` is valid under the same rule
  - all spans emitted by a `CandidateStrategy` for a given `DocumentView` share the `DocumentView`'s `text_anchor_space`
  - `candidate_id` is a deterministic hash of `(strategy_id, source_span, evidence_spans, normalized_structural_payload)` — never a call counter, never a uuid4
  - candidate_ids are unique within a `CandidateSet`
  - **no dedup by normalized value, ever.** dedup identity is strategy-specific and must preserve evidential distinctness
  - a candidate carries everything an observer needs to choose between it and its peers, including multi-span evidence for tables and footnotes
  - seam C output is canonical and full. strategy-owned truncation under `PromptPolicy.candidate_overflow_policy` produces a bounded view for seam D; the original `CandidateSet` is unchanged and still consumed in full by `G.resolver` and `ReplayArtifact` (see ADR-0005)
- **hidden:** regex source, model weights, tokenization, scoring heuristics, pattern libraries

### seam C.filter — CandidateFilter

- **producer:** field-level `FilterBinding`
- **consumer:** `Observer`, `InstanceResolver`
- **in:** `CandidateSet`, `FieldSpec.filter_binding`
- **out:** filtered `CandidateSet`
- **invariants:**
  - filters run after candidate generation and before seam D
  - the full filter expression is evaluated against the generated `CandidateSet`
  - filter declarations are typed, serializable pydantic predicate ASTs; no callables and no string DSL
  - filter expressions participate in `ExtractionSpec.version` and `SpecSummary`
  - filters may inspect sibling candidates in the same `CandidateSet` for span predicates such as `ContainedBy` / `Contains`
  - scalar predicates use `Candidate.normalized_hint` when present and otherwise fall back to the shared candidate-level scalar coercion helper. this keeps C.filter aligned with seam-C producers without running seam-F field validation early
- **hidden:** evaluator implementation and any later optimization strategy

### seam D — Observer (soft compute)

primary soft-compute seam. §8 applies in full.

- **producer:** `Observer` impl (algorithmic or llm-backed; default llm-backed ships as `PydanticAIObserver` in `extras/pydantic_ai/`). the id-only contract (`Observation.evidence_id is None or Observation.evidence_id in input_candidate_ids`) is enforced by the extractx wrapper on top of pydantic-ai's `output_type`
- **consumer:** `ObservationAdapter`, `Reporter`, `Budget` (via `UsageEvent`)
- **in:** `FieldSpec`; candidate summaries (id, text, context, entity_type, structured_payload, evidence_span_count) **derived from `CandidateSet`**, possibly bounded when `ContextPack.candidate_overflow` is non-`None` (see ADR-0005); `ContextPack`; optional `InstanceState` (iterative strategy only)
- **out:** `tuple[Observation, ...]`; for llm-backed producers, also emits a `UsageEvent` to the executor
- **invariants:**
  - observer returns ids only. every non-null `Observation.evidence_id` is in the bounded input candidate ids. no fabrication
  - absence is explicit: no input candidates produces `NegativeOutcome("observation", "no_candidates")`; an abstaining observation uses `abstain=True` and `evidence_id=None`. these are distinct even when the downstream consequence is the same
  - observer sees no ambient state beyond `ContextPack` and `InstanceState`
  - `producer_version` pins model + prompt_template + observer code_hash (mandatory). for pure algorithmic observers, `producer_version = "code:{code_hash}"` with model and prompt_template fields null
  - malformed output fails loudly at the seam — no silent coercion
  - retry count and termination reason are recorded by Reporter, not by Observer
  - observer does not compute or propose values outside of candidate ids
  - when `InstanceState` is provided, the observer may condition on prior validated evidence for the same instance, but never on unvalidated or hypothetical values
  - observer does not enforce cardinality — it may return any subset of ids; cardinality semantics are enforced at seam E
  - observer MAY inspect `ContextPack.candidate_overflow` and condition behavior (e.g., abstain under high truncation ratio). observer MUST NOT fabricate candidate ids outside the presented summaries — the id-only contract applies to the presented (possibly bounded) set, not the underlying full `CandidateSet` (ADR-0005)
  - llm-backed observers render their prompt via a `Prompt` implementation (see §9) — the prompt template is versioned and forms part of `producer_version`
  - `ValueKind.CATEGORY` fields may use deterministic selector backends such as `RuleBasedCategorySelector`. these backends still select among bounded literal candidates, emit canonical `Observation` objects, and surface replayable signal diagnostics; they do not create a separate classification truth object.
  - `ValueKind.CATEGORY` selectors may receive `ClassificationContextSet` evidence packets. these are non-selectable grounded windows used to decide among label candidates; they are sibling selector inputs to `CandidateSet`, not candidate sets themselves.
  - **provider response passthrough:** llm-backed observers emit a `UsageEvent` carrying the provider's raw usage object (unchanged) plus a minimal typed projection (tokens, model_id, finish_reason if available) to `Budget`. the raw response envelope is also captured by seam H for replay (payload contents stripped; metadata preserved)
  - **interview capture (opt-in):** when `ExecutorPolicy.capture_interview_transcripts=True` and the observer is pydantic-ai-backed, each call also emits an `InterviewTranscript` to the sibling interview artifact (never into `ReplayArtifact`). transcript carries the full pydantic-ai message history serialized via `ModelMessagesTypeAdapter`, including tool calls and returns. capture is field-scoped; see ADR-0004 for the rationale behind narrowing capture to seams D and C.alt only.
- **hidden:** llm provider, prompt text, temperature, sampling, tool-call plumbing, caching layer, retry internals

### seam E — ObservationAdapter (cardinality-aware)

- **producer:** `ObservationAdapter` impl
- **consumer:** `ProposalValidator`
- **in:** `tuple[Observation, ...]`, `CandidateSet`, `FieldSpec`
- **out:** `tuple[ProposedField, ...] | NegativeOutcome`

**cardinality semantics** — this is a contract, not a convention. given `k = count(non_abstaining observations for field_id)` and `c = FieldSpec.cardinality`:

| cardinality | no candidates / all abstained | `k = 0` with non-abstaining observations | `k = 1` | `k > 1` |
|---|---|---|---|---|
| `one` | `NegativeOutcome(category from outcome)` | `NegativeOutcome("adaptation", "empty_observation")` | one `ProposedField` | `NegativeOutcome("validation", "cardinality.one_expected_many_selected")` |
| `optional` | `NegativeOutcome(...)` | one `NegativeOutcome("observation", "abstained")` | one `ProposedField` | `NegativeOutcome("validation", "cardinality.optional_expected_many_selected")` |
| `many` | `NegativeOutcome(...)` | empty tuple `()` | one `ProposedField` in tuple | `k` `ProposedField`s in tuple |
| `per_instance` | handled per instance by iterative strategy; treated as `one` within each `Instance.instance_id` | — | — | — |

- **invariants:**
  - `ProposedField.source_span` is literally the selected `Candidate.source_span` — never synthesized
  - `ProposedField.evidence_spans` is literally the selected `Candidate.evidence_spans` — carried forward unchanged
  - `ProposedField.tentative_instance_id` is set to the tentative `Instance.instance_id` during iterative fill; final assignment happens at `G.resolver`
  - `ProposedField.normalized_hint` carries the selected `Candidate.normalized_hint` through unchanged. seam E does **not** produce a normalized value; normalization is seam F layer 2's exclusive responsibility. there is no `normalized_value` field on `ProposedField` — `ValidatedField.normalized_value` is the post-normalization surface.
  - adapter is pure — no normalization, no validation, no llm, no cardinality recovery beyond what the table above specifies
  - cardinality mismatches never silently coerce; they emit typed `NegativeOutcome`
- **hidden:** nothing material; mechanical shape-casting and cardinality mapping

### seam F — ProposalValidator (three layers, single normalization site)

**structural note:** layers 1 and 2 run per-`ProposedField` during fill. **canonical layer 3 runs per-`Instance` that reaches layer 3, after `G.resolver` assigns final `Instance.instance_id`s, exactly once per instance per run.** load-bearing.

- **producer:** `ProposalValidator`
- **consumer:** `InstanceResolver` (layers 1, 2), caller / `ExecutorPolicy` / `Reporter` (layer 3)
- **in:** `ProposedField` (layers 1, 2); `Instance` containing `Evidence`s (layer 3)
- **out:** layers 1, 2: `ValidatedField` | `NegativeOutcome` | `ValidationFailure`; layer 3: pass-through | `NegativeOutcome` | `ValidationFailure`

**layers:**

1. **candidate layer** — `source_span` and all `evidence_spans` valid under `anchor_map` according to each span's `text_anchor_space` (per seam C rules; ADR-0006). concretely: `normalized_text` spans require `byte_start` and `byte_end` to be UTF-8-aligned offsets within `normalized_text.encode('utf-8')`; `source_bytes` spans require a round-trip through `anchor_map`'s image. `structured_payload` shape valid. spans whose `text_anchor_space` is inconsistent with the `DocumentView`'s adapter subcontract fail with `NegativeOutcome("validation", "candidate.text_anchor_space_mismatch")`. UTF-8-misaligned `normalized_text` spans fail with `NegativeOutcome("validation", "candidate.utf8_alignment")`. other shape failures remain `NegativeOutcome("validation", "candidate.*")`. non-retryable.
2. **field layer** — the **single** normalization site.
   - for pydantic-backed specs: calls pydantic's type coercion on `ProposedField.raw_value`, then pydantic `field_validator`s for that field
   - for manual specs: calls `FieldSpec.validation_binding.normalizer(raw_value)` followed by declared `FieldValidator`s
   - success → emit `ValidatedField`
   - failure → `ValidationFailure` routed through `ExecutorPolicy`
3. **object layer** — cross-field consistency within one **final** `Instance.instance_id`.
   - pydantic `model_validator(mode="after")` functions run first on the materialized partial-instance view
   - extractx object validators registered with `@extractx_object_validator(...)` run after pydantic succeeds, receiving `{field_id: normalized_value}` and `{field_id: Evidence}` mappings
   - object-validator registration is schema-method based in v1; shared rule logic can be factored into helper functions called by decorated `@staticmethod`s
   - pydantic failure emits `ValidationFailure(layer="instance", ...)` directly and object validators do not run
   - object validators return structured `ObjectIssue`s rather than raising; returned issue `implicates` override decorator metadata, and missing issue `implicates` inherit from decorator metadata
   - `"error"` issues emit `ValidationFailure(layer="instance", object_issues=...)`, while `"warning"` issues are diagnostic and non-blocking in the independent strategy
   - layer-3 object-validation failures do not remove the instance; the executor appends a validation negative with `object_issues`, flips the instance to `partial`, and preserves existing evidence
   - failure → `ValidationFailure` routed through `ExecutorPolicy`

- **invariants:**
  - validators never mutate; they return decisions
  - normalization happens exactly once, at layer 2
  - **pydantic validators run here and nowhere else.** they never see raw text, candidates, or observer outputs
  - layer 1 failures are data defects and stop (non-retryable)
  - layer 2 failures are recoverable under the bounded `IterativeStrategy` field-repair slice; layer 3 object-validation failures are recoverable under the bounded `IterativeStrategy` object-repair slice
  - under the current bounded `IterativeStrategy`, layer 2 field failures retry the failed field once, then layer 3 object issues retry only implicated fields once; both retries append validator reasons to `ContextPack.retry_feedback`
  - canonical layer 3 is the sole instance-layer validation phase. under `IndependentStrategy` it runs exactly once per `Instance` that reaches layer 3. under the bounded `IterativeStrategy` repair slice it runs once for the initial resolved object and once for the repaired object when repair is attempted. no non-executor seam invokes layer 3 or its constituent validators.
  - layer 3 failures emit `ValidationFailure(layer="instance", ...)` routed through `ExecutorPolicy`. they do not trigger `G.resolver` reassignment.
  - validator precedence (pydantic-backed specs): pydantic `field_validator` is the default at layer 2; pydantic `model_validator(mode="after")` runs first at layer 3; extractx object validators run second and provide structured repair metadata
- **hidden:** validator internals, retry orchestration, normalizer implementation

### seam G.candidates — InstanceCandidateStrategy

the deterministic bounded-menu seam for `Cardinality.MANY`. this seam finds document-local candidate extraction instances before the soft proposer chooses which candidates are real.

- **producer:** `InstanceCandidateStrategy` impl (line grouping baseline | regex/defined-term | table row | hybrid)
- **consumer:** `InstanceProposer`, executor / strategy, replay
- **in:** `DocumentView`, `ExtractionSpec`, `tuple[CandidateSet, ...]`
- **out:** `InstanceCandidateSet{document_id, instance_type, candidates}`
- **invariants:**
  - only runs when `ExtractionSpec.instance_cardinality == Cardinality.MANY`
  - `Cardinality.ONE` bypasses this seam and creates the synthetic `instance_id="inst_0"` instance
  - referentially transparent: same `(DocumentView, ExtractionSpec, CandidateSet[])` yields the same `InstanceCandidateSet`
  - no llm, no network, no hidden external effects
  - `instance_type` is copied from `ExtractionSpec.instance_type`; the strategy never authors domain identity
  - every `InstanceCandidate.instance_id` is deterministic and unique within the set
  - every `anchor_spans` entry is valid under the `DocumentView` source-span contract
  - `anchor_candidate_ids` may reference field candidates that helped form the menu, but final field/evidence assignment remains seam D `Observation`
  - an empty set under `Cardinality.MANY` is an insufficient instance-candidate outcome, not a single-instance fallback
- **hidden:** regex source, table-row heuristics, heading detection, clause-block heuristics, scoring used to rank candidate anchors

### seam G.proposer — InstanceProposer

the multi-instance proposal seam for `Cardinality.MANY`. this seam selects document-local extraction instances from a bounded candidate set before field observation assigns evidence to fields.

- **producer:** `InstanceProposer` impl, phase-2 production default `LLMInstanceProposer`
- **consumer:** executor / strategy, observer
- **in:** `DocumentView`, `ExtractionSpec`, `InstanceCandidateSet`
- **out:** `InstanceProposerResponse{selected_instance_ids, reason?}`
- **invariants:**
  - only runs when `ExtractionSpec.instance_cardinality == Cardinality.MANY`
  - `Cardinality.ONE` bypasses this seam and creates the synthetic `instance_id="inst_0"` instance
  - bounded ids come from `G.candidates` `InstanceCandidateSet`; the proposer never authors `instance_id` or `instance_type`
  - `instance_type` is schema/spec-owned and comes from `ExtractionSpec.instance_type`
  - proposer output is narrow: selected instance ids only, plus diagnostic `reason`
  - field/evidence assignment remains seam D `Observation`; the proposer must not return per-field mappings
  - selected ids outside the candidate set, duplicate selected ids, malformed structured output, and provider failures fail loudly at the seam
  - an empty selected set is an insufficient instance proposal, not a silent single-instance fallback
  - every soft call captures document hash, spec version, instance candidate set hash, rendered prompt hash, model id, temperature, seed, and producer code hash for replay/forensics
  - replay is authority; any cache is a separate namespace
- **hidden:** prompt text, provider, sampling knobs, retry internals

### seam G.planner — InstancePlanner

an early, coarse pass that estimates instance count and produces tentative scaffolds before per-field extraction happens. optional under `IndependentStrategy` (skipped, single document-scope key synthesized), required under `IterativeStrategy`.

- **producer:** `InstancePlanner` impl
- **consumer:** `IterativeStrategy`, `InstanceResolver`, `Reporter`
- **in:** `DocumentView`, `ExtractionSpec`, bounded `ContextPack`
- **out:** `InstancePlan{tentative_keys, grouping_evidence, producer_version?}`
- **invariants:**
  - planner output is tentative. the resolver may merge, split, or drop instances at the end
  - `Instance.instance_id.group_id` is a deterministic hash of `(group_anchors, group_key_material)` — stable across runs for the same inputs and the same pinned planner
  - when the planner is soft, §8 soft-compute discipline applies in full: `producer_version` pinned, structured output validated at the seam, retries and termination reason recorded, replay fixtures captured, `UsageEvent` emitted
  - planner does not access per-field candidates; it works from structural evidence in the document (headings, sections, clause patterns, layout, etc.)
  - grouping evidence from the planner is emitted as `GroupingEvidence(stage="planned", ...)`; the same object type is used by `G.resolver` with `stage="resolved"` — there is no separate `PlanningEvidence` type
  - **boundary_defining field handling (tentative — revisit after first real multi-instance extraction):** under `IterativeStrategy`, fields whose `GroupingBinding.role == "boundary_defining"` run an advisory pre-plan C→D pass before the planner executes. pre-plan is strictly C→D — seams E and F are not invoked during pre-plan; selected candidate source_spans are looked up from `CandidateSet` by `candidate_id` and accumulated as planner anchors. canonical `ProposedField` / `ValidatedField` for boundary_defining fields comes from their per-instance run, not pre-plan.
  - **pre-plan orchestration outcomes emit trace events only.** abstentions, no-candidates, and candidate-overflow observed during pre-plan do not produce canonical `NegativeOutcome`s. they are recorded via `Reporter` for diagnostic access through `ReplayArtifact` + `ExecutionTrace`. canonical outcomes for boundary_defining fields come from their per-instance run. non-abstaining `Observation.evidence_id`s contribute advisory anchors in this phase.
  - **zero boundary_defining fields case:** if `spec.fields` contains no `boundary_defining` field, the pre-plan phase is skipped. planner runs on `(doc, spec, ())` — empty anchor sequence — and produces `InstancePlan` from structural signals alone.
  - **multiple boundary_defining fields:** ordering is `FieldSpec.priority` descending; ties broken by position in `ExtractionSpec.fields` (declaration order, stable). all selected anchors contribute jointly; planner merges.
  - **all-abstain fallback:** if every boundary_defining field abstains or yields no candidates in pre-plan, planner runs on empty anchor sequence (equivalent to zero-field case). this is not a canonical failure.
  - **divergence rule:** if per-instance observation of a boundary_defining field picks a different candidate than pre-plan, per-instance wins for the canonical `ValidatedField`. `G.resolver` reconciles final `group_anchors` from per-instance results. pre-plan anchors are tentative and advisory (consistent with "planner output is tentative" above).
  - **canonical failure mode: no tentative keys.** if `G.planner` cannot produce at least one tentative `Instance.instance_id` (zero anchors from pre-plan AND zero from structural signals), it emits `NegativeOutcome("planning", "no_tentative_keys")`. `InstancePlan.tentative_keys` is non-empty when an `InstancePlan` is returned.
- **hidden:** planning algorithm, anchor detection, (if soft) model choice and prompt

**known implementations:**
- `StructuralInstancePlanner` (default under `IterativeStrategy`) — deterministic, source-anchored, section/heading-based
- `GraphInstancePlanner` — graph-based clustering from structural signals
- `NeuralInstancePlanner` — asks a model "how many instances, what are their anchors?"; soft-compute

### seam G.resolver — InstanceResolver (final instance authority)

the late pass that assigns final `Instance.instance_id`s, promotes `ValidatedField`s into `Evidence`s, and reconciles tentative plans against actual evidence. **this is the single named owner of final instance truth.** always runs.

- **producer:** `InstanceResolver` impl
- **consumer:** `ProposalValidator.layer3`, caller (via `Extraction`), `ReplayArtifactWriter`
- **in:** all `ValidatedField`s + all `CandidateSet`s + optional `InstancePlan` + `GroupingBinding` per field
- **out:** `tuple[Instance, ...]` containing `Evidence`s

**precedence rule (load-bearing).** when signals disagree about final instance assignment, the resolver applies authorities in this order, higher overriding lower:

1. **explicit `GroupingBinding`** declared on a `FieldSpec` (`role="boundary_defining"` fields)
2. **source-anchor continuity** — fields whose `source_span`s lie within the same semantic block (section, clause, table row)
3. **candidate co-occurrence** — fields whose candidate `source_span`s cluster within the same local neighborhood under the binding's distance metric
4. **`InstancePlan` tentative scaffolds** — priors from the planner, lowest authority

if authorities 1–4 leave grouping ambiguous for a validated field, the resolver emits `NegativeOutcome("resolution", "ambiguous_grouping", field_id=<affected>, instance_id=<tentative>)` per affected field, attached to the tentative instance with the strongest partial signal from authorities 1–4 (deterministic tie-break via `tentative_key` ordering). the outcome lands in that tentative instance's `Instance.negative_outcomes`. the affected field does not become `Evidence`. instance-layer validation is canonical under seam F layer 3, post-resolution; the resolver does not invoke it.

- **invariants:**
  - resolver does not invoke `InstanceValidator`s or pydantic `model_validator`s. instance-layer validation is canonical under seam F layer 3, post-resolution. resolver does not retry or backtrack based on validator outcomes.
  - no candidate or `ValidatedField` is assigned to more than one final `Instance.instance_id`
  - final `Instance.instance_id` is stable across runs for the same inputs and the same pinned resolver
  - resolver may merge two tentative instances, split one tentative into two, or drop an instance that resolved to zero evidence
  - `GroupingEvidence(stage="resolved", ...)` is preserved in every `Instance`
  - when the resolver is soft, §8 soft-compute discipline applies in full
  - `cardinality=one` on a field with multiple instance-scoped evidence surfaces as `NegativeOutcome("resolution", "cardinality.*")` for that field within that instance
  - group boundaries are source-anchored — `group_anchors` must reference `SourceSpan`s, never cleaned-text heuristics. `group_anchors` may carry either `text_anchor_space`; the resolver does not require uniformity across a group. `Instance.instance_id.group_id` is a deterministic hash over `(group_anchors, group_key_material)`; serialization of `group_anchors` includes `text_anchor_space`, so spans with identical `byte_*` but different `text_anchor_space` produce different `group_id`s — correct, they are semantically different spans (ADR-0006)
  - `ValidatedField`s from fill are promoted into `Evidence`s with final `instance_id`; no mutation
  - normalized values may influence grouping only via declared `GroupingBinding`s on the relevant fields — implicit normalized-value-based grouping is forbidden
  - `producer_version` for algorithmic resolvers follows the same rule as algorithmic observers: `code:{code_hash}`, null model/prompt
- **hidden:** clustering algorithm, merge/split heuristics, (if soft) model choice and prompt

**known implementations:**
- `DeterministicInstanceResolver` (default) — anchor-based, structural, applies the precedence rule
- `GraphInstanceResolver` — graph partitioning over candidates + anchors
- `NeuralInstanceResolver` — soft-compute resolver

### seam H — ReplayArtifactWriter

- **producer:** `ReplayArtifactWriter` (executor-owned)
- **consumer:** offline debugger, replay harness, regression tests
- **in:** full `Extraction` + intermediate `CandidateSet`s + `Observation`s + selector-call diagnostics + `UsageEvent`s + `InstancePlan` + `InstanceState` versions (iterative only) + `CandidateOverflowMetadata` per bounded Observation (ADR-0005) + Reporter trace
- **out:** `ReplayArtifact`
- **invariants:**
  - replay v3 stores observation-shaped decisions under `observations` and selector-call diagnostics under `selector_call_diagnostics`, preserving the deterministic reconstruction path `Candidate -> Observation -> Evidence -> Instance -> Extraction` plus enough structural evidence to explain the selector call that produced each Observation
  - **replay mode determinism:** given pinned producer versions on the supported path, replay re-execution reconstructs an `Extraction` structurally equal to the captured result, excluding only `replay_artifact_ref`. `ExecutionTrace.events` participates in that equality and is typed to the phase-1 event shape: `tuple[NegativeOutcome, ...]`.
  - replay reader deserializes the typed `ExecutionTrace` directly; malformed or incompatible trace-event payloads fail loudly at the reader boundary.
  - `replay_artifact_ref` is the content hash of serialized artifact bytes, not a semantic idempotency key. consumers that need equivalence or dedup use `RunManifest.run_fingerprint` or their own downstream hash over stable fields.
  - **comparison mode (live providers):** live-provider reruns compare outputs and classify divergence (`prompt change | model change | code change | fixture drift`) — a signal, not a pass/fail
  - artifact never embeds provider-specific **raw payloads** (no prompt text, no raw llm response bodies with content)
  - artifact **does** embed provider-specific **operational metadata** — usage objects, finish reasons, response ids, model ids — via `UsageEvent` passthrough (principle 21). metadata helps diagnosis; payload would bloat and leak
  - selector-call diagnostics carry rendered prompt hashes/refs, prompt-local id maps, presented candidate ids, shard/reducer metadata, response hashes/refs, final Observations, and usage/model metadata; full prompt and response bodies stay behind refs when a recorder exists
  - artifact is append-only and immutable after write
  - artifact is self-describing: schema version + extractx version + all `producer_version`s used + pydantic model schema hash
  - iterative runs capture `InstanceState` at every version
  - persisted replay / source / spec objects sit behind `ExtractxStore`; logical refs are canonical, backend paths are adapter internals. phase 1 provides `LocalFilesystemStore` for `objects/source`, `objects/spec`, `objects/replay`, and `runs/`; cached `result`, sibling `interview`, and derived `views` storage remain deferred (ADR-0007)
  - default serialization backend is `msgspec` (internal detail, swappable via Writer protocol)
- **hidden:** serialization format, storage backend, compression

### seam I — Execution (Executor × Runtime × Strategy)

three orthogonal subseams. the graph is an internal execution plan, not a public type.

**I.1 — Executor**

- `SerialExecutor | AsyncExecutor` (v1 public). remote executors (`ModalExecutor`, `RayExecutor`, etc.) live in `extras/*` and are not part of the v1 end-user public pact
- owns concurrency, retry, budget enforcement, trace writing, manifest check, graph construction (internal)
- **invariants:**
  - executor is the only writer of `ExecutionTrace`
  - per-run `Budget` is enforced in the executor by consuming `UsageEvent`s from soft-compute producers; the executor never inspects pricing
  - step failures become `NegativeOutcome` + trace entry — never raw exceptions surfaced to caller
  - **determinism clause:** *same runtime bindings + same pinned producer artifacts + same inputs + same strategy → same `Extraction`*. live providers without pinning are explicitly out of scope
  - manifest key = `sha256(executor_id + spec.version + observer_producer_version + planner_producer_version + resolver_producer_version + strategy_id + document_id)`

**I.2 — Runtime**

- protocol resolver; binds declared protocols to implementations
- **invariants:**
  - no ambient attribute access. a step that does not declare `LLM` in its signature cannot call the llm
  - runtime is the only site where provider choice lives
  - same runtime + different executor = valid swap, identical result under pinning
  - `Runtime.from_env()` is a pure assembler: reads documented env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, provider routing overrides) and binds default capability impls. missing required capabilities raise `CapabilityError` at `Runtime` construction

**I.3 — Strategy**

- `IndependentStrategy | IterativeStrategy` — composes seams C–F–G into different orchestration orders
- selected via `ExecutorPolicy.strategy`; never inferred implicitly
- **not plugin-public in v1.** built-in strategies only; the protocol may be promoted to plugin-public in v2 if genuine need emerges
- **invariants:** strategy never changes seam contracts; it only chooses the order in which seams fire and whether `InstanceState` is maintained during fill. see §11

### seam J — Capability injection (incl. Budget)

- every step declares capabilities as typed protocol parameters: `LLM`, `NLP`, `Fetch`, `Budget`, `Reporter`
- runtime resolves and injects at executor-run time
- tests pass fakes by constructing a `Runtime` with fakes bound
- **invariants:**
  - a step cannot use a capability it did not declare
  - `Runtime` construction rejects specs that require unbound capabilities → `CapabilityError`

**Budget protocol (principle 21 — pass-through, no pricing):**

```python
class Budget(Protocol):
    def record(self, event: UsageEvent) -> None: ...
    def check(self) -> BudgetDecision: ...      # allow | deny_with_reason

    # optional: consumers that want cost-in-dollars subclass or wrap Budget
    # and compute cost from event.raw_usage using their own pricing source
```

- extractx ships a default `TokenCountBudget` impl that tracks input/output tokens against user-provided limits (no pricing)
- there is no default pricing impl in core or extras. users who want dollar-denominated budgets bring their own pricing source (provider invoice, `tokencost`, internal table) and subclass or wrap `Budget`

### seam K — Reporting (OpenTelemetry-semantic)

`Reporter` is a write-only protocol whose semantics mirror `opentelemetry.trace.Tracer`. spans, events, attributes, and baggage map 1:1.

- `ExecutionTrace` is the executor-owned run trace carried on `Extraction`. phase 1 stores a deterministic `trace_id` plus `events: tuple[NegativeOutcome, ...]`, emitted only for the supported-path failed-run diagnostic case. fuller OTEL span/event export remains the `Reporter` semantic target, not the landed phase-1 trace payload.
- default `EventSink` exports to any OTEL collector (honeycomb, grafana, datadog, jaeger, file)
- **invariants:**
  - steps emit through `Reporter`; the executor is the only reader and OTEL-exports on behalf of the run
  - no shared mutable state; reporter is write-only from the step's perspective
  - trace is append-only
  - seam correlation is stable across refactors via explicit span names and seam-id attributes

### optional seams

#### [C.alt] GroundedProposalGenerator (optional alternate to C+D)

for document classes where deterministic enumeration fails (complex tables, figures, multi-hop clause bindings), a neural producer may emit `ProposedField`s directly from `DocumentView`, bypassing the enumerate-then-select flow.

- **producer:** `GroundedProposalGenerator` impl
- **consumer:** `ProposalValidator`
- **in:** `FieldSpec`, `DocumentView`, bounded `ContextPack`, optional `InstanceState`
- **out:** `tuple[ProposedField, ...] | NegativeOutcome`; also emits `UsageEvent` for Budget
- **invariants:**
  - every emitted `ProposedField.source_span` (and `evidence_spans`) must be valid under `DocumentView.anchor_map` — no fabricated spans
  - `producer_version` pinned (§8 applies in full)
  - the generator does not normalize; `normalized_value = None`
  - use is opt-in per `FieldSpec.strategy_bindings`: a field binds one or more `CandidateStrategy` producers that are composed before selection
  - when this path is used, seam D is bypassed; `F` receives `ProposedField`s directly
- **hidden:** model choice, prompt, span-generation internals

#### [M] AcceptanceLifecycle (optional outer integration)

explicit plugin seam where outer truth-owning systems integrate with extractx without collapsing the evidence/truth boundary.

- **producer:** outer system impl of `AcceptanceLifecycle`
- **consumer:** outer system's own state
- **in:** `Extraction` (and optionally streaming `Instance`s)
- **out:** `AcceptanceStates` — outer system's state; extractx does not own the shape beyond a thin event interface
- **invariants:**
  - extractx never constructs `AcceptanceState` itself
  - the lifecycle is optional; a run without an `AcceptanceLifecycle` binding completes normally and emits `Evidence`s as the terminal public output
  - acceptance never feeds back into the current run
  - replay artifacts do not record acceptance state
- **hidden:** outer system internals entirely

---

## 8. soft-compute discipline

applies to seam D (always), seams G.planner and G.resolver (when either is soft), and seam C.alt (always, when used):

- **pin the producer.** `producer_version` is mandatory on every `Observation`, soft `InstancePlan`, soft `GroupingResult`, and `GroundedProposalGenerator` output
  - soft producer: `producer_version = "{model}|{prompt_template_hash}|{code_hash}"`
  - algorithmic producer: `producer_version = "code:{code_hash}"` with model and prompt_template null
- **validate at the seam.** malformed structured output fails loudly at the producer boundary
- **record retries.** retrying producers receive validator reasons through `ContextPack.retry_feedback`. the current bounded object-repair slice records extra selector observations in replay, any soft-compute usage as `UsageEvent`s, and structured operational logs. first-class successful retry trace events land with the trace-event widening thread.
- **record usage.** every soft-compute call emits a `UsageEvent` to `Budget` carrying the provider's raw usage object (unchanged) plus a typed projection (tokens, model_id, finish_reason). extractx does not price the event — the `UsageEvent` is the passthrough surface; pricing is the consumer's concern (principle 21)
- **replay fixtures.** producer input + output pairs are captured in `ReplayArtifact`. offline replay runs without live providers
- **drift investigation.** when replay diverges from live run, classify as `prompt change | model change | producer code change | fixture drift`
- **deterministic-first preference.** if an algorithmic producer suffices, prefer it
- **"deterministic" does not mean "regex soup."** the seam is implementation-agnostic; the contract is the commitment

---

## 9. canonical objects — necessary and sufficient

### `SourceRef`
```
source_id: str
content_hash: str
```

### `SourceSpan`
```
source_ref: SourceRef
text_anchor_space: Literal["source_bytes", "normalized_text"]
byte_start: int
byte_end: int
page_ref: PageRef | None
bounding_region: BoundingRegion | None
```

- `text_anchor_space` declares the coordinate space for `byte_start` / `byte_end`. required at construction; no default. (ADR-0006)
- `byte_start` / `byte_end` are half-open **byte** offsets (inclusive-start, exclusive-end) with `0 <= byte_start <= byte_end`.
- **`source_bytes`**: raw byte offsets into the bytes identified by `source_ref.content_hash`. alignment to the source's native encoding is the adapter's responsibility.
- **`normalized_text`**: UTF-8 byte offsets into the UTF-8 encoding of `DocumentView.normalized_text`, UTF-8-aligned (code-point boundaries). `byte_end <= len(normalized_text.encode('utf-8'))`. these are the **same byte offsets as `anchor_map`'s domain** (see seam A).
- UI consumers that hold a Python `str` must convert byte offsets to string offsets before slicing. `utf8_byte_span_to_char_range(text, span)` and `slice_utf8_byte_span(text, span)` are the public projections for UTF-8 text. They do not change span authority; they only project byte-addressed spans into Python-string coordinates for highlighting.
- `page_ref` and `bounding_region` are orthogonal visual locators. either may be non-`None` regardless of `text_anchor_space`.

### `PageRef`, `BoundingRegion`
```
PageRef:
  page_number: int
  page_size: tuple[float, float] | None

BoundingRegion:
  page_number: int
  polygon: tuple[tuple[float, float], ...]   # normalized coordinates in [0, 1]
```

### `DocumentView`
```
document_id: str
normalized_text: str
anchor_map: AnchorMap
source_ref: SourceRef
metadata: Mapping[str, Any]                  # includes parser native metadata under metadata["parser"]
```

### `ExtractionSpec`
```
fields: tuple[FieldSpec, ...]
instance_type: str = "ExtractionInstance"
instance_cardinality: Cardinality = Cardinality.ONE
instance_candidate_strategy_binding: InstanceCandidateStrategyBinding | None = None
instance_proposer_binding: InstanceProposerBinding | None = None
prompt_policy: PromptPolicy
validation_policy: ValidationPolicy
grouping_policy: GroupingPolicy
budget: BudgetSpec
version: str
source_schema_ref: SchemaRef | None
```

- `Cardinality.ONE` creates exactly one synthetic extraction instance, `instance_id="inst_0"`, and does not invoke or require an instance candidate strategy or proposer.
- `Cardinality.MANY` requires `instance_candidate_strategy_binding` and `instance_proposer_binding`; extraction fails with `SpecError` when either is absent.
- `instance_type` names the extraction-level schema/type for instances. schema-derived specs default it to the pydantic class name; manual specs default to `"ExtractionInstance"`.
- `InstanceProposer` is a multi-instance protocol only. no singleton proposer type exists.

### `InstanceCandidate`, `InstanceCandidateSet`, `InstanceProposerResponse`
```
InstanceCandidate:
  instance_id: str
  instance_type: str
  label: str | None
  anchor_candidate_ids: tuple[str, ...]
  anchor_spans: tuple[SourceSpan, ...]
  context: str

InstanceCandidateSet:
  document_id: str
  instance_type: str
  candidates: tuple[InstanceCandidate, ...]

InstanceProposerResponse:
  selected_instance_ids: tuple[str, ...]
  reason: str | None
```

- `InstanceCandidateSet` is the bounded id source for `Cardinality.MANY` instance proposal and is produced by `InstanceCandidateStrategy`.
- `InstanceProposerResponse` is intentionally narrow. it selects instance ids only; field/evidence assignment remains the `Observation` contract.

### `FieldSpec` (core + composable bindings)

core (always present):
```
field_id: str
description: str
value_kind: ValueKind
cardinality: Cardinality
priority: int
depends_on: tuple[FieldId, ...]
python_type: type
literal_values: tuple[str, ...]
```

composable bindings (optional):
```
strategy_bindings: tuple[StrategyBinding, ...]
validation_binding: ValidationBinding | None
grouping_binding: GroupingBinding | None
prompt_binding: PromptBinding | None
filter_binding: FilterBinding | None
sorter_binding: SorterBinding | None
```

nullable bindings default to `None`; `strategy_bindings` defaults to an empty
tuple. absence is a meaningful state, not a defect. later seams interpret
absence per their own contracts — e.g., seam B rejects manual `FieldSpec` with
`validation_binding=None` and no pydantic class fallback (§7 seam B `SpecError`
triggers); empty `strategy_bindings` is an executor-policy concern.

**cardinality inference from pydantic types:**

| pydantic annotation | inferred `Cardinality` |
|---|---|
| `X` (bare) | `Cardinality.one` |
| `X \| None` or `Optional[X]` | `Cardinality.optional` |
| `list[X]` (X is a submodel / pydantic BaseModel) | `Cardinality.per_instance` |
| `list[X]` (X is a scalar or non-model type) | `Cardinality.many` |
| explicit `cardinality=` in `extract_field(...)` | overrides inference |

### `StrategyBinding`, `ValidationBinding`, `GroupingBinding`, `PromptBinding`

```
StrategyBinding:
  cls: type[CandidateStrategy] | type[GroundedProposalGenerator]
  params: Mapping[str, Any]
  kind: Literal["candidate", "grounded_evidence"]

ValidationBinding:
  normalizer: Normalizer | None
  field_validators: tuple[FieldValidator, ...]

GroupingBinding:
  role: Literal["boundary_defining", "boundary_consuming", "neutral"]
  distance_metric: DistanceMetric

PromptBinding:
  template_id: str
  params: Mapping[str, Any]
```

`ValueKind` is semantic. It describes the expected field value. It does not
choose a candidate source. Candidate sources are attached explicitly through
`StrategyBinding`, so schemas can choose regex, NER, literal-set classification,
or future strategies without hidden framework policy.

Example: explicit spaCy NER binding for an invoice field:

```python
from typing import Annotated

from pydantic import BaseModel

from extractx import ValueKind, extract_field
from extractx.candidates import NerCandidateStrategy, NerEntityRulerConfig
from extractx.core import StrategyBinding


class InvoiceSummary(BaseModel):
    total_due: Annotated[str, ValueKind.MONEY] = extract_field(
        description="invoice total due",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                kind="candidate",
                params={
                    "model_id": "en",
                    "entity_rulers": (
                        NerEntityRulerConfig(
                            name="invoice_money",
                            patterns=(
                                {"label": "MONEY", "pattern": "$42.50"},
                            ),
                        ).model_dump(mode="json"),
                    ),
                    "entity_filter": ("MONEY",),
                },
            ),
        ),
    )
```

`model_id="en"` creates a blank English spaCy pipeline. Consumers that want a
pretrained spaCy model install that model separately and pass its package name
as `model_id`. NER candidates are `source_kind="text"` candidates, so they do
not auto-select by structural authority.

**role mechanics (tentative — revisit after first real multi-instance extraction):**
- `boundary_defining` — field's value contributes an anchor for instance boundaries. under `IterativeStrategy`, its candidates go through an advisory pre-plan C→D pass before `G.planner` runs; the selected candidates' `source_span`s are accumulated as planner anchors. the field also runs canonically per-instance; the per-instance result is canonical for its `ValidatedField`. see §7 G.planner for pre-plan rules and §11 for the iterative pseudocode.
- `boundary_consuming` — field is scoped to the detected instance but does not contribute anchors to the planner. runs per-instance only.
- `neutral` — no special role. runs per-instance only. behaves exactly as a field with no `GroupingBinding`.

### `SorterBinding` (ADR-0005)

composable binding on `FieldSpec.sorter_binding`; mirror of other bindings.

```
SorterBinding:
  cls: type[CandidateSorter]
  params: Mapping[str, Any]
```

### `PromptPolicy` (minimal v1 shape; ADR-0005)

```
PromptPolicy:
  candidate_overflow_policy: Literal["fail", "truncate_sorted"] = "fail"
  candidate_count_bound: int | None = None
  selector_prompt_max_chars: int | None = None
```

`selector_prompt_max_chars` is the ADR-0025 budget for batch selector prompt
planning. `None` preserves one-call batch behavior. When set, the executor packs
soft-selected field tasks into one or more batch selector calls under the
rendered prompt budget and fails before the provider call if a single field task
cannot fit. Document-level literal/category fields may opt into ADR-0034
budgeted document windows through `SelectorPromptPolicy`; that splits document
context rather than candidate menus and reduces the window decisions back to one
`Observation`.

### `CandidateOverflowMetadata` (ADR-0005)

plugin-public. attached to `ContextPack.candidate_overflow` when strategy has bounded the observer input.

```
CandidateOverflowMetadata:
  source_candidate_count: int             # full count in the source CandidateSet from seam C
  presented_candidate_count: int          # count shown to observer after bounding
  sorter_id: str                          # stable versioned identifier; follows code:{code_hash} discipline (§8)
  overflow_policy: Literal["truncate_sorted"]
```

### `ContextBudget` (minimal v1 shape; ADR-0005)

orthogonal to `PromptPolicy.candidate_count_bound`: `ContextBudget` is runtime/prompt-size bound surface; `candidate_count_bound` is spec-level policy. intentionally not collapsed.

```
ContextBudget:
  max_prompt_chars: int | None = None
  max_tokens: int | None = None
```

### `GroupingPolicy`
```
default_distance_metric: DistanceMetric
allow_parallel_instances: bool = False
max_instances: int | None = None
merge_threshold: float | None = None
```

### `Candidate`
```
candidate_id: str
text: str
source_kind: Literal["structured", "text"]
source_id: str
source_span: SourceSpan
evidence_spans: tuple[SourceSpan, ...]
context: str
entity_type: str | None
normalized_hint: Any | None
structured_payload: Mapping | None
structural_status: StructuralStatus | None
```

- `source_kind="text"` candidates are interpreted from natural language
  sources such as regex, NER, or prose-grounded producers. they never
  auto-select.
- `ValueKind.CATEGORY` + string `Literal[...]` fields use
  `LiteralSetCandidateStrategy` by default. the literal arms become
  structured candidates with synthetic zero-length document-head spans and
  `structured_payload={"literal": value}`.
- `source_kind="structured"` candidates come from source formats with typed
  payload identity, such as future structured records facts. they must carry
  `structural_status`.
- failed structured candidates remain selector-visible. hard malformed
  structured facts are source/parse diagnostics and do not become candidates.

### `ClassificationContextSet` / `ClassificationContextWindow` (ADR-0036)
```
ClassificationContextSet:
  field_id: FieldId
  document_id: str
  strategy_id: str
  windows: tuple[ClassificationContextWindow, ...]
  overflow: ClassificationContextOverflowMetadata | None

ClassificationContextWindow:
  window_id: str
  field_id: FieldId
  text: str
  source_kind: Literal["text"]
  source_id: str
  source_span: SourceSpan
  matched_terms: tuple[str, ...]
  strategy_id: str
  rank: int
  metadata: Mapping[str, Any]
```

classification context is non-selectable evidence shown to CATEGORY selectors.
it mirrors `CandidateSet` operationally (`field_id`, `document_id`,
`strategy_id`, stable item ids, text, source span, deterministic order, overflow
metadata) but does not reuse `Candidate` / `CandidateSet` types. label
candidates remain the only ids selectors may return.

### `StructuralStatus` / `StructuralFailure` (ADR-0013)
```
StructuralStatus:
  passed: bool
  contract_id: str
  failures: tuple[StructuralFailure, ...]

StructuralFailure:
  field: str
  actual: ConstraintValue
  expected: SetConstraint | RangeConstraint | PredicateConstraint
```

structured source contracts are ordinary pydantic models. contract evaluation
is `contract_class.model_validate(candidate_payload)`. extractx adapts
pydantic field metadata into the expected-constraint kernel; it does not scrape
error-message text and does not maintain a separate failure-code namespace.

invariants:
- `source_kind="text"` requires `structural_status is None`
- `source_kind="structured"` requires `structural_status is not None`
- `passed=True` requires `failures == ()`
- `passed=False` requires `failures != ()`

### `DeterministicSelectionGate` (ADR-0013)

between candidate-set construction and selector invocation:

```
eligible = structured candidates with structural_status.passed

if len(eligible) == 1 and not require_corroboration:
    auto-select eligible[0]
else:
    invoke selector over the bounded CandidateSet
```

source declaration order has no authority semantics. text candidates never
auto-select.

### Document Classification (ADR-0014)

document-level classification is expressed with pydantic `Literal[...]`
annotations and `ValueKind.CATEGORY`:

```
verdict: Annotated[
  Literal["receipt", "review", "irrelevant"],
  ValueKind.CATEGORY,
] = extract_field(...)
```

schema inference preserves the literal arms on `FieldSpec.literal_values`.
when a category field has literal values and no explicit strategy binding,
`from_pydantic` installs `LiteralSetCandidateStrategy`.

`LiteralSetCandidateStrategy` emits one structured candidate per literal arm.
the candidate is schema-grounded, not text-grounded:

```
source_kind: "structured"
source_id: "literal_set"
source_span: normalized_text[0:0]
normalized_hint: <literal>
structured_payload: {"literal": <literal>}
structural_status: passed=True, contract_id="literal_set_strategy_v1"
```

`ClassificationPrompt` is the prompt implementation for LLM-backed category
selection. it renders whole-document context plus the bounded literal candidate
set. `Selector` and `SelectorBinding` are unchanged.

### `CandidateSet`
```
field_id: str
document_id: str
instance_hint: InstanceHint | None
candidates: tuple[Candidate, ...]
strategy_id: str
```

### `InstanceHint`
type alias: `InstanceHint = Instance.instance_id`.

### `ContextPack`
```
schema_description: str
document_summary: str
field_context: Mapping[FieldId, str]
prior_evidence: tuple[ValidatedField, ...]
retry_feedback: tuple[ValidationReason, ...]
bounds: ContextBudget
candidate_overflow: CandidateOverflowMetadata | None
```

`candidate_overflow` is `None` when the observer is seeing the full `CandidateSet` from seam C; non-`None` when the strategy has bounded the view under `PromptPolicy.candidate_overflow_policy` (see ADR-0005).

### `Observation`
```
instance_id: str
field_id: str
evidence_id: str | None
abstain: bool = False
reason: str | None
producer_version: str
```

- `evidence_id` names a bounded candidate id before deterministic sealing promotes that candidate to `Evidence`.
- `abstain=True` requires `evidence_id is None`.
- `reason` is diagnostic only and never becomes raw value, normalized value, source span, evidence span, or materialized output.

### `SelectorExample` (eval fixture; ADR-0030)
```
document_id: str
field_id: str
field_summary: FieldSummary
candidate_set: CandidateSet
document_context: str
expected: ExpectedObservation
original_observation: Observation | None
metadata: dict[str, Any]
```

- `SelectorExample` is a portable seam-D fixture: field information + candidate menu + bounded document context plus curated expected selector output.
- it is not a replay artifact. replay answers "what happened"; selector examples answer "what should the selector return for this frozen decision."
- `field_summary` is used instead of live `FieldSpec` because `FieldSpec` carries Python type / callable references that are not JSONL-portable.
- extractx owns the fixture shape, JSONL load/export, and exact-match scoring. consumers own labels, datasets, domain miss classes, and optimizer frameworks.

### `SelectorDemoSet` and `SelectorPromptPolicy` (prompt assets; ADR-0030)
```
SelectorDemo:
  field_id: str
  document_context: str
  candidate_set: CandidateSet
  expected: ExpectedObservation
  note: str | None

SelectorDemoSet:
  demo_set_id: str
  version: str
  demos: tuple[SelectorDemo, ...]
  source: str
  description: str | None
  metadata: dict[str, Any]

SelectorPromptPolicy:
  instruction_ref: str | None
  demo_refs: tuple[str, ...]
  document_context_mode: Literal["full", "budgeted_windows", "classification_context"] = "full"
  document_window_overlap_chars: int = 1000
  document_reducer: DocumentClassificationReducerPolicy | None = None
  classification_context_binding: ClassificationContextBinding | None = None

DocumentClassificationReducerPolicy:
  strategy: Literal["priority", "union"] = "priority"
  priority: tuple[str, ...] = ()
```

- prompt assets are selector-owned operational guidance, not field definitions. `FieldSpec.description` says what the field means; selector demos/instructions say how a selector should choose among bounded candidates.
- `Runtime.selector_prompt_policies` maps `field_id -> SelectorPromptPolicy`; `Runtime.selector_prompt_assets` resolves refs to demo/instruction content.
- resolved prompt assets are rendered into selector prompts and included in `rendered_prompt_hash` / soft-call identity. changing resolved demos changes prompt identity, not semantic `spec.version`.
- `document_context_mode="budgeted_windows"` is for document-level literal/category classification. extractx renders one selector call per document window with the same literal candidate menu, then applies the typed reducer policy to produce one final `Observation`. `priority` reducers are for single-label fields; `union` reducers are for `Cardinality.MANY` multi-label fields. sync and deferred execution share the same planner; deferred only changes provider lifecycle.
- `document_context_mode="classification_context"` is for focused CATEGORY classification evidence retrieval. extractx renders label candidates plus the resolved `ClassificationContextSet`; it does not render those context windows as candidates.
- production refs should be immutable; floating aliases are experiment-only unless the resolved content hash is recorded with the run.

### `UsageEvent` (pass-through envelope; principle 21)
```
producer_version: str              # pins which producer emitted this
model_id: str | None               # null for algorithmic producers
input_tokens: int | None           # null when not applicable (e.g. algorithmic)
output_tokens: int | None          # null when not applicable
finish_reason: str | None          # provider-native string; not an enum
timestamp_ns: int                  # monotonic ns, for ordering
raw_usage: Mapping[str, Any] | None  # provider's native usage object, unchanged; None for algorithmic producers
```

- **necessary:** `producer_version` pins who emitted; tokens enable the default `TokenCountBudget`; `raw_usage` enables consumer pricing without reshape
- **sufficient:** consumers who want cost-in-dollars read `raw_usage` and apply their own pricing source. extractx does not translate
- **invariant:** `raw_usage` is never reshaped by extractx. it is attached as the provider emitted it, or `None` if the provider emitted none

### `InterviewTranscript` (sibling artifact; opt-in capture)
```
field_id: str
instance_id: Instance.instance_id | None
attempt_index: int                       # 0-based; increments across retries
producer_version: str                    # must match at rehydration or InterviewError
message_history_json: str                # pydantic-ai ModelMessagesTypeAdapter output
timestamp_ns: int
```

- **necessary:** identifies which soft-compute call this transcript captures (field × instance × attempt); pins producer for faithful rehydration; carries the full serialized message history
- **sufficient:** enables `.interview()` on `Extraction` — the message_history_json round-trips through `ModelMessagesTypeAdapter.validate_json()` into a `list[ModelMessage]` that pydantic-ai consumes as `message_history=` on a rehydrated agent
- **invariant:** never embedded in `ReplayArtifact` (see anti-pattern `Transcripts-In-Default-Replay-Artifact` §15). stored as a sibling artifact with independent retention, privacy, and transport policy. captured only when `ExecutorPolicy.capture_interview_transcripts=True`
- **invariant:** `producer_version` pin is enforced at interview time — attempting to interview against a runtime whose current `producer_version` does not match the captured transcript's raises `InterviewError`

### `Prompt` (protocol)
```
class Prompt(Protocol):
    def render(
        self,
        field_spec: FieldSpec,
        candidate_summaries: Sequence[CandidateSummary],
        context_pack: ContextPack,
        instance_state: InstanceState | None,
    ) -> RenderedPrompt: ...

    @property
    def template_hash(self) -> str: ...
```

### `RenderedPrompt`
```
messages: tuple[Message, ...]
structured_output_schema: Mapping | None
metadata: Mapping[str, Any]                  # template_id, template_hash, rendered_at_version
```

### `ProposedField` (post-observation, pre-normalization)
```
field_id: str
tentative_instance_id: Instance.instance_id | None
raw_value: str
evidence_text: str
source_span: SourceSpan
evidence_spans: tuple[SourceSpan, ...]
normalized_hint: Any | None
candidate_id_refs: tuple[str, ...]
strategy_id: str
observer_producer_version: str | None
grounded_producer_version: str | None
```

### `ValidatedField` (post-normalization)
```
proposed: ProposedField
normalized_value: Any
field_validation_version: str
```

### `Evidence` (public canonical)
```
field_id: str
instance_id: Instance.instance_id
raw_value: str
evidence_text: str
source_span: SourceSpan
evidence_spans: tuple[SourceSpan, ...]
normalized_value: Any
evidence_provenance: ProposalProvenance
```

### `ProposalProvenance` (public canonical)
```
strategy_id: str
candidate_id_refs: tuple[str, ...]
selector_producer_version: str | None
grounded_producer_version: str | None
```

`ProposalProvenance` is the v1 contract for tracing sealed `Evidence` back to
the extraction producer path. It is not validator provenance and not instance
grouping provenance.

### `NegativeOutcome`
```
category: Literal["observation", "validation", "budget", "resolution", "adaptation", "planning"]
code: str
field_id: str | None
instance_id: Instance.instance_id | None
reason: str
candidate_count: int | None
object_issues: tuple[ObjectIssue, ...]
```

### `ValidationFailure`
```
layer: Literal["candidate", "field", "instance"]
field_id: str
instance_id: Instance.instance_id | None
reason: str
producer_version: str | None
object_issues: tuple[ObjectIssue, ...]
```

### `ObjectIssue`
```
severity: Literal["warning", "error"]
code: str
reason: str
implicates: tuple[FieldRef, ...]
```

### `FieldRef`
```
field_id: str
candidate_id_refs: tuple[str, ...]
```

### `Instance.instance_id`
```
instance_id: str
```

`Instance.instance_id` is an extraction-scoped id. for `Cardinality.ONE`, it is the synthetic id `inst_0`. for `Cardinality.MANY`, it is proposed by the bound instance proposer and finalized by resolution. domain ids remain outside core.

### `InstanceState` (versioned, immutable per version)
```
instance_id: Instance.instance_id
version: int
accepted_evidence: tuple[ValidatedField, ...]
negatives_so_far: tuple[NegativeOutcome, ...]
unresolved_fields: tuple[FieldId, ...]
grouping_anchors: tuple[SourceSpan, ...]
```

### `Instance`
```
instance_id: Instance.instance_id
outcome: Literal["complete", "partial"]
field_evidence: tuple[Evidence, ...]
negative_outcomes: tuple[NegativeOutcome, ...]
grouping_evidence: GroupingEvidence
```

### `InstancePlan`
```
tentative_keys: tuple[Instance.instance_id, ...]
grouping_evidence: GroupingEvidence   # stage="planned"
producer_version: str | None
```

### `GroupingEvidence` (stage-tagged, unified)
```
stage: Literal["planned", "resolved"]
anchor_spans: tuple[SourceSpan, ...]
discriminators: tuple[GroupingDiscriminator, ...]
clustering_signals: Mapping[str, Any]
confidence: float | None
producer_version: str
```

### `GroupingDiscriminator`
```
field_id: str
candidate_id_refs: tuple[str, ...]
authority: Literal[
  "boundary_defining",
  "source_anchor_continuity",
  "candidate_cooccurrence",
  "instance_plan_prior",
]
```

`GroupingDiscriminator` is diagnostic. It explains which extracted fields and
candidate ids helped separate an extraction instance from its siblings. It is not
domain identity; consumers derive business ids from sealed `Evidence`.

### `Extraction`

canonical:
```
document_id: str
spec_version: str
outcome: Literal["complete", "partial", "failed"]
strategy: Literal["independent", "iterative"]
instances: tuple[Instance, ...]
trace: ExecutionTrace
replay_artifact_ref: ArtifactRef
```

### `ExecutionTrace`
```
trace_id: str
events: tuple[NegativeOutcome, ...]
```

phase-1 `events` is intentionally narrow: it carries only the supported-path diagnostic event shape emitted today, deterministic `NegativeOutcome`s surfaced when the executor rolls a run up to `failed`. richer reporter / OTEL span semantics remain behind seam K.

derived projections (methods):
```
.evidence() -> tuple[Evidence, ...]
.negatives() -> tuple[NegativeOutcome, ...]
.stream() -> AsyncIterator[Instance]
.to_pydantic(Cls) -> list[Cls]
.usage() -> tuple[UsageEvent, ...]           # all UsageEvents from the run, in order
.interview(*, field_id, instance_id=None, attempt_index=None, question) -> str
    # opt-in (requires ExecutorPolicy.capture_interview_transcripts=True at run time)
    # raises InterviewError if transcripts were not captured or producer_version mismatches
```

---

## 10. three-tier public surface

| tier | types | stability |
|---|---|---|
| **end-user public** | `extract`, `extract_one`, `run_extraction`, `ExtractionSpec` (via `from_pydantic`), `extract_field`, `Runtime`, `ExecutorPolicy`, `Extraction`, `Instance`, `Instance.instance_id`, `Evidence`, `NegativeOutcome`, `SourceSpan`, `ValueKind`, `Cardinality`, branded types in `extractx.types`, exception types (`SpecError`, `CapabilityError`, `InfrastructureError`, `InterviewError`, `ExtractionFailed`) | semver (main pact) |
| **plugin public** | `DocumentView`, `AnchorMap`, `PageRef`, `BoundingRegion`, `Candidate`, `CandidateSet`, `Observation`, `ContextPack`, `RenderedPrompt`, `UsageEvent`, `ReplayArtifact`, `InterviewTranscript`, `ProposedField`, `ValidatedField`, `InstanceHint`, `InstanceState`, `InstancePlan`, `GroupingEvidence`, `GroupingPolicy`, `FieldSpec` and all bindings, `Reporter`, `Budget`, `Executor`, `CandidateStrategy`, `GroundedProposalGenerator`, `Observer`, `Prompt`, `InstancePlanner`, `InstanceResolver`, `DocumentAdapter`, `Normalizer`, `FieldValidator`, `InstanceValidator`, `CandidateSorter`, `AcceptanceLifecycle`, capability protocols | semver (plugin pact, tracked separately) |
| **internal** | execution graph construction, manifest hashing, `ExecutionTrace` serialization format, `ReplayArtifact` on-disk format, retry orchestration internals, strategy implementations, pydantic introspection mechanics, msgspec serialization, `Strategy` protocol (v1) | may change without notice |

**pydantic versioning:** extractx pins to pydantic v2.x major. a v3 migration is a breaking release of the end-user pact.

---

## 11. execution model

three orthogonal concerns, three objects, one canonical code path.

- **`Executor`** — `SerialExecutor | AsyncExecutor`. owns concurrency, retry, budget, trace writing, manifest check, graph construction (internal)
- **`Runtime`** — protocol resolver. binds `LLM`, `NLP`, `Fetch`, `Budget`, `Reporter`, and any extension protocols
- **`Strategy`** — `IndependentStrategy | IterativeStrategy`

invariants:
- same runtime + same strategy + different executor = same `Extraction` (determinism clause under pinning)
- executor swap is the only local-vs-async split
- strategy is selected via `ExecutorPolicy.strategy`; never inferred

### extraction strategies

**`IndependentStrategy`** — parallel-per-field, independent decisions.

```
for each FieldSpec (parallel or any order):
    if field.strategy_bindings:
        candidates  = merge(C(binding, field, doc) for binding in field.strategy_bindings)
        observation, usage = D(candidates, field, ContextPack())
        evidence   = E(observation, candidates, field)
    else:                                                       # "grounded_evidence"
        evidence, usage = C.alt(field, doc, ContextPack())
    Budget.record(usage)
    for evidence in evidence:
        F.layer1(evidence) → F.layer2(evidence)
then:
    plan            = None
    final_instances = G.resolver(all ValidatedFields, plan=None, all CandidateSets)
    for instance in final_instances:
        F.layer3(instance)
```

**`IterativeStrategy`** — bounded repair now; planner-first later.

Current runnable support is intentionally narrow: for single-instance specs,
`ExecutorPolicy(strategy="iterative")` runs the canonical independent pass, then
performs at most one field-level repair round for layer-2 validation failures.
It retries failed fields over the original candidate sets with pydantic/manual
validator reasons in `ContextPack.retry_feedback`. After resolving, it evaluates
layer-3 object validators and, if error-severity `ObjectIssue`s implicate
fields, retries only those fields once with the issue reasons in
`ContextPack.retry_feedback`. It then resolves and validates again. Repairs do
not mechanically exclude rejected candidates or derive filters from validator
prose.

The future planner-first shape remains:

```
# pre-plan phase (strictly C -> D; no E, no F; trace-only orchestration outcomes)
boundary_defining_fields = [
    f for f in spec.fields
    if f.grouping_binding and f.grouping_binding.role == "boundary_defining"
]
# ordering: priority desc, declaration order for ties
boundary_defining_fields.sort(
    key=lambda f: (-f.priority, spec.fields.index(f))
)

boundary_defining_spans: list[SourceSpan] = []
for field in boundary_defining_fields:
    candidates = C(field, doc)
    ctx = build_preplan_context_pack(spec, field)  # rules: schema_description full,
                                                   # field_context current field only,
                                                   # prior_evidence (), retry_feedback (),
                                                   # bounds = full ContextBudget,
                                                   # candidate_overflow set by pre_D if bounded
    presented, overflow_meta = pre_D(field, candidates, ctx)  # ADR-0005 strategy pre-D check
    if presented is None:
        # overflow policy "fail" during pre-plan -> trace event only; no canonical negative
        Reporter.emit("preplan.candidate_overflow", field_id=field.field_id)
        continue
    observations, usage = D(presented_summaries(presented), field, ctx, instance_state=None)
    Budget.record(usage)
    selected = [obs for obs in observations if not obs.abstain and obs.evidence_id is not None]
    if selected:
        for obs in selected:
            cid = obs.evidence_id
            c = candidate_by_id(candidates, cid)
            boundary_defining_spans.append(c.source_span)
    else:  # abstained or no candidates
        Reporter.emit(
            "preplan.no_anchor",
            field_id=field.field_id,
            outcome="abstained_or_no_candidates",
        )
        # no canonical negative; continue with remaining boundary_defining fields
    # note: pre-plan does NOT invoke E or F; evidence are not produced in this phase

# planner runs on whatever anchors were accumulated — may be empty (zero-field or all-abstain)
plan, plan_usage = G.planner(doc, spec, tuple(boundary_defining_spans))
Budget.record(plan_usage)
# canonical failure: planner may emit NegativeOutcome("planning", "no_tentative_keys")
# if it cannot produce at least one tentative Instance.instance_id; handled per ExecutorPolicy.

all_validated = []
parallel_mode = spec.grouping_policy.allow_parallel_instances

for tentative_key in plan.tentative_keys (parallel if parallel_mode, else sequential):
    state = InstanceState(instance_id=tentative_key, version=0, ...)
    for field in topological_order(spec.fields, key=priority):
        ctx = ContextPack(prior_evidence=state.accepted_evidence, ...)
        if field.strategy_bindings:
            candidates       = merge(C(binding, field, doc, instance_hint=tentative_key) for binding in field.strategy_bindings)
            observation, usage = D(candidates, field, ctx, instance_state=state)
            evidence        = E(observation, candidates, field)
        else:
            evidence, usage = C.alt(field, doc, ctx, instance_state=state)
        Budget.record(usage)
        for evidence in evidence:
            F.layer1(evidence) → F.layer2(evidence)
            if accepted:
                state = state.with_accepted(validated)
                all_validated.append(validated)
            else:
                state = state.with_negative(negative)

final_instances = G.resolver(all_validated, plan, all_candidate_sets)
for instance in final_instances:
    F.layer3(instance)
```

- sequential within each instance; instances may run in parallel if `GroupingPolicy.allow_parallel_instances` is true
- each observation conditions on `InstanceState` containing prior validated evidence
- cross-field coherence is enforced at the decision site AND verified at layer 3 after resolution
- **retry discipline:** a failed observation at field N never rolls back fields 1..N-1 in the same instance. `ExecutorPolicy` retries field N with validator reason appended to `ContextPack.retry_feedback`; if retries exhaust, emits `NegativeOutcome` and proceeds to N+1
- **budget enforcement:** checked via `Budget.check()` before each soft-compute producer call. if denied, the current partial `InstanceState` is promoted to a `partial` `Instance`; remaining unresolved fields emit `NegativeOutcome("budget", "exhausted")`

### strategy observation guidance

| document shape | strategy |
|---|---|
| multi-instance, correlated fields (line-item groups, repeated forms, clinical arms) | `IterativeStrategy` |
| single-instance, strongly coupled fields | `IterativeStrategy` |
| independent scalar fields (invoices, receipts, simple forms) | `IndependentStrategy` |
| high-throughput, low-correlation batch | `IndependentStrategy` |

### manifest keying

manifest key = `sha256(executor_id + spec.version + observer_producer_version + planner_producer_version + resolver_producer_version + strategy_id + document_id)`.

---

## 12. schema surface (pydantic-native)

### declaration

```python
from pydantic import BaseModel
from extractx.types import Money, Percent, Date, Org
from extractx import extract_field

class LineItem(BaseModel):
    amount: Money     = extract_field(description="line item amount")
    tax_rate: Percent = extract_field(description="line item tax rate")
    due_date: Date    = extract_field(description="line item due date", depends_on=["invoice_date"])
    vendor: Org       = extract_field(description="vendor entity")

class InvoiceBatch(BaseModel):
    line_items: list[LineItem] = extract_field(description="one per line item")
```

### branded types

```python
from typing import Annotated
from decimal import Decimal
from datetime import date
from extractx import ValueKind

Money    = Annotated[Decimal, ValueKind.MONEY]
Percent  = Annotated[Decimal, ValueKind.PERCENT]
Date     = Annotated[date,    ValueKind.DATE]
Org      = Annotated[str,     ValueKind.ORG]
Person   = Annotated[str,     ValueKind.PERSON]
Gpe      = Annotated[str,     ValueKind.GPE]
Cardinal = Annotated[int,     ValueKind.CARDINAL]
Ordinal  = Annotated[int,     ValueKind.ORDINAL]
Bool     = Annotated[bool,    ValueKind.BOOL]
```

### extract_field

```python
def extract_field(
    *,
    description: str,
    cardinality: Cardinality | None = None,
    priority: int = 0,
    depends_on: Sequence[FieldId] = (),
    strategy_bindings: Sequence[StrategyBinding] = (),
    validation_binding: ValidationBinding | None = None,
    grouping_binding: GroupingBinding | None = None,
    prompt_binding: PromptBinding | None = None,
    sorter_binding: SorterBinding | None = None,
    default: Any = ...,
    **pydantic_field_kwargs,
) -> FieldInfo
```

### spec construction and materialization

```python
from extractx import ExtractionSpec, run_extraction

spec = ExtractionSpec.from_pydantic(ReceiptBatch)
result = await run_extraction(doc, spec, runtime, policy)

for instance in result.instances:
    line_item: LineItem = instance.to_pydantic(LineItem)

line_items: list[LineItem] = result.to_pydantic(LineItem)
```

### rules

1. the schema class is a pydantic `BaseModel`. no parallel `extractx.Schema` class exists
2. `ExtractionSpec` is not semantically a schema class
3. pydantic validators run at seam F layer 2 or layer 3. they never process raw text, candidates, or observer outputs. a `field_validator` that attempts to pull values from raw text is rejected at spec load time (`SpecError`)
4. `extract_field` metadata lives in a typed extractx container
5. users without pydantic can construct `ExtractionSpec` manually, provided every `FieldSpec` carries a non-null `ValidationBinding.normalizer`
6. `to_pydantic` is materialization, not extraction

---

## 13. public api surface

### schema-first happy path

```python
from pydantic import BaseModel
from extractx import extract, extract_field
from extractx.types import Money, Org, Date

class Invoice(BaseModel):
    total:  Money = extract_field(description="total amount due")
    vendor: Org   = extract_field(description="billing organization")
    date:   Date  = extract_field(description="invoice date")

result = await extract(document=doc, schema=Invoice)

for instance in result.instances:
    invoice: Invoice = instance.to_pydantic(Invoice)
```

`extract(document, schema, *, runtime=None, store=None, capture_interviews=False)` is the schema-first end-user entrypoint. It builds `ExtractionSpec.from_pydantic(schema)`, uses the supplied `Runtime` or a default `Runtime()`, builds `ExecutorPolicy(strategy="independent")`, and runs `SerialExecutor(storage=store)` internally. `store` is an `ExtractxStore | None`; callers construct concrete stores themselves. LLM-bound schemas should pass `runtime=Runtime(llm=...)`; missing capabilities fail before extraction begins.

### single-object materializing helper

```python
from extractx import ExtractionFailed, extract_one

try:
    invoice = await extract_one(document=doc, schema=Invoice)
except ExtractionFailed as exc:
    result = exc.result
```

`extract_one(document, schema, *, runtime=None, store=None, capture_interviews=False)` calls `extract(...)`, then materializes through `Extraction.to_pydantic(schema)`. it returns the single materialized pydantic object only when the run and its sole materialized instance are complete. failed, partial, zero-object, and multi-object outcomes raise `ExtractionFailed(result=...)`; the attached `Extraction` remains the authority for diagnosis.

### engine path

```python
from pydantic import BaseModel
from extractx import ExtractionSpec, run_extraction, Runtime, ExecutorPolicy
from extractx.types import Money, Org, Date
from extractx import extract_field

class Invoice(BaseModel):
    total:  Money = extract_field(description="total amount due")
    vendor: Org   = extract_field(description="billing organization")
    date:   Date  = extract_field(description="invoice date")

spec    = ExtractionSpec.from_pydantic(Invoice)
runtime = Runtime.from_env()
policy  = ExecutorPolicy(strategy="independent")

result = await run_extraction(document=doc, spec=spec, runtime=runtime, policy=policy)

for instance in result.instances:
    invoice: Invoice = instance.to_pydantic(Invoice)

# want cost? consumer computes it from raw usage:
for usage in result.usage():
    tokens_in  = usage.input_tokens or 0
    tokens_out = usage.output_tokens or 0
    # dollars = user's own pricing(model=usage.model_id, in=tokens_in, out=tokens_out)
```

`run_extraction(document, spec, runtime, policy)` remains the explicit engine path for plugin authors, tests, and advanced callers who need to supply the spec, runtime, and policy themselves.

### exception taxonomy

| exception | when | surface |
|---|---|---|
| `SpecError` | at `ExtractionSpec.from_pydantic()` or manual construction | raised at construction |
| `CapabilityError` | at `Runtime(...)` or `Runtime.from_env()` | raised at runtime construction |
| `InfrastructureError` | at `Executor` setup | raised at executor setup |
| `InterviewError` | at `Extraction.interview(...)` when transcripts were not captured, the transcript cannot be found, or `producer_version` does not match the current runtime | raised post-run, on `.interview()` call |
| `ExtractionFailed` | after `extract_one(...)` receives an `Extraction` but cannot return exactly one complete materialized object | raised by the materializing helper; carries `result` |

after the run begins, engine step failures, validation errors, budget exhaustion, and malformed soft-compute output all become typed `NegativeOutcome`s or `ValidationFailure`s routed through `ExecutorPolicy`. `ExtractionFailed` is a post-run convenience-surface exception: the canonical run evidence is still the attached `Extraction`.

### interview semantics (opt-in)

`Extraction.interview(field_id=..., instance_id=..., attempt_index=..., question=...)` rehydrates the full pydantic-ai conversation that produced the given field's observation and appends one follow-up question. `.interview()` is field-scoped by design; capture applies to seams D and C.alt only and does not extend to G.planner or G.resolver. requires `ExecutorPolicy.capture_interview_transcripts=True` at run time; otherwise `.interview()` raises `InterviewError`. the `InterviewTranscript` sibling artifact is pinned to the run's `producer_version` — interview only runs when the current runtime's producer_version matches (bytewise). see `docs/adr/0002-pydantic-ai-default-selector-and-interview.md` and `docs/adr/0004-narrow-interview-scope-to-field-seams.md`.

### streaming semantics

- **`IndependentStrategy`** — all instances flush at end of run
- **`IterativeStrategy`** — instances yield as they complete resolution
- live progress, per-field events, `InstanceState` versions, and trace entries flow through `Reporter` (OTEL), not through the result stream

### batch orchestration

out of scope. `extract(...)` and `run_extraction(...)` each handle one document.

---

## 14. extensibility map

| new capability | implements | lives in |
|---|---|---|
| source format | `DocumentAdapter` | `extractx.source.adapters` |
| domain source (web_forms, clinical, invoices) | `DocumentAdapter` | sibling package |
| candidate strategy | `CandidateStrategy` | `extractx.candidates.generators` |
| candidate sorter (reorders, doesn't drop) | `CandidateSorter` | `extractx.candidates.sorters` or sibling package |
| grounded evidence generator | `GroundedProposalGenerator` | `extractx.candidates.grounded` or sibling package |
| observer | `Observer` | `extractx.observation` or sibling package (default llm: `PydanticAIObserver` in `extras/pydantic_ai/`; enables interview capture natively via pydantic-ai message history) |
| prompt template | `Prompt` | `extractx.observation.prompts` or sibling package |
| instance planner | `InstancePlanner` | `extractx.instances.planners` or sibling package |
| instance resolver | `InstanceResolver` | `extractx.instances.resolvers` or sibling package |
| normalizer | `Normalizer` or pydantic `field_validator` | user code or domain package |
| field validator | `FieldValidator` or pydantic `field_validator` | user code or domain package |
| instance validator | `InstanceValidator` or pydantic `model_validator` | user code |
| executor (remote) | `Executor` | `extras/*` (not v1 public) |
| reporter sink | OTEL `SpanExporter` | user code |
| capability provider | the capability protocol | user code or adapter package |
| custom `ValueKind` | `Annotated[pytype, ValueKind.register("NAME")]` | user code or domain package |
| acceptance lifecycle | `AcceptanceLifecycle` | outer system |
| domain correlation (`Instance` → business entity id) | consumer adapter over `Extraction` + domain rules | sibling package / user code |
| exemplar emission (post-v1) | sibling package reading `Extraction` + `ReplayArtifact` | `extractx_exemplars` |
| benchmark / evaluation harness | sibling package calling real `extract(...)` or `run_extraction(...)` paths and classifying outputs against `Extraction` + `ReplayArtifact` | sibling package |
| **pricing impl (cost-in-dollars)** | subclass `Budget`; read `UsageEvent.raw_usage`; apply your own pricing source | user code |

`CandidateSorter` reorders without dropping. Under `PromptPolicy.candidate_overflow_policy = "truncate_sorted"`, the strategy invokes the sorter to produce an ordering and takes the bounded prefix; dropping is a typed strategy decision with a `CandidateOverflowMetadata` signal, not a sorter behavior. See ADR-0005.

**pricing is a user concern, not core.** extractx ships `TokenCountBudget` (counts in/out tokens against user limits) and exposes `UsageEvent.raw_usage` as passthrough. any dollarization is done by the user with their own pricing source.

**benchmark / evaluation discipline:** benchmark and exemplar tooling reuse the real public or engine entrypoints (`extract(...)` for schema-first harnesses, `run_extraction(...)` for explicit engine harnesses); they do not get a special execution path. eval-package scoring compares expected fixture instances to canonical `Extraction.instances` and emits typed exact misses (`missing_field | unexpected_field | value_mismatch | instance_count_mismatch`) before prompts, policies, or code are changed. richer miss interpretation belongs in later scorer extensions, not the runtime path.

**domain correlation discipline:** consumer adapters may use `Extraction.instances`, `Instance.instance_id`, grounded field evidence, and replay evidence to assign domain ids. core does not emit domain ids and does not own natural-key policies. if a pattern repeats across consumers, it may become a sibling correlation package; it still remains outside core until proven generic.

---

## 15. anti-patterns this design avoids

| anti-pattern | how blocked |
|---|---|
| Policy Trapped In Consumer | dedup, grouping, supersession, cardinality live in `InstancePlanner`, `InstanceResolver`, and `FieldSpec` bindings. `G.resolver` has an explicit precedence rule — it is not a policy sink |
| Canonical/Derived Smear | `instances` is canonical; `evidence()`/`negatives()`/`to_pydantic()`/`usage()` are explicit derived methods |
| Dual Normalization | normalization happens at exactly one site: seam F layer 2 |
| Raw-Payload Escape Hatch | `ContextPack` is typed. `extract_field` metadata lives in a typed container. `InstanceState` is typed and versioned. `RenderedPrompt` is typed |
| Silent None | invalid input produces typed `NegativeOutcome` or `ValidationFailure` |
| Duplicate Overlapping Path | no fallback observer, no alt path for local vs remote; materializing helpers compile through `extract(...)` rather than constructing a parallel sugar pipeline |
| Hidden Pre-Observation Filter | filtering between C and D is explicit as `FieldSpec.filter_binding`, typed as serializable predicate ASTs, and included in spec hashing. strategy-owned truncation under `PromptPolicy.candidate_overflow_policy` remains separate and visible through `ContextPack.candidate_overflow` (see ADR-0005 and ADR-0018) |
| Benchmark-Only Execution Path | evaluation and exemplar tooling call the real public/engine entrypoints (`extract(...)` for the schema-first path, `run_extraction(...)` for explicit engine harnesses) and consume `Extraction` / `ReplayArtifact`; no benchmark-only pipeline is canonical |
| Domain-Correlation Smear | core emits extraction-level `Instance.instance_id`s only. domain ids are assigned by consumers or sibling adapters after extraction, using `Extraction` and domain rules |
| Replay-Drift-Gate-Inversion | replay drift checks iterate captured `producer_versions` against live values. live keys absent from captured artifacts are not drift, which keeps drift-gate widenings compatible with older artifacts |
| Flattened Benchmark Miss | miss handling starts with typed classification (`true extraction miss | source absent | gold-label mismatch | fixture/resolution mismatch`), not a blanket "the model failed" bucket |
| Pydantic-as-Extractor | pydantic validators run at seam F on normalized values only; `field_validator` parsing raw text is rejected at spec load |
| Schema Class Competition | extractx does not define a parallel `Schema` base class |
| Lifecycle-Object Conflation | `ProposedField` → `ValidatedField` → `Evidence`. no single type carries three roles |
| Resolver-As-Truth-Owner | `G.resolver` follows a documented precedence rule; truth acceptance lives in optional `AcceptanceLifecycle` |
| Evidence-Spans-Undersized | all evidence-lifecycle types carry `evidence_spans: tuple[SourceSpan, ...]` |
| Ambient-Context-Bag | `ContextPack` is fully typed |
| **Reshape-Operational-Metadata** | provider usage, parser metadata, finish reasons, and response envelopes pass through raw (or as minimal typed projections with a `raw_*` passthrough field). extractx does not invent abstractions over subsystem outputs (principle 21). the `UsageEvent.raw_usage` field is the canonical passthrough shape |
| **Core-Owns-Pricing** | core ships no pricing tables, no cost-in-dollars translation, no `litellm` / `tokencost` dependency. `Budget` receives `UsageEvent`s; pricing is a user concern outside extractx |
| **Transcripts-In-Default-Replay-Artifact** | `InterviewTranscript` is a sibling artifact with independent retention, privacy, and transport policy. it is never embedded in `ReplayArtifact`, regardless of whether capture is enabled. replay is portable across CI, regression, and shared debugging; transcripts contain prompt content which may carry sensitive document excerpts and must not ride along by default |
| **Format-Silent-Span-Semantics** | `SourceSpan.byte_*` have explicit meaning via `text_anchor_space`; no format-specific interpretation is allowed to hide behind identical field names. `anchor_map` domain and `normalized_text`-space spans use the same UTF-8 byte-offset coordinate system (see ADR-0006) |

---

## 16. project layout

```
extractx/
  core/
    contracts.py          # all Protocols
    objects.py            # DocumentView, ExtractionSpec, FieldSpec (+ bindings),
                          #   Candidate, CandidateSet, Observation, ContextPack, RenderedPrompt,
                          #   Instance.instance_id, InstanceHint, InstanceState, InstancePlan,
                          #   GroupingEvidence, GroupingPolicy, UsageEvent
    outcomes.py           # ProposedField, ValidatedField, Evidence,
                          #   NegativeOutcome, ValidationFailure,
                          #   Instance, Extraction
    anchors.py            # AnchorMap, SourceSpan, SourceRef, PageRef, BoundingRegion
    cardinality.py        # Cardinality, GroupingPolicy helpers, inference table
    value_kinds.py        # ValueKind enum (registrable), canonical types
    versions.py           # content-hash helpers; producer_version composition
    dependencies.py       # FieldSpec dependency graph validation
    exceptions.py         # SpecError, CapabilityError, InfrastructureError, InterviewError, ExtractionFailed

  schema/
    types.py
    extract_field.py
    from_pydantic.py
    to_pydantic.py
    metadata.py
    validators.py         # enforces "pydantic-as-extractor" prohibition
    inference.py          # cardinality inference from pydantic types

  types.py                # top-level re-export of branded types

  source/
    document_view.py
    adapters/
      html.py             # generic (default)
      pdf.py              # generic (default)
      text.py

  candidates/
    candidate_set.py
    generators/
      regex.py
      ner.py
      clause.py
      table.py
      hybrid.py
    sorters/
      relevance.py
      layout.py
    grounded/
      neural.py

  observation/
    observer.py
    context_pack.py
    prompts/
      base.py
      observation.py
      grounded.py
    algorithmic/
    llm/                  # llm-backed default ships in extras/pydantic_ai/

  evidence/
    adapter.py            # ObservationAdapter (seam E), cardinality-aware
    validation.py         # ProposalValidator, 3 layers
    provenance.py

  instances/
    planners/
      structural.py       # default
      graph.py
      neural.py
    resolvers/
      deterministic.py    # default; applies precedence rule
      graph.py
      neural.py
    state.py
    plan.py
    grouping.py
    precedence.py         # precedence rule engine
    boundary.py           # boundary_defining pre-pass (tentative)

  replay/
    artifact.py
    writer.py             # msgspec default
    reader.py
    fixtures.py
    comparison.py

  execution/
    executor/
      protocol.py
      serial.py
      async_.py
    strategies/
      independent.py      # internal for v1
      iterative.py        # internal for v1
    policy.py
    runtime.py            # Runtime.from_env()
    budget.py             # Budget protocol + default TokenCountBudget (no pricing)
    reporter.py           # OTEL Tracer semantics
    manifest.py

  extras/
    pydantic_ai/
      observer.py         # PydanticAIObserver (default llm-backed; enables interview capture)
      interview.py        # InterviewTranscript capture + Extraction.interview() impl
    unstructured/
      adapter.py          # if chosen after research; alternative adapters may land here
    modal/
      executor.py         # not v1 public pact
    ray/
      executor.py         # not v1 public pact

  api.py                  # extract(), extract_one(), run_extraction()
  __init__.py

  cli/
    run.py
    replay.py
    inspect.py
```

**what this layout forbids, on purpose:**
- no `contrib/` with preconfigured pipelines
- no `settings.py` stringly-keyed `PIPELINES` registry
- no `@step(sinks=[...])` output coupling
- no string-keyed `Context` or ambient state bag
- no `rt.llm` ambient attribute access
- no `SpacyMoney` / `RegexMoney` backend-coupled field classes
- no domain source adapters in core
- no `ExemplarEmitter` or `AcceptedExemplar` in core v1
- no implicit strategy observation
- no `extractx.Schema` base class
- no filter seam between C and D
- no mutation of `ProposedField` / `ValidatedField` / `Evidence`
- no public `Strategy` extension protocol in v1
- no public `PipelineGraph` type
- **no pricing tables, cost-in-dollars translation, or `litellm`/`tokencost` dependency in core or extras**
- **no reshaping of `UsageEvent.raw_usage`** (principle 21)

---

## 17. what "done" means for the rebuild

| criterion | seam | proof level |
|---|---|---|
| one contract test per seam A–K + C.alt + M enforces its invariants | all | contract |
| `adapt(raw_bytes, SourceRef)` → byte-identical `DocumentView` across runs | A | unit + contract |
| `SourceSpan` round-trips with optional `page_ref` and `bounding_region` preserved | A | contract |
| parser metadata is attached to `DocumentView.metadata["parser"]` unchanged | A | contract |
| spec with cyclic `depends_on` raises `SpecError` at load | B | contract |
| spec with a pydantic `field_validator` that parses raw text raises `SpecError` | B | contract |
| manual `FieldSpec` with `validation_binding=None` and no pydantic class raises `SpecError` | B | contract |
| `ExtractionSpec.from_pydantic(Cls)` is pure: same class → same `spec.version` | B | contract |
| cardinality inference table applied correctly across `X`, `Optional[X]`, `list[X]` (scalar and model) | B + schema | contract |
| generator purity: no network, no llm, no mutable cross-run state | C | invariant |
| `candidate_id` deterministic across runs for identical inputs | C | contract |
| multi-span `Candidate` round-trips through E/F/G.resolver preserving all evidence spans | C, E, F, G | integration |
| observer returns only subsets of input ids; never fabricates | D | contract + property-based |
| observer distinguishes `NO_CANDIDATES` and `ABSTAINED` | D | contract |
| observer conditions on `InstanceState` correctly under iterative strategy | D | contract |
| `Prompt.render` is referentially transparent | D | contract |
| `Prompt.template_hash` changes when the template text changes and flows into `producer_version` | D + H | contract |
| **llm-backed observer emits `UsageEvent` with `raw_usage` attached unchanged** | D + J | contract |
| **`UsageEvent.raw_usage` is never reshaped by extractx; passed through exactly as the provider emitted** | J + H | invariant |
| cardinality table at seam E: each `(cardinality, k, outcome)` tuple produces the documented result | E | contract (table-driven) |
| normalization happens at exactly one site (seam F layer 2) | E, F | invariant |
| pydantic coercion is the default normalizer; custom normalizers plug in as `field_validator`s | F | contract |
| layer 3: pydantic `model_validator` runs first; extractx `InstanceValidator` runs after and only if pydantic passes | F | contract |
| `F.layer3` runs after `G.resolver` assigns final `Instance.instance_id`s | F, G | invariant + integration |
| under iterative, a mid-instance validation failure does not roll back prior validated evidence | F | integration |
| `InstanceState` is versioned and immutable per version | state | invariant |
| `ProposedField` / `ValidatedField` / `Evidence` are immutable across lifecycle stages | lifecycle | invariant |
| replay artifact bytes round-trip identically; source-driven replay reproduces `Extraction` under the replay equality helper, excluding only `replay_artifact_ref`; typed `trace.events` participates in equality | H | integration |
| **`ReplayArtifact` embeds `UsageEvent`s with `raw_usage` preserved; no raw payload content** | H | contract |
| **`ReplayArtifact.selector_call_diagnostics` records the presented selector candidate subset, prompt-local id maps, shard/reducer metadata, prompt/response refs or hashes, and final Observations without embedding raw prompt/response bodies** | H | contract |
| comparison mode classifies live-vs-replay divergence correctly | H | integration |
| same spec + doc on `SerialExecutor` and `AsyncExecutor` produce identical `Extraction` | I.1 | integration |
| same spec + doc + strategy, different executors → identical result | I.1 + I.3 | integration |
| multi-instance doc → multiple `Instance.instance_id`s, no id collision, no normalized-value dedup loss | G | contract + smoke |
| boundary_defining fields run first under `IterativeStrategy` and their spans flow into the plan | G.planner | integration |
| G.resolver precedence rule resolves contrived conflicts in the documented authority order | G | contract + integration |
| `InstancePlanner` swap preserves contract | G.planner | contract |
| `InstanceResolver` swap with same contract passes | G.resolver | contract |
| neural planner/resolver pinning and replay fixtures work identically to observer pinning | G + H | integration |
| algorithmic producer emits `producer_version = "code:{code_hash}"`; soft producer emits model + prompt + code | 8 | contract |
| `GroupingPolicy.allow_parallel_instances=True` runs instances concurrently under iterative | I.3 | integration |
| `GroupingPolicy.max_instances` violation emits `NegativeOutcome("planning", "max_exceeded")` | G.planner | contract |
| per-instance outcome: one run can contain `complete` and `partial` `Instance`s | result | integration |
| budget exhaustion mid-instance under iterative → `Extraction(outcome="partial")`; already-validated evidence preserved | I.1 | integration |
| **default `TokenCountBudget` tracks tokens against limits with no pricing** | J | contract |
| **`Budget` protocol does not import or depend on any pricing library** | J | invariant (static check) |
| `IndependentStrategy` and `IterativeStrategy` on same spec + doc produce distinct manifest keys and distinct artifacts | I.3 + H | integration |
| pydantic round-trip: `from_pydantic` → `run_extraction` → `result.to_pydantic(Cls)` produces valid `Cls` instances | schema + F | integration |
| `GroundedProposalGenerator` alternate path emits `ProposedField`s directly, bypasses seam D, passes seam F unchanged | C.alt + F | integration |
| `CandidateSorter` reorders without dropping | sorter + D | contract |
| `AcceptanceLifecycle` plugin receives `Extraction` without mutating it | M | integration |
| swap llm provider via Runtime → `Evidence` contract unchanged; `producer_version` changes; replay detects divergence | I.2 + D + H | contract + replay |
| `Runtime.from_env()` raises `CapabilityError` when a required capability is unbound | J | contract |
| `Reporter` output is valid OTEL: spans nest correctly, events attach to spans, attributes include seam ids and producer_versions | K | contract |
| **`InterviewTranscript` is never embedded in `ReplayArtifact`** (static check + integration) | H | invariant |
| **`.interview()` raises `InterviewError` when transcripts were not captured** | result | contract |
| **`.interview()` round-trips the message history via `ModelMessagesTypeAdapter`** and returns the rehydrated agent's answer to a new question | result + extras/pydantic_ai | integration |
| **`.interview()` raises `InterviewError` when runtime `producer_version` does not match captured transcript** | result | contract |
| `PydanticAIObserver` enforces the id-only contract on top of pydantic-ai's structured output (`Observation.evidence_id is None or in input_candidate_ids`) | D | contract |

---

## 18. shortest summary

extractx is a **evidence engine** with eleven named seams (A–K, where G is a two-phase seam) plus two optional extensions (C.alt, M).

every seam has a clear, necessary, and sufficient contract. soft compute is confined to named producer seams under pinned-producer discipline.

evidence lifecycle is explicit: `ProposedField` → `ValidatedField` → `Evidence`.

cardinality semantics are enforced at seam E. `NO_CANDIDATES` and `ABSTAINED` are distinct.

final instance assignment is owned by `G.resolver` alone, following a documented precedence rule. `F.layer3` runs only after. pydantic `model_validator` precedes extractx `InstanceValidator` at layer 3.

extraction strategy is orthogonal: `IndependentStrategy` for parallel independent fields, `IterativeStrategy` for sequential conditional extraction.

**operational metadata passes through raw.** `UsageEvent.raw_usage` carries the provider's native usage object unchanged. extractx ships no pricing, no cost-in-dollars translation, no `litellm` / `tokencost` dependency. consumers who want dollars compute them from `raw_usage` using their own pricing source. the `Budget` protocol receives `UsageEvent`s and decides allow/deny against user-defined limits (tokens, calls, or whatever).

semantic public types (`Evidence`, `Instance`, etc.) remain fully typed and provider-agnostic.

the schema surface is pydantic-native. cardinality inference from pydantic types is documented. validator precedence: pydantic first, extractx second.

the public api has a schema-first end-user result path, `extract(...)`, one single-object materializing helper, `extract_one(...)`, and an explicit engine path, `run_extraction(...)`. setup and post-run helper failures are exceptions; engine step failures are typed outcomes.

`Prompt` is a typed, versioned protocol used by llm-backed producers. `CandidateSorter` is the approved response when candidate count degrades observation — reorders without dropping.

`Extraction.outcome` is independent from per-`Instance` outcome — a single run may contain instances of mixed outcome.

ecosystem leverage: pydantic (schema + normalization), opentelemetry (reporting), pydantic-ai (default llm observer in extras, and the source of interview capability via `ModelMessagesTypeAdapter`), msgspec (replay serialization). no pricing libraries.

interview capability (opt-in): when `ExecutorPolicy.capture_interview_transcripts=True`, each **field-scoped** soft-compute call at seam D and seam C.alt emits an `InterviewTranscript` to a sibling artifact. capture does not apply at G.planner or G.resolver — `InterviewTranscript` is field-scoped by design (see ADR-0004). `Extraction.interview(field_id, instance_id, attempt_index, question)` rehydrates the exact pydantic-ai conversation and appends one follow-up question — you ask the agent "why?" and it answers in the context of its own prior decisions, not a simulation of them.

domain specificity lives outside core behind named protocols and consumer adapters. replay is reproducible under pinned producers: artifact bytes round-trip deterministically and source-driven replay reproduces the captured result under the replay equality helper. exemplars, truth acceptance, and domain correlation live outside core.

nothing in the core package knows which llm you use, which document format you parse, how much your tokens cost, which business entity a run should update, or where your domain truth lives.

that is the shape. build to the contracts.
