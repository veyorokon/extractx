# Task: implement core contracts and canonical objects

*Critical-path implementation thread after M0. This task makes `src/extractx/core/**` real so downstream seam work can import canonical objects, exceptions, enums, version helpers, and dependency validation without inventing them locally. No seam behavior beyond the pure core type layer lands here.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; tier boundaries; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§4 canonical vocabulary, §7 seam contracts, §9 canonical objects, §10 three-tier public surface, §13 public api surface, §16 project layout, and §17 proof table** in full
- [`docs/thread-orchestration.md`](../thread-orchestration.md) — this is a bounded implementation thread; the integrator owns global coherence
- [`docs/adr/0001-pass-through-operational-metadata.md`](../adr/0001-pass-through-operational-metadata.md) — `UsageEvent.raw_usage` passthrough rule
- [`docs/adr/0002-pydantic-ai-default-selector-and-interview.md`](../adr/0002-pydantic-ai-default-selector-and-interview.md) — `InterviewTranscript` and `InterviewError`
- [`docs/adr/0003-single-canonical-layer3-no-resolver-validators.md`](../adr/0003-single-canonical-layer3-no-resolver-validators.md) — layer-3 ownership affects outcome shapes and failure routing
- [`docs/adr/0004-narrow-interview-scope-to-field-seams.md`](../adr/0004-narrow-interview-scope-to-field-seams.md) — field-scoped `InterviewTranscript`
- [`docs/adr/0005-candidate-overflow-policy.md`](../adr/0005-candidate-overflow-policy.md) — `PromptPolicy`, `SorterBinding`, `CandidateOverflowMetadata`, `ContextBudget`, `ContextPack.candidate_overflow`
- [`docs/adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md`](../adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md) — `SourceSpan.text_anchor_space`, seam-A subcontracts, UTF-8 byte-offset discipline

## Goal

implement the pure core type layer in `src/extractx/core/**`: canonical objects, outcome objects, anchor types, cardinality/value-kind primitives, exceptions, version helpers, dependency-graph validation, and named protocols/aliases sufficient for downstream modules to import without guessing.

**"done" in one sentence:** `src/extractx/core/**` contains real typed implementations of the architecture's core nouns and pure helpers, focused core tests pass, and no later seam task needs to invent a missing foundational type or core invariant.

## Scope

numbered implementation areas. do each in order.

### 1. anchors and source-provenance types

implement in `src/extractx/core/anchors.py`:

- `SourceRef`
- `SourceSpan`
- `PageRef`
- `BoundingRegion`
- `AnchorMap`

requirements:

- honor ADR-0006 exactly:
  - `SourceSpan.text_anchor_space: Literal["source_bytes", "normalized_text"]`
  - `byte_start` / `byte_end` are half-open byte offsets
  - `normalized_text` spans use UTF-8 byte offsets into `DocumentView.normalized_text.encode("utf-8")`
- do **not** silently treat normalized-text offsets as python `str` character indices
- encode the shape and local invariants that are purely structural in core (ordering, field presence, simple range checks)
- keep `AnchorMap` minimal and honest. the architecture defines it as a total function from normalized-text UTF-8 byte offsets to `SourceSpan`s, but does **not** yet define a rich method surface. implement the narrowest typed representation that downstream code can depend on without inventing extra semantics

implementation-shape constraint:

- if a richer `AnchorMap` api is not explicitly required by the docs, do **not** invent one here. prefer a minimal protocol or alias over speculative helper methods

### 2. canonical object layer

implement in `src/extractx/core/objects.py` the canonical objects and typed configuration/binding containers from `docs/architecture.md` §9:

- `DocumentView`
- `ExtractionSpec`
- `FieldSpec`
- `StrategyBinding`
- `ValidationBinding`
- `GroupingBinding`
- `PromptBinding`
- `SorterBinding`
- `PromptPolicy`
- `CandidateOverflowMetadata`
- `ContextBudget`
- `GroupingPolicy`
- `Candidate`
- `CandidateSet`
- `InstanceHint` (type alias)
- `ContextPack`
- `Selection`
- `UsageEvent`
- `InterviewTranscript`
- `RenderedPrompt`
- `InstanceKey`
- `InstanceState`
- `InstancePlan`
- `GroupingEvidence`

requirements:

- use **typed pydantic v2 containers** for structured data objects where that matches the architecture's stated direction for typed configuration/containers
- objects that should be immutable by contract should be implemented as frozen/immutable models
- sequences that are canonical in the docs as tuples stay tuples in code
- `ExtractionSpec` and `FieldSpec` must be able to participate in pure version/dependency validation later in this task
- `UsageEvent.raw_usage` stays pass-through and unshaped
- `InterviewTranscript` remains field-scoped exactly as narrowed by ADR-0004
- include the T0/T1 additions:
  - `PromptPolicy.candidate_overflow_policy`
  - `PromptPolicy.candidate_count_bound`
  - `FieldSpec.sorter_binding`
  - `ContextPack.candidate_overflow`

for names referenced in §9 / §13 but not fully shaped in the architecture today:

- define the **narrowest opaque placeholders or aliases** needed to keep core compile- and type-check-clean
- examples likely include: `FieldId`, `SchemaRef`, `ValidationReason`, `DistanceMetric`, `BudgetSpec`, `ValidationPolicy`, `Message`, `ArtifactRef`, `ExecutionTrace`
- document them as minimal placeholders owned by later tasks if their real owning seam has not yet landed
- do **not** invent rich structure for them here

implementation-shape constraint:

- no raw dict bags for typed core objects
- no convenience fields that are not in the architecture
- no later-seam behavior (no schema loading, no candidate generation, no replay writing, no runtime binding logic)

### 3. outcome and result layer

implement in `src/extractx/core/outcomes.py`:

- `ProposedField`
- `ValidatedField`
- `ResolvedFieldProposal`
- `NegativeOutcome`
- `ValidationFailure`
- `InstanceResult`
- `ExtractionResult`

also handle referenced-but-not-yet-fully-shaped outcome-adjacent nouns conservatively:

- `ProposalProvenance`

requirements:

- match the lifecycle separation exactly:
  - `ProposedField`
  - `ValidatedField`
  - `ResolvedFieldProposal`
- encode outcome enums/literals and fields exactly as documented
- `ExtractionResult.instances` is canonical
- implement only the **pure derived methods that are fully supportable from current core data**:
  - `.proposals()`
  - `.negatives()`
- you may implement `.stream()` as a post-hoc async iterator over `self.instances` if it can be done purely and honestly. real-time streaming during execution is executor-owned and out of scope for core
- methods that require later subsystems may exist as typed stubs raising `NotImplementedError`, with a short docstring pointing at the owning later task:
  - `.to_pydantic(...)`
  - `.usage()`
  - `.interview(...)`
- likewise, if `InstanceResult.to_pydantic(...)` is present to satisfy the architecture examples, make it a typed stub only; materialization belongs to later schema work

implementation-shape constraint:

- do not fake `.usage()` by scraping `trace`
- do not fake `.interview()` by inventing transcript lookup behavior
- do not embed replay/interview internals into `ExtractionResult`

### 4. enums, value kinds, exceptions, and protocols

implement:

- `src/extractx/core/cardinality.py`
  - `Cardinality`
  - only pure helpers directly justified by the docs; no selection-adapter table logic here
- `src/extractx/core/value_kinds.py`
  - built-in `ValueKind` members from `docs/architecture.md` §12 examples
  - extensibility hook matching the architecture's required `ValueKind.register("NAME")` behavior
- `src/extractx/core/exceptions.py`
  - `SpecError`
  - `CapabilityError`
  - `InfrastructureError`
  - `InterviewError`
- `src/extractx/core/contracts.py`
  - named protocols for the architecture's protocol nouns
  - add a named `Protocol` class for **every** §4 protocol noun in `core/contracts.py` so downstream seam tasks have a stable import surface
  - implement **explicit method signatures only where the docs already define them clearly**
  - examples that are explicit enough now:
    - `Budget`
    - `Prompt`
  - `Budget.check()` returns `BudgetDecision`; define `BudgetDecision` inline in `contracts.py` as the minimal typed allow / deny_with_reason shape described by §7 seam J
  - for protocols whose callable surface is not yet fully owned by a landed seam task, the class may remain empty (`class DocumentAdapter(Protocol): ...`). this preserves the import surface without inventing behavior. do **not** add methods that are not documented

implementation-shape constraints:

- `ValueKind` must support registration semantics; do **not** force it into a standard-library `Enum` shape if that breaks `.register()`
- `Cardinality` can be a real string enum
- do not add runtime registries beyond what the docs already call for
- do not invent full protocol method surfaces for seams whose detailed behavior is owned by later tasks

### 5. pure helpers: versioning and dependency graph validation

implement:

- `src/extractx/core/versions.py`
- `src/extractx/core/dependencies.py`

requirements:

- `versions.py` should provide pure helpers for:
  - stable content hashing used by core/spec objects
  - `producer_version` composition helpers for algorithmic vs soft producers
- `dependencies.py` should provide pure helpers for:
  - dependency graph validation over `FieldSpec.depends_on`
  - cycle detection raising `SpecError`
  - deterministic topological ordering helper if it can be defined without inventing policy beyond dependency edges

implementation-shape constraints:

- no manifest hashing here (owned later by execution)
- no filesystem or env access
- no runtime/provider logic

### 6. core package surface and focused tests

update:

- `src/extractx/core/__init__.py`

to export the implemented core symbols in a way that is useful for downstream internal imports.

do **not** widen the top-level `src/extractx/__init__.py` end-user surface yet unless a small import fix is absolutely required for testability. if you touch it, keep the change minimal and explain why.

add focused tests under the existing test lanes. expected shape:

- `tests/contracts/` for object-shape and seam-contract-adjacent pure invariants
- `tests/invariant/` for immutability / lifecycle / hash / registration invariants
- `tests/cardinality/` for cardinality enum / helper behavior
- `tests/determinism/` for version/dependency helper determinism

minimum proof targets to cover:

- `SourceSpan` requires `text_anchor_space`; `normalized_text` spans enforce UTF-8 byte alignment
- `ExtractionSpec` / `FieldSpec` typed shapes include ADR-0005 additions
- `InterviewTranscript` remains field-scoped
- `NegativeOutcome` / `ValidationFailure` shapes match docs
- `ExtractionResult.proposals()` and `.negatives()` flatten canonically
- `ValueKind.register("NAME")` behaves deterministically and does not break built-in kinds
- dependency cycle detection raises `SpecError`
- stable hashing / producer-version helpers are deterministic
- immutable/frozen core lifecycle objects cannot be mutated after construction

## Guardrails

- **write scope:** `src/extractx/core/**`, `src/extractx/core/__init__.py`, and focused tests only. if a tiny import fix outside that scope is unavoidable, keep it minimal and call it out explicitly.
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly. if so, stop and report with the standard pushback shape.
- **no code outside core behavior.** do not implement:
  - `schema/`
  - `source/`
  - `candidates/`
  - `selection/`
  - `proposals/`
  - `instances/`
  - `replay/`
  - `execution/`
  - `extras/`
- **no hidden later behavior.** if a method depends on a later seam, leave a typed stub and a short docstring rather than inventing fake behavior now.
- **do not overdesign opaque/internal types.** for internal or later-owned nouns whose exact structure is not specified, use the narrowest placeholder necessary to keep core honest and type-checkable.
- **do not backfill domain logic** from any consumer domain. this task is kernel-only.
- **do not introduce schema or prompt conveniences** that compete with later `schema/` or `selection/` work.
- **no commits or pushes** unless separately asked. the worker should leave the branch ready for review.

## Deliverable

code and tests in the repo, with the implementation centered in:

- `src/extractx/core/anchors.py`
- `src/extractx/core/objects.py`
- `src/extractx/core/outcomes.py`
- `src/extractx/core/cardinality.py`
- `src/extractx/core/value_kinds.py`
- `src/extractx/core/contracts.py`
- `src/extractx/core/versions.py`
- `src/extractx/core/dependencies.py`
- `src/extractx/core/exceptions.py`
- `src/extractx/core/__init__.py`

plus focused tests in `tests/**`.

include in your final report:

- the exact files changed
- any places where you intentionally used a minimal opaque placeholder because the owning seam has not landed yet
- any contradictions you found that would require a follow-on doc thread rather than more code

## Success criteria

- all targeted core modules are implemented and importable
- core objects and outcomes reflect the current architecture doc and ADR-0001 through ADR-0006
- no later seam needs to invent a missing foundational core type
- no speculative runtime/selection/schema behavior is smuggled into core
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pyright`
- new tests cover the listed proof targets
- any placeholder/opaque types are clearly documented and kept minimal
- top-level repo state remains coherent with the current architecture/doc pact

## Downstream consequences

- unblocks M2/T2: downstream seam implementation can import stable core nouns instead of inventing them locally
- directly feeds later tasks in:
  - `schema/` (`ExtractionSpec`, `FieldSpec`, `ValueKind`, `Cardinality`, dependency validation)
  - seam A (`SourceSpan`, `AnchorMap`, `DocumentView`)
  - seam D (`Selection`, `ContextPack`, `Prompt`, overflow metadata)
  - seam F (`NegativeOutcome`, `ValidationFailure`, lifecycle objects)
  - seam G (`InstanceKey`, `InstanceState`, `InstancePlan`, `GroupingEvidence`)
  - replay/execution (`producer_version` helpers, `UsageEvent`, `InterviewTranscript`, `ExtractionResult`)
- if this task exposes a real contradiction in the current architecture, that becomes a new coordinator-owned thread before more implementation proceeds
