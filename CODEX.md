# CODEX.md

This file is the repo-local operating guide for coding agents working on extractx.

`AGENTS.md` defines the durable working model (seams, contracts, threads, proof doctrine). This file defines how this specific repo works.

Companion docs:

- [`AGENTS.md`](AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`docs/architecture.md`](docs/architecture.md) — the full system design (the north star; read this before touching code)
- [`docs/thread-orchestration.md`](docs/thread-orchestration.md) — how bounded worker threads and the coordinator interact
- [`docs/process/evidence-bundle.md`](docs/process/evidence-bundle.md) — standard worker completion report and proof gates
- [`docs/process/drift-log.md`](docs/process/drift-log.md) — cross-lane docs / wording / status drift rollup
- [`docs/adr/`](docs/adr/) — architectural decisions in chronological order
- [`docs/tasks/`](docs/tasks/) — work briefs handed to exec agents
- [`docs/research/`](docs/research/) — findings that inform decisions

## Repo Snapshot

- **Product:** a schema-first grounded proposal engine. given one already-scoped source unit and a pydantic schema class, produce typed `ResolvedFieldProposal`s with byte-addressable source provenance and replay artifacts sufficient to reconstruct every decision. extractx is a **proposal engine**; truth, acceptance, materialization, and source formation live elsewhere.
- **Primary stack:** python 3.12+, pydantic v2, asyncio, opentelemetry (reporting), msgspec (replay serialization), pydantic-ai (default llm-backed Selector in `extras/pydantic_ai/`; also powers `.interview()` via `ModelMessagesTypeAdapter` — see `docs/adr/0002-pydantic-ai-default-selector-and-interview.md`). document adapter default is under research — see `docs/tasks/select-default-document-adapter.md`. **no pricing libs in core or extras** per `docs/adr/0001-pass-through-operational-metadata.md` — `Budget` receives `UsageEvent`s; the default `TokenCountBudget` tracks tokens against user-provided limits, with no cost-in-dollars translation.
- **Main entrypoints:** `extract(document, schema, *, store=None, capture_interviews=False)` is the schema-first end-user result path; `extract_one(document, schema, *, store=None, capture_interviews=False)` is the single-object materializing helper and compiles through `extract(...)`; `run_extraction(document, spec, runtime, policy)` is the explicit engine path for advanced callers, plugin authors, and tests. all live in `extractx/api.py`.
- **Current maturity:** clean-slate rebuild. the old extractx lives at `../extractx-old` for reference only — do not import from it.

## Documentation Map

Start here, in this order:

1. [`AGENTS.md`](AGENTS.md) — generic doctrine (read first, always)
2. [`docs/architecture.md`](docs/architecture.md) — full system design, 18 sections, ~1400 lines. every seam and contract is defined here. **this is the single canonical source of truth for the architecture.**
3. [`docs/thread-orchestration.md`](docs/thread-orchestration.md) — how work is organized into lanes, threads, workers, evidence bundles, and drift handling
4. [`docs/process/evidence-bundle.md`](docs/process/evidence-bundle.md) — required worker completion report
5. [`docs/process/drift-log.md`](docs/process/drift-log.md) — docs / wording / status drift collected across lanes
6. `docs/adr/NNNN-*.md` — for each decision relevant to your work, read the ADR that established it
7. `docs/tasks/<slug>.md` — your own brief, if you were handed one

If `docs/architecture.md` and an ADR disagree, the architecture doc is **current truth**. ADRs are history. If they conflict about what should be true *now*, that's a bug — surface it rather than guess.

## Workflow Skills

When available in the local Codex skill registry, use these extractx workflow skills for repeated reasoning loops:

- `extractx-contract-work` — use before implementation when the question is seam ownership, contract sufficiency, or whether behavior belongs in extractx.
- `extractx-runtime-debug` — use for concrete run failures, logs, replay artifacts, candidate sets, deferred jobs, or provider lifecycle symptoms.
- `extractx-formalize-decision` — use when a converged design decision needs an ADR, architecture-doc update, implementation phases, or proof plan.
- `extractx-change-implementation` — use once the desired state is clear and the repo should be edited, tested, and summarized.

The intended reasoning order is: evidence → seam → contract → ownership → implementation → proof.

## Canonical Nouns

Use these nouns consistently. They are the public and plugin-public vocabulary defined in `docs/architecture.md` §4.

- `DocumentView`, `AnchorMap`, `SourceSpan`, `SourceRef`, `PageRef`, `BoundingRegion`
- `ExtractionSpec`, `FieldSpec`, `StrategyBinding`, `ValidationBinding`, `GroupingBinding`, `PromptBinding`, `GroupingPolicy`
- `Candidate`, `CandidateSet`, `Selection`, `ContextPack`, `RenderedPrompt`
- `ProposedField`, `ValidatedField`, `ResolvedFieldProposal`
- `NegativeOutcome`, `ValidationFailure`
- `InstanceKey`, `InstanceHint`, `InstanceState`, `InstanceResult`, `InstancePlan`, `GroupingEvidence`
- `ExtractionResult`, `UsageEvent`, `InterviewTranscript`, `ReplayArtifact`, `ExecutionTrace`
- `Executor`, `Runtime`, `ExecutorPolicy`, `Reporter`, `Budget`
- protocols: `DocumentAdapter`, `CandidateStrategy`, `GroundedProposalGenerator`, `Selector`, `Prompt`, `InstancePlanner`, `InstanceResolver`, `Normalizer`, `FieldValidator`, `InstanceValidator`, `CandidateSorter`, `AcceptanceLifecycle`

Do not blur:

- `text_anchor_space="source_bytes"` vs `text_anchor_space="normalized_text"` — distinct textual coordinate systems on `SourceSpan`; downstream code must dispatch on `text_anchor_space` before interpreting `byte_*` offsets (ADR-0006).
- `candidate` vs `proposal` vs `truth` — each is distinct. candidates are enumerated; proposals are grounded outputs; truth lives outside extractx.
- `selection` vs `resolution` vs `materialization` — selection picks candidate ids; resolution assigns instances; materialization is converting `ResolvedFieldProposal`s to the user's pydantic instances.
- `ProposedField` vs `ValidatedField` vs `ResolvedFieldProposal` — three lifecycle stages. no single type carries multiple roles.
- `NO_CANDIDATES` vs `ABSTAINED` — NO_CANDIDATES = `CandidateSet` was empty; ABSTAINED = ≥1 candidate but selector declined.
- tentative `InstanceKey` (from planner or during fill) vs final `InstanceKey` (after `G.resolver`).
- `schema class` (user's pydantic BaseModel) vs `ExtractionSpec` (run configuration).

### Conceptual frame: source-grounded observation

The unifying cross-modal concept behind extractx's lifecycle is **source-grounded observation**: a localization in source (`SourceSpan`) + something observed there + a claim derived from it + what it normalized to. `Candidate`, `ProposedField`, `ValidatedField`, and `ResolvedFieldProposal` are today's text-first manifestations of this pattern — their field names (`text`, `context`, `entity_type`, `raw_value`, `evidence_text`) read as NLP nouns because the v1 surface is linearizable + paginated text. This is honest for what the library does today. When future non-text modalities land (image regions, audio segments), they should preserve the **localization + observation + claim** shape — not invent a second provenance model. New types are preferable to renaming the current ones. This framing is docs language, not a code-level abstraction; the `Candidate` / `ProposedField` / `ResolvedFieldProposal` lifecycle remains canonical.

## Architecture Map

See `docs/architecture.md` §5 for the full seam map. Summary:

Main subsystems (each a directory under `extractx/`):

- `core/` — canonical objects, protocols, value kinds, versions, exceptions
- `schema/` — pydantic-native schema surface (`extract_field`, `from_pydantic`, `to_pydantic`, cardinality inference)
- `source/` — `DocumentAdapter` impls (generic html/pdf/text; domain adapters live in sibling packages). seam A adapts one already-scoped source unit; grouping raw assets into that unit is upstream.
- `candidates/` — `CandidateStrategy`, `CandidateSorter`, `GroundedProposalGenerator` impls
- `selection/` — `Selector`, `SelectionAdapter`, `Prompt` templates
- `proposals/` — `SelectionAdapter`, `ProposalValidator` (three layers, single normalization site)
- `instances/` — `InstancePlanner`, `InstanceResolver`, state, plan, precedence rule engine
- `replay/` — `ReplayArtifactWriter`, reader, source-driven re-execution, producer-version drift gate
- `execution/` — `Executor`, `Runtime`, strategies, policy, reporter (OTEL-native), budget, manifest

Canonical objects (authority):

- `ExtractionResult.instances: tuple[InstanceResult, ...]` is canonical. `.proposals()`, `.negatives()`, `.to_pydantic(Cls)` are derived projections.
- `ResolvedFieldProposal` is the per-field public canonical. `ProposedField` and `ValidatedField` are plugin-public intermediates in the lifecycle.

## Configuration Surface

Prefer typed external configuration or typed container objects over embedding configuration in code.

Good candidates already typed in the design:

- `ExtractionSpec`, `FieldSpec` and all bindings (typed pydantic containers; `extract_field` stores extractx metadata in a typed container, not pydantic's `json_schema_extra` dict)
- `GroupingPolicy`, `BudgetSpec`, `ExecutorPolicy`
- `ContextPack` (typed, not a raw dict)
- prompt templates live under `selection/prompts/` as `Prompt` implementations, not as scattered string concatenation

Keep code for:

- behavior (producers, validators, resolvers)
- dispatch (executor, runtime)
- computation (normalizers, validators, manifest hashing)
- typed readers and loaders (`from_pydantic`, `to_pydantic`)

Smells:

- a source file that is mostly data declarations — move to typed configuration
- prompt text assembled through string concatenation — move to a `Prompt` template
- implicit schema spread across multiple readers — consolidate

## Important Repo Seams

extractx has eleven named seams (A–K, where G is a two-phase seam G.planner + G.resolver) plus two optional extensions (C.alt grounded proposal, M acceptance lifecycle). Full contracts in `docs/architecture.md` §7. Summary of the ones most likely to fail first:

### `[D] Selector`

- owner of truth: `Selector` impl
- producer responsibility: pick candidate ids from the provided `CandidateSet`, never synthesize values
- consumer responsibility: `SelectionAdapter` (seam E) converts `Selection` to `ProposedField[]` per the cardinality table
- contract abstraction level: high-level semantic (grounded classification among enumerated candidates)
- contract: `selected_candidate_ids ⊆ input candidate_ids`; outcome is one of `SELECTED | AMBIGUOUS | ABSTAINED | NO_CANDIDATES`
- required inputs: `FieldSpec`, `CandidateSet` summaries (possibly bounded per `PromptPolicy.candidate_overflow_policy`), `ContextPack` (including `candidate_overflow: CandidateOverflowMetadata | None` signal), optional `InstanceState`
- required outputs: `Selection{outcome, selected_candidate_ids, reason?, producer_version}`
- missing information to watch for: confidence thresholds leak into prompts instead of being policy, candidate ordering assumptions that aren't load-bearing
- leaked information to avoid: provider name, prompt text, temperature, sampling details
- overflow discipline: no hidden truncation inside selectors; strategy bounds the view per declared policy with selector-visible signal (ADR-0005)
- preferred proof level: contract test + property-based (fabrication check)

### `[E] SelectionAdapter — cardinality-aware`

- owner of truth: `SelectionAdapter`
- contract: cardinality table in `docs/architecture.md` §7 seam E, load-bearing. `(FieldSpec.cardinality, len(selected_candidate_ids))` → exactly one outcome (`ProposedField` tuple or typed `NegativeOutcome`)
- preferred proof level: table-driven contract test

### `[F] ProposalValidator — single normalization site`

- owner of truth: `ProposalValidator`
- layer 1 — candidate shape; layer 2 — **single normalization**; layer 3 — cross-field, **runs after `G.resolver`**
- pydantic validators run here and nowhere else; a `field_validator` that parses raw text is rejected at spec load with `SpecError`
- preferred proof level: integration (layer ordering) + contract (precedence of pydantic vs extractx validators at layer 3)

### `[G.resolver] InstanceResolver — final instance authority`

- the single named owner of final instance assignment
- precedence rule (load-bearing): `GroupingBinding` (role=boundary_defining) > source-anchor continuity > candidate co-occurrence > `InstancePlan` priors. ambiguity after authorities 1–4 emits `NegativeOutcome("resolution", "ambiguous_grouping")`; resolver does not invoke instance-layer validators (see ADR-0003)
- preferred proof level: contract test over contrived conflicts, plus smoke test over real multi-instance docs

### `[K] Reporter — OTEL-semantic`

- write-only protocol with opentelemetry tracer semantics; each step boundary is a span, `Event` is a span event, `producer_version` is a span attribute
- preferred proof level: contract test (valid OTEL output shape)

Use this section to verify that each seam exposes **all and only** the information required for the next layer. if a seam needs implementation details from behind the boundary, the abstraction level is wrong.

## Commands

(none yet — project is in bootstrap. this section will be filled as the scaffolding lands.)

Expected shape:

- install: `uv sync`
- typecheck: `uv run pyright`
- lint: `uv run ruff check`
- format: `uv run ruff format`
- contract tests: `uv run pytest tests/contracts -v`
- integration tests: `uv run pytest tests/integration -v`
- smoke tests: `uv run pytest tests/smoke -v`
- all tests: `uv run pytest`

## Environment and Runtime

- local runtime: native python, asyncio; no docker required for core development
- test runtime: same as local; contract tests use fakes for `LLM`, `NLP`, `Fetch`
- deployed runtime: not applicable — extractx is a library, not a service

Environment and config sources:

- `.env` for local development (provider api keys for integration tests)
- `Runtime.from_env()` reads documented env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, provider routing overrides)
- no secret manager; users bind providers via `Runtime` at their call site

Important runtime rules:

- steps declare capabilities as typed protocol params; no ambient `rt.llm` access
- executor is the only writer of `ExecutionTrace`
- no step raises exceptions to the caller — all failures become typed `NegativeOutcome` or `ValidationFailure`
- five exception types: `SpecError` (construction), `CapabilityError` (runtime construction), `InfrastructureError` (executor setup), `InterviewError` (post-run `.interview()` call when transcripts were not captured, the transcript cannot be found, or `producer_version` does not match), `ExtractionFailed` (`extract_one(...)` cannot return exactly one complete materialized object and carries the full `ExtractionResult`)
- **pass operational metadata through** (principle 21): `UsageEvent.raw_usage` carries provider usage unchanged; `DocumentView.metadata["parser"]` carries parser native metadata unchanged. extractx does not reshape, normalize, or price operational metadata from subsystems. see `docs/adr/0001-pass-through-operational-metadata.md`.

## Workflow Rules

- **Branching:** feature branches off `main`; one thread per branch when practical
- **Commits:** small, coherent, contract-test-covered where seams are touched
- **PRs:** link the ADR (if any) or task brief that motivated the change; pre-commit hooks run ruff + pyright
- **Issues:** used for long-lived threads when shared memory is needed beyond a single conversation
- **Shared-state actions:** commits, pushes, merges, deploys, dependency changes — require explicit confirmation before executing. follow AGENTS.md shared-state rules.

### Git rules

- Never mention Claude, Codex, Anthropic, OpenAI, or any AI tool in commit messages, PR titles, PR descriptions, or Co-Authored-By lines. Git artifacts should read as if written by a human engineer.
- Never skip hooks (`--no-verify`, `--no-gpg-sign`, etc.) unless the user explicitly requests it.
- Prefer new commits over amending published commits.

## Debugging Workflow

When debugging extractx:

1. locate the failing seam from the error or failing outcome (use `NegativeOutcome.category` + `code` first; then the seam letter they map to)
2. inspect the `ReplayArtifact` for the failed run — it captures `CandidateSet`s, `Selection`s, `ValidatedField`s, pre-resolver negatives, final instances, usage events, producer versions, and trace
3. run source-driven replay via `replay_re_execute(artifact, store)` when the persisted source/spec are available. producer-version drift raises `InfrastructureError("replay.producer_version_drift: ...")`; the gate iterates captured keys, so legacy artifacts without newer keys still replay
4. trace the chain: `DocumentView` → `CandidateSet` → `Selection` → `ProposedField` → `ValidatedField` → `ResolvedFieldProposal`. identify the first place reality diverges from the expected contract.
5. check whether the seam contract is at the right abstraction level — is the seam carrying the information the next layer needs, and only that?
6. prefer the earliest honest proof for the seam (usually contract test)
7. only then widen scope

Known failure-prone areas (predicted; will be updated as incidents land):

- `G.planner` on multi-instance documents where instance boundaries are candidate-defined rather than structural (see architecture doc §7 G.planner, tentative `boundary_defining` mechanics)
- `G.resolver` precedence rule edge cases (contrived conflicts between authorities)
- `F.layer3` ordering (must run after `G.resolver`; if it runs before, it sees wrong `InstanceKey`s)
- seam E cardinality table — any coercion short-circuit bypasses typed negatives
- benchmark / exemplar misses — classify first (`true extraction miss | source absent | gold-label mismatch | fixture/resolution mismatch`) before changing prompts, policies, or code

## Forbidden Shortcuts

Do not:

- introduce an `extractx.Schema` base class that competes with pydantic `BaseModel`
- add a `CandidateFilter` or lossy pre-selection filter between seams C and D — use `CandidateSorter` or hierarchical selection instead (see anti-pattern §15 "Pre-Selection Filter")
- use pydantic `field_validator` to extract values from raw text — that work belongs at seams C and D; parsing in validators is rejected at spec load with `SpecError`
- normalize at seam E — normalization happens only at seam F layer 2
- mutate `ProposedField`, `ValidatedField`, or `ResolvedFieldProposal` after construction
- let `G.resolver` accumulate acceptance or truth-ownership logic — use the optional `AcceptanceLifecycle` seam
- bypass the canonical write path for `ExecutionTrace` (the executor is the only writer of the phase-1 trace payload; `Reporter` owns external OTEL-style reporting semantics)
- embed provider names, prompt text, or model identifiers in public types
- dedup candidates by normalized value anywhere (seam C invariant)
- **reshape operational metadata** from subsystems (provider usage, parser metadata, finish reasons) — pass through raw per principle 21; see anti-patterns `Reshape-Operational-Metadata` and `Core-Owns-Pricing` in architecture §15
- **ship pricing tables or cost-in-dollars translation in core or extras** — users bring their own pricing source against `UsageEvent.raw_usage`
- add `litellm`, `tokencost`, or any pricing library as a core or extras dependency
- skip hooks (`--no-verify`) on commits
- produce `SourceSpan`s whose `text_anchor_space` varies silently by format — the discriminator is canonical; downstream consumers depend on it (ADR-0006)
- add a benchmark-only or exemplar-only execution path — evaluation tooling must call real `extract(...)` or `run_extraction(...)` paths and read `ExtractionResult` / `ReplayArtifact`, not a hand-wired alternate pipeline

## Testing Notes

See `docs/architecture.md` §17 for the full "done" criteria table. Each seam has a contract test; some have property-based tests; integration tests cover multi-seam wiring; replay tests prove artifact byte round-trip, structural reconstruction, and source-driven replay equality under pinning.

Expected test lanes:

- `tests/contracts/` — one file per seam, enforces invariants
- `tests/integration/` — multi-seam wiring
- `tests/smoke/` — minimal end-to-end
- `tests/invariant/` — architecture-shape assertions
- `tests/replay/` — artifact byte round-trip, structural reconstruction, source-driven replay equality, and producer-version drift gates
- `tests/determinism/` — same inputs → same result across strategies and executors
- `tests/strategies/` — `IndependentStrategy` vs `IterativeStrategy` parity and divergence
- `tests/schema/` — pydantic round-trip: `from_pydantic` → run → `to_pydantic`
- `tests/cardinality/` — table-driven enforcement at seam E
- `tests/precedence/` — `G.resolver` precedence rule behavior under contrived conflicts
- `tests/lifecycle/` — `ProposedField` → `ValidatedField` → `ResolvedFieldProposal` immutability
- `tests/prompts/` — `Prompt.template_hash` stability and render purity
- benchmark / exemplar harnesses (when they land) must reuse `extract(...)` or `run_extraction(...)`; do not create a parallel evaluator-only path

## Observability and Diagnosis

Preferred diagnosis artifacts:

- `ReplayArtifact` — full run record; primary forensic surface
- `ExecutionTrace` — executor-owned run trace; phase 1 carries deterministic `NegativeOutcome` events while richer OTEL span export remains `Reporter`-owned
- `NegativeOutcome` — typed absence; every absence has a category + code + reason
- `ValidationFailure` — short-lived; routed through `ExecutorPolicy`

When a failure happens, every diagnosis artifact should surface:

- seam (letter A–K, or C.alt / M for optional)
- contract being exercised
- failure code (within `NegativeOutcome.category`)
- operator-facing reason
- correlation identifiers (document_id, instance_key, field_id, producer_versions)
- next debugging target

## Current Priorities

- bootstrap the project skeleton per `docs/architecture.md` §16 project layout
- build seam A (`DocumentAdapter`) + seam B (`ExtractionSpec.from_pydantic`) first — smallest proof surface and unblocks everything downstream
- contract tests alongside implementation, not after
- when M9 lands, keep persistence behind a store seam with typed refs; first backend should be a boring local filesystem adapter, with backend choice hidden behind the seam

## Notes For Future Agents

- the old extractx at `../extractx-old` is reference only; it made several anti-pattern choices we are rebuilding against. do not import from it.
- `docs/architecture.md` §17 "done" criteria are the bar for v1. every criterion maps to a specific seam and proof level.
- when in doubt about a spec detail, look at §9 canonical objects first — every object has a "necessary and sufficient" justification.
- one item in the architecture is flagged **tentative**: `boundary_defining` field mechanics in `G.planner`. revisit after first real multi-instance extraction before committing.
- pydantic v3 migration, if it happens, is a breaking release of the end-user pact. pin v2.x for now.
