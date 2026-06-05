# Task: implement M9 phase 2 — source-driven replay re-execution

*This is M9 phase 2. M9 phase 1 landed canonical persistence: `ReplayArtifact`, `SpecSummary`, `ExtractxStore`, `RunManifest`, executor-owned storage. The remaining open contract is **re-executability under pinning** — the architecture's "given pinned producer versions, replay reconstructs `ExtractionResult` bytewise" promise (§7 seam H). Phase 2 closes that loop with **source-driven replay**: given a persisted `ReplayArtifact` plus the store, rehydrate `(source_bytes, ExtractionSpec, schema_cls, ExecutorPolicy)`, run the real M8 pipeline through `SerialExecutor`, and prove the reproduced `ExtractionResult` is structurally equal to the captured one (modulo `replay_artifact_ref`). Keep this thread narrow: source-driven replay only, in-process class registry, pydantic-backed specs only, no per-seam fixture replay, no comparison mode, no seam-K tightening, no executor changes.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; replay notes; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam H in full** (note the storage-seam pin and the "replay mode determinism: given pinned selector, planner, and resolver `producer_version`s, replay reconstructs `ExtractionResult` bytewise" invariant — this thread operationalizes that promise), **§7 seam I.1 Executor** (determinism clause), **§7 seam J** (capability list — still does not widen here), **§9 canonical objects** (`ReplayArtifact`, `ExtractionResult`, `SpecSummary`), **§10 three-tier public surface**, **§11 execution model**, **§13 public api surface** (`run_extraction(...)` does **not** widen), **§15 anti-patterns** (`Canonical/Derived Smear`, `Benchmark-Only Execution Path`, `Duplicate Overlapping Path`)
- [`docs/adr/0007-storage-shape-authority-and-minimum-skeleton.md`](../adr/0007-storage-shape-authority-and-minimum-skeleton.md) — replay is canonical authority for reconstruction; this thread proves that authority concretely
- [`docs/tasks/m9-phase-1-replay-storage-skeleton.md`](m9-phase-1-replay-storage-skeleton.md) — the persistence skeleton this thread consumes. note its drift §2 ("phase-1 reconstruction does not re-execute seams; phase 2 will") — this thread fulfills that deferral
- [`docs/tasks/m8-phase-1-serial-independent-vertical-slice.md`](m8-phase-1-serial-independent-vertical-slice.md) — the supported pipeline shape that replay re-runs
- [`src/extractx/replay/{artifact,writer,reader}.py`](../../src/extractx/replay) — landed M9 phase-1 surface
- [`src/extractx/schema/summary.py`](../../src/extractx/schema/summary.py) — `SpecSummary` and `summarize_spec`
- [`src/extractx/schema/from_pydantic.py`](../../src/extractx/schema/from_pydantic.py) — current spec-build entry point; this thread extends its registration side effect
- [`src/extractx/schema/_schema_cls_registry.py`](../../src/extractx/schema/_schema_cls_registry.py) — the existing `_SCHEMA_CLS_BY_SPEC_VERSION` map; the new qualname registry sits alongside it
- [`src/extractx/execution/executor/serial.py`](../../src/extractx/execution/executor/serial.py) — **read-only** in this thread. no executor edits
- [`src/extractx/execution/runtime.py`](../../src/extractx/execution/runtime.py) — **read-only**. seam J still does not widen
- [`src/extractx/execution/policy.py`](../../src/extractx/execution/policy.py) — `ExecutorPolicy` shape. add a small rehydrator helper here

## Goal

implement source-driven replay re-execution so that a persisted run can be reproduced from its artifact alone:

- in-process class registry that maps `python_type` qualnames (and, defensively, binding-class qualnames) to live classes — populated at `from_pydantic(...)` time alongside the existing `_SCHEMA_CLS_BY_SPEC_VERSION` registry
- a `rehydrate_spec(summary, schema_cls) -> ExtractionSpec` helper that rebuilds a runnable `ExtractionSpec` from the stored `SpecSummary` and the registered live `schema_cls`
- a `replay_re_execute(artifact, store) -> ExtractionResult` engine that pulls source bytes, rehydrates the spec, looks up `schema_cls`, rebuilds `ExecutorPolicy`, constructs a fresh `SerialExecutor()` (no storage — replay does **not** persist a second artifact), runs the real M8 pipeline, and returns the reproduced `ExtractionResult`
- a typed producer-version drift surface: if any captured `producer_versions` entry diverges from the live class-level value at replay time, raise `InfrastructureError("replay.producer_version_drift: ...")` — drift surfaces, never blurs
- a typed missing-class surface: if `lookup_schema_cls(spec_version)` returns `None`, raise `InfrastructureError("spec_rehydrate.missing_class: ...")`
- proof: a real M8 run, persisted, replayed end-to-end, and shown structurally equal under the named equality below

without widening `run_extraction(...)` signature, without changes to `Runtime` / seam J / `SerialExecutor`, without per-seam fixture replay, without comparison mode against live providers, without seam-K tightening, and without supporting manual specs (deferred to a follow-on thread).

**"done" in one sentence:** given a `ReplayArtifact` and the `ExtractxStore` that holds the persisted source + spec_summary blobs, `replay_re_execute(artifact, store)` rehydrates the spec, runs `SerialExecutor()` against the original source bytes, and returns an `ExtractionResult` that is structurally equal to the captured one **excluding `replay_artifact_ref`**, on the supported algorithmic path.

## The named equality (load-bearing, used throughout this brief)

phase 2 introduces one new named equality, on top of the three from phase 1:

**replay-result equality** — given `captured = reconstruct_extraction_result(read_replay(store, artifact_id), artifact_id=artifact_id)` and `reproduced = replay_re_execute(artifact, store)`, assert `captured == reproduced` under pydantic structural equality **with `replay_artifact_ref` excluded** from the comparison (captured has the real id; reproduced has `""` because the replay engine constructs a non-persisting executor).

required equality fields (load-bearing, must compare equal):
- `final_instances` (canonical authority — every `ResolvedFieldProposal` byte-equal)
- `outcome`
- `document_id`
- `spec_version`
- `strategy`

excluded from required equality:
- `replay_artifact_ref` — known to differ; excluded by the helper
- `trace.events` — placeholder type owned by the seam-K thread (see drift §3); equality on step-event content is **not** asserted in phase 2. `trace.trace_id` is asserted equal because both runs go through the same executor shape with the same deterministic composition

## Scope

numbered implementation areas. do each in order.

### 1. extend the in-process class registry

extend `src/extractx/schema/_schema_cls_registry.py` (or add a sibling `_class_registry.py` next to it; the worker chooses the cleaner name). the existing `_SCHEMA_CLS_BY_SPEC_VERSION` keeps its semantics.

requirements:

- add a second registry: `_CLASS_BY_QUALNAME: dict[str, type]` mapping `f"{cls.__module__}.{cls.__qualname__}"` to the live class
- add `register_class_by_qualname(cls: type) -> None` and `lookup_class_by_qualname(qualname: str) -> type | None`
- the registry is in-process only — no filesystem walk, no `importlib`, no module-discovery shortcut
- `register_class_by_qualname` is idempotent on identical `(qualname, cls)` and raises `RuntimeError` on collision with a different class (mirroring `_SCHEMA_CLS_BY_SPEC_VERSION` semantics)
- `from_pydantic(schema_cls, ...)` (in `src/extractx/schema/from_pydantic.py`) calls `register_class_by_qualname(schema_cls)` as a side effect at spec-build time, in addition to the existing `register_schema_cls(spec.version, schema_cls)`
- defensively, `from_pydantic` also registers any binding `cls` it encounters (`StrategyBinding.cls`, `SorterBinding.cls`) by walking the built spec's `field_summaries`-equivalent during the same pass. this widens the registry's coverage so a future manual-spec replay thread does not need a second migration

implementation-shape constraints:

- no public api widening (all registry helpers are internal; not exported from `extractx.__init__` or `extractx.schema`)
- no `register_callable_by_qualname` for `normalizer` / `field_validators` in this thread — manual-spec replay is deferred (see drift §6)
- no auto-registration of arbitrary user classes outside the `from_pydantic` path
- the registry never auto-discovers from disk or from environment

### 2. canonical `rehydrate_spec` helper

land in `src/extractx/schema/rehydrate.py` (new module).

requirements:

- function signature:
  ```
  def rehydrate_spec(
      summary: SpecSummary,
      *,
      schema_cls: type[BaseModel],
  ) -> ExtractionSpec: ...
  ```
- rehydration strategy (load-bearing, simplest honest path): call `ExtractionSpec.from_pydantic(schema_cls)` with **no policy args**. trust the registry-resolved live class to produce the same spec the original `from_pydantic` call did — `from_pydantic` is deterministic over the same class and the same default-materialization path, and the original M8 call sites pass default policy args. `summary.prompt_policy` / `summary.validation_policy` / `summary.grouping_policy` / `summary.budget` are persistence records / forensic surface — they are **not** passed back into `from_pydantic` during rehydration in phase 2 (see drift §1)
- assert `spec.version == summary.spec_version`. on mismatch, raise `InfrastructureError("spec_rehydrate.version_mismatch: rehydrated spec.version=<x> != summary.spec_version=<y>")` — this surfaces silent drift in `from_pydantic`'s hash composition
- **field-shape sanity check (load-bearing):** assert `len(spec.fields) == len(summary.field_summaries)` and `tuple(f.field_id for f in spec.fields) == tuple(s.field_id for s in summary.field_summaries)`. on mismatch, raise `InfrastructureError("spec_rehydrate.field_drift: rehydrated field_ids=<...> != summary field_ids=<...>")` — closes the silent-drift surface where `from_pydantic`'s field-extraction logic could diverge between original-run time and replay time
- the rehydrated `ExtractionSpec` is structurally equal to the original spec produced at original-run time (because the same `from_pydantic` pass against the same live class is deterministic)
- the helper raises `InfrastructureError("spec_rehydrate.missing_class: spec_version=<v>")` if the caller passes a `schema_cls` whose qualname doesn't match what the registry has for `summary.spec_version`. caller is expected to look up `schema_cls` via `lookup_schema_cls(summary.spec_version)` before calling `rehydrate_spec`
- exports: `rehydrate_spec` from `src/extractx/schema/__init__.py` (internal — not tier-1)

implementation-shape constraints:

- do **not** rebuild `ExtractionSpec` field-by-field from `SpecSummary.field_summaries`. the live `schema_cls` is the source of truth for fields/bindings; `SpecSummary.field_summaries` is the field-shape sanity-check surface only
- do **not** pass `summary.prompt_policy` / `validation_policy` / `grouping_policy` / `budget` back into `from_pydantic(...)` during rehydration. policy fields on `SpecSummary` are forensic record / cross-check surface, not the rehydration source
- do **not** support manual specs in this thread. if `summary.source_schema_ref is None` (manual spec), `rehydrate_spec` raises `InfrastructureError("spec_rehydrate.manual_unsupported: phase 2 supports pydantic-backed specs only")`. manual replay is a follow-on thread that needs a public registration api (drift §6)
- do **not** mutate the registry in `rehydrate_spec` — it only reads
- do **not** silently coerce the rehydrated `spec.version` to match `summary.spec_version`; the version-mismatch failure is load-bearing
- do **not** silently coerce field-shape drift; `field_drift` raises loudly

### 3. canonical `replay_re_execute` engine

land in `src/extractx/replay/engine.py` (new module).

requirements:

- function signature:
  ```
  def replay_re_execute(
      artifact: ReplayArtifact,
      store: ExtractxStore,
  ) -> ExtractionResult: ...
  ```
- execution flow (load-bearing):
  1. read source bytes via `store.get_object("source", artifact.source_ref.content_hash)`
  2. read spec summary via `read_spec_summary(store, artifact.spec_version)`
  3. look up `schema_cls = lookup_schema_cls(artifact.spec_version)`. if `None` → `InfrastructureError("spec_rehydrate.missing_class: spec_version=<v>")`
  4. rehydrate spec: `spec = rehydrate_spec(summary, schema_cls=schema_cls)`
  5. **producer-version drift check** — see §4
  6. rebuild `ExecutorPolicy` from `artifact.policy_summary` via `ExecutorPolicy.from_summary(artifact.policy_summary)` (helper added in §5)
  7. construct a fresh `Runtime()` and a fresh `SerialExecutor()` — **no `storage` parameter; replay does not persist**
  8. run the real pipeline: `result = await executor.execute(source_bytes, spec, runtime, policy)`. this is the same `SerialExecutor.execute(...)` callers use; no replay-specific code path through the executor
  9. return `result`. the returned result has `replay_artifact_ref=""` because the executor was constructed without storage
- the engine is **async** (because `SerialExecutor.execute` is async); callers `await replay_re_execute(...)`. if a sync wrapper proves useful, it's a follow-on
- exports: `replay_re_execute` from `src/extractx/replay/__init__.py`

implementation-shape constraints:

- do **not** add replay-specific code paths inside `SerialExecutor` or `IndependentStrategy`. the engine is a caller of the existing executor, not a parallel pipeline (anti-pattern §15 `Benchmark-Only Execution Path`)
- do **not** persist anything during replay. `objects/replay/` does not gain a second entry; `runs/` does not gain a second manifest
- do **not** widen the engine's signature with `policy`, `runtime`, or `schema_cls` parameters — the artifact + store are the only inputs. the engine resolves everything else
- do **not** swallow exceptions from the underlying `executor.execute(...)`; pre-run-gate `InfrastructureError`s propagate as-is. `ValidationFailure`s are already escalated by the executor and appear inside the returned `ExtractionResult`
- do **not** introduce a `replay_re_execute_sync(...)` wrapper unless the proof tests genuinely need it (they likely don't — pytest `pytest-asyncio` covers the async path)

### 4. producer-version drift check

a typed surface that fires when the captured `producer_versions` map disagrees with the live values at replay time.

requirements:

- helper `check_producer_version_drift(captured: Mapping[str, str]) -> None` in `src/extractx/replay/engine.py`
- live values are computed via the same module-level helpers M9 phase 1 used for capture:
  - `"candidate_strategy"` ← `extractx.candidates.generators.regex.algorithmic_code_hash()`
  - `"selector"` ← `extractx.selection.algorithmic.singleton.algorithmic_code_hash()`
  - `"resolver"` ← `extractx.instances.resolvers.deterministic.algorithmic_code_hash()`
- if any captured key is missing from the live computation, or any captured value differs from the live value, raise `InfrastructureError("replay.producer_version_drift: <key>: captured=<x> live=<y>; ...")` listing every diverging entry
- live keys not present in the captured map are **not** a drift (the captured map is the canonical key set for the run; new live keys would surface only after a seam adds a tracked producer)
- `check_producer_version_drift` is invoked once, after spec rehydration and before constructing the executor

implementation-shape constraints:

- do **not** soft-classify drift as a warning; the architecture's replay-under-pinning promise is binary (matches or doesn't)
- do **not** ignore drift with a "best-effort replay" mode; phase-2 has only the strict path
- do **not** emit a `NegativeOutcome` — drift is an `InfrastructureError`, surfaced before the run begins, not a typed runtime negative
- the helper is private to the engine; no public api

### 5. `ExecutorPolicy.from_summary` rehydrator

small additive helper on `ExecutorPolicy` (in `src/extractx/execution/policy.py`).

requirements:

- classmethod `ExecutorPolicy.from_summary(summary: PolicySummary) -> ExecutorPolicy` that reconstructs an `ExecutorPolicy` whose fields match the summary's
- the inverse helper `ExecutorPolicy.to_summary() -> PolicySummary` (or equivalent) is **not** required in this thread; M9 phase 1 already builds `PolicySummary` at executor-write-time and that path stays unchanged
- the rehydrator is symmetric: `policy.to_summary` from phase 1 round-trips through `from_summary` to a structurally-equal policy under pydantic equality

implementation-shape constraints:

- do **not** rename or repurpose existing `PolicySummary` fields
- do **not** widen `ExecutorPolicy` with new fields in this thread

### 6. wire and prove

write the proof tests; do not modify the executor.

requirements:

- new test file `tests/replay/test_source_driven_replay.py` with:
  - happy-path: persist a `complete`-outcome run; `replay_re_execute(artifact, store)`; assert replay-result equality
  - happy-path: same, but `partial`-outcome run. **fixture note:** constructing a `partial`-outcome fixture requires deliberate validation failure or grouping ambiguity — reuse the M9 phase 1 partial-outcome fixture if it landed; otherwise build the smallest honest one (single-field spec with a regex that produces an out-of-range date for a `Date` python_type works) and do not over-engineer the test setup
  - happy-path: same, but `failed`-outcome run (`outcome="failed"`, `instances=()`)
  - white-box: `replay_re_execute` does **not** write to the store (no second artifact, no second manifest after replay completes)
  - white-box: replay-result equality is satisfied with `replay_artifact_ref` exclusion (a helper or fixture confirms the captured value is non-empty and reproduced is `""` before exclusion)
  - drift surface: bump a seam class's `algorithmic_code_hash()` (or monkey-patch it); replay; assert `InfrastructureError` raised whose message starts with `"replay.producer_version_drift: "`
  - missing class: clear `_SCHEMA_CLS_BY_SPEC_VERSION` entry for the run's spec_version; replay; assert `InfrastructureError("spec_rehydrate.missing_class: ...")`
  - manual-spec rejection: build a manual `ExtractionSpec` (no `from_pydantic`); persist + capture artifact; replay; assert `InfrastructureError("spec_rehydrate.manual_unsupported: ...")`
- new test file `tests/schema/test_rehydrate_spec.py` with:
  - `rehydrate_spec(summary, schema_cls=registered_cls)` produces a spec structurally equal to the original
  - rehydrated `spec.version == summary.spec_version`
  - version-mismatch surface: monkey-patch `from_pydantic` to produce a different version; assert `InfrastructureError("spec_rehydrate.version_mismatch: ...")`
  - manual-spec rejection: `summary.source_schema_ref is None` → `InfrastructureError("spec_rehydrate.manual_unsupported: ...")`
- new test file `tests/integration/test_replay_round_trip_e2e.py` with:
  - end-to-end: persist a multi-field pydantic-backed run; replay end-to-end; assert reproduced `final_instances` byte-equal under pydantic equality, and `outcome` matches; no second store entry written
- existing tests must continue to pass; do **not** modify the M9 phase 1 test suite

implementation-shape constraints:

- tests must use `tmp_path` and clean up cleanly (no shared filesystem state)
- no new conftest-level fixtures unless multiple test files share them
- monkey-patching seam-class helpers must restore the original at test teardown (use `monkeypatch.setattr` in pytest)
- do **not** introduce a benchmark or evaluator harness — proof tests reuse `run_extraction(...)` (or equivalently `SerialExecutor.execute(...)`) and the new `replay_re_execute(...)`

## Explicit drifts to acknowledge in the implementation

surface these in code comments or the final report; do not silently invent around them:

1. **rehydration uses live `schema_cls` as source of truth; `SpecSummary` is sanity-check surface**
   - for pydantic-backed specs, the registered `schema_cls` is the source of truth for fields/bindings. rehydration calls `ExtractionSpec.from_pydantic(schema_cls)` with no policy args, then asserts `spec.version == summary.spec_version` (composition-stability check) and `tuple(f.field_id for f in spec.fields) == tuple(s.field_id for s in summary.field_summaries)` (field-drift check). `SpecSummary.field_summaries` and policy fields are persistence record / forensic surface, **not** the rehydration source. this avoids the version-composition ambiguity that arises when the original `from_pydantic` call used default policy args while the persisted summary carries the materialized defaults
2. **manual specs are deferred**
   - a manual spec carries live binding callables (`normalizer`, `field_validators`) that have no spec-load-time registration site today. supporting manual replay would require a public `register_for_replay(cls_or_callable)` api — a real public-surface decision deferred to a follow-on thread
3. **`ExecutionTrace.events` is a placeholder; equality excludes step events**
   - M9 phase 1 drift §4 already noted the placeholder. phase 2 does not fold in seam-K tightening; the equality helper excludes `trace.events` content from the load-bearing comparison. `trace.trace_id` is included because both runs compose it deterministically from the same `(document_id, spec.version, "serial", "independent")` tuple
4. **`replay_artifact_ref` is excluded from the equality**
   - reproduced result carries `""` because the replay engine constructs `SerialExecutor()` without storage. excluded by the helper. **no executor knob** to pin `replay_artifact_ref` from a replay input
5. **producer-version drift is a hard failure, not a warning**
   - `InfrastructureError("replay.producer_version_drift: ...")` is the only outcome of mismatch. there is no "soft replay" mode, no comparison-mode surface, no divergence classification in phase 2
6. **class registry extends `_SCHEMA_CLS_BY_SPEC_VERSION` with a sibling `_CLASS_BY_QUALNAME` map; both populated at `from_pydantic` time**
   - registration is a side effect of `from_pydantic(...)`, mirroring the existing pattern. no new public api. cross-process replay (where `from_pydantic` was not called in the replay process) is out of scope and surfaces as `spec_rehydrate.missing_class`
7. **the engine is async**
   - `replay_re_execute(...)` is `async def` because `SerialExecutor.execute(...)` is async. callers `await` it. no sync wrapper unless tests prove a need
8. **per-seam fixture replay is parked**
   - feeding each seam its captured input (e.g. seam D gets stored `CandidateSet`; seam C is not re-run) is a useful regression surface but not the load-bearing closure. follow-on thread
9. **comparison mode against live providers is parked**
   - the algorithmic slice has no live providers. soft-compute replay + divergence classification is a follow-on
10. **no executor changes**
    - `SerialExecutor` and `IndependentStrategy` and the seam classes are not modified. the replay engine is purely a caller composition
11. **`Budget` / `Reporter` identity is not part of replay equality**
    - the replay engine constructs a fresh `Runtime()` whose `budget` is a fresh `TokenCountBudget()` and whose `reporter` is `NullReporter()`. the captured run's `Budget` / `Reporter` instances are not preserved across replay. this is a non-issue on the algorithmic slice (no `UsageEvent`s emitted, no reporter events threaded), but it is a real difference and is named here so it does not surface as a surprise during soft-compute replay (which is its own thread)

## Guardrails

- **write scope:** `src/extractx/schema/rehydrate.py` (new), `src/extractx/schema/_schema_cls_registry.py` (extend) **or** new sibling `src/extractx/schema/_class_registry.py`, `src/extractx/schema/from_pydantic.py` (small additive registration call), `src/extractx/schema/__init__.py` (export `rehydrate_spec`), `src/extractx/replay/engine.py` (new), `src/extractx/replay/__init__.py` (export `replay_re_execute`), `src/extractx/execution/policy.py` (add `ExecutorPolicy.from_summary`), focused tests
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly
- **no `Runtime` changes whatsoever** (`src/extractx/execution/runtime.py` is read-only). seam J does not widen
- **no `SerialExecutor` / `IndependentStrategy` changes whatsoever** (`src/extractx/execution/executor/serial.py` and `src/extractx/execution/strategies/independent.py` are read-only). the replay engine is a caller, not a modifier
- **no seam-class changes** (no edits to `RegexCandidateStrategy`, `SingletonSelector`, `LayeredProposalValidator`, `DeterministicInstanceResolver`, `StructuralInstancePlanner`, `CardinalitySelectionAdapter`, `TextAdapter`)
- **no widening of `run_extraction(...)` signature**
- **no widening of `extractx.__init__` tier-1 exports**
- **no widening of seam J capability list**
- **no manual-spec replay** (deferred; raises `InfrastructureError`)
- **no per-seam fixture replay** (parked)
- **no comparison-mode harness** (parked)
- **no seam-K tightening** (`ExecutionTrace.events` placeholder stays as-is)
- **no second storage backend**
- **no `result/` cache writing during replay**
- **no public registration api** for arbitrary classes/callables (e.g., no `extractx.register_for_replay(...)`)
- **no dependency changes**
- **no commits or pushes** unless separately asked

## Pushback discipline

if a hard pin contradicts code reality (e.g. `from_pydantic`'s version composition turns out non-deterministic for the same `schema_cls`, or `SerialExecutor.execute` raises an unexpected exception on the algorithmic path), do **not** silently work around it. instead, in the final report under a `## Pushback` heading, write a structured block:

- current contract:
- observed gap or contradiction:
- consequence if implemented as written:
- proposed cleaner pattern:
- seam / ownership impact:
- whether this is clarification vs architecture change:
- proof target:

…and stop coding. the coordinator will adjudicate.

## Focused proof

minimum proof targets (numbered):

1. **replay-result equality** (load-bearing) — for `complete`, `partial`, `failed` outcomes, `replay_re_execute(artifact, store)` returns an `ExtractionResult` structurally equal to the captured one, modulo the documented exclusions (`replay_artifact_ref` excluded; `trace.events` content excluded; `trace.trace_id` included)
2. **no second persistence** — replay engine writes nothing to the store; `objects/replay/` and `runs/` directory listings are unchanged after replay completes
3. **producer-version drift surface** — a deliberate seam-class hash bump (or monkey-patch) at replay time produces `InfrastructureError("replay.producer_version_drift: ...")` listing the diverging key(s)
4. **missing class surface** — clearing `_SCHEMA_CLS_BY_SPEC_VERSION` for the run's spec_version produces `InfrastructureError("spec_rehydrate.missing_class: ...")`
5. **manual-spec rejection** — replaying a manual spec produces `InfrastructureError("spec_rehydrate.manual_unsupported: ...")`
6. **version-mismatch surface** — a deliberate `from_pydantic` version perturbation at rehydration time produces `InfrastructureError("spec_rehydrate.version_mismatch: ...")`
7. **field-drift surface** — a deliberate divergence between rehydrated `spec.fields` and `summary.field_summaries` (e.g. monkey-patch `from_pydantic` to drop a field) produces `InfrastructureError("spec_rehydrate.field_drift: ...")`
8. **`ExecutorPolicy.from_summary` round-trip** — `policy_summary → from_summary → to_summary` is structurally identical to the original `PolicySummary`
9. **registry-extension scope** — `from_pydantic` registers `schema_cls` in both `_SCHEMA_CLS_BY_SPEC_VERSION` and `_CLASS_BY_QUALNAME`. `lookup_class_by_qualname` returns the registered class; collision raises
10. **engine surface** — `replay_re_execute` is `async`, takes only `(artifact, store)`, returns `ExtractionResult`. no policy/runtime/schema_cls parameters
11. **no executor edits** — diff stat for `src/extractx/execution/executor/serial.py`, `src/extractx/execution/strategies/independent.py`, `src/extractx/execution/runtime.py` is zero in the worker commit
12. **stub honesty preserved** — `ExtractionResult.usage()` and `.interview()` and `.to_pydantic()` continue to raise `NotImplementedError` (this thread does not unblock those paths)
    - current implementation note: ADR-0015 later replaced `.usage()` with a captured usage-event projection; `.interview()` remains stubbed
13. **no benchmark-only path** — replay tests reach the executor via real `replay_re_execute(artifact, store)` which itself calls real `SerialExecutor.execute(...)`. no parallel pipeline

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/schema/rehydrate.py`
- `src/extractx/schema/_schema_cls_registry.py` or sibling `_class_registry.py`
- `src/extractx/schema/from_pydantic.py` (small additive)
- `src/extractx/replay/engine.py`
- `src/extractx/execution/policy.py` (small additive)
- focused tests under `tests/replay/`, `tests/schema/`, `tests/integration/`

include in your final report:

- exact files changed (zero-line diff confirmed for `runtime.py`, `executor/serial.py`, `strategies/independent.py`, every seam class)
- the registry shape as landed (class names, registration call sites)
- `rehydrate_spec` signature as landed (vs the brief's signature — surface drift if any)
- `replay_re_execute` signature as landed (must be `async def replay_re_execute(artifact, store) -> ExtractionResult`)
- the producer-version drift check shape (helper signature; live-value source)
- the equality helper used for replay-result comparison (file:line; exclusions encoded)
- whether the version-mismatch and producer-version-drift error prefixes are stable (so future tooling can pattern-match)
- any follow-on that should become a coordinator-owned thread instead of widening this one (likely candidates: manual-spec replay public registration api, per-seam fixture replay regression harness, comparison-mode against live providers, sync `replay_re_execute_sync` wrapper, second storage backend)

## Success criteria

- `replay_re_execute(artifact, store)` reproduces `ExtractionResult` structurally equal to the captured one (modulo excluded fields) for `complete`, `partial`, and `failed` outcomes on the supported algorithmic path
- producer-version drift surfaces as `InfrastructureError("replay.producer_version_drift: ...")` — never silently
- missing-class and version-mismatch surfaces fire with their pinned message prefixes
- manual specs are honestly rejected with a pinned prefix; deferred to a follow-on thread
- replay writes nothing to the store
- `Runtime` / `SerialExecutor` / `IndependentStrategy` / seam classes are unchanged
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`

## Downstream consequences

- once this lands, the replay-under-pinning promise is operationally proven on the supported path: stored artifact + stored source + registered schema class → reproduces the answer. M9 is feature-complete for pydantic-backed specs
- next clean threads (in priority order, all coordinator-owned, none folded into this one):
  1. **seam K tightening** — typed `ExecutionTrace.events`; remove the M9 phase-1 `_rehydrate_trace` shim; tighten the replay-result equality to include step events
  2. **result cache** — `objects/result/` populated via `reconstruct_extraction_result(replay)` per M9 phase-1 drift §7
  3. **manual-spec replay** — public `register_for_replay(...)` api enabling manual specs to participate in the registry
  4. **per-seam fixture replay** — regression harness that feeds each seam its captured input and asserts seam-isolated reproducibility
  5. **comparison mode** — soft-compute replay + divergence classification (per architecture §7 seam H comparison-mode invariant)
  6. **second storage backend** — s3 / gcs / db
  7. **interview storage**, **views**, **async executor**, **iterative-strategy persistence** (all already enumerated in M9 phase 1)
- do not fold any of those into this thread
