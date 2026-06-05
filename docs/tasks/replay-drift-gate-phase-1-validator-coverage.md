# Task: implement replay drift-gate phase 1 — validator producer-version coverage

*This is the first phase of replay drift-gate tightening. After M9 phase 2 lands the source-driven replay contract, the gate's coverage is too narrow: `replay.producer_version_drift` checks `"candidate_strategy"` / `"selector"` / `"resolver"` only. **`LayeredProposalValidator` (seam F layer 2 + the M9 phase 2 default for layer 3) is re-run during replay but is not part of the drift gate.** if a future change to `LayeredProposalValidator`'s code shape produces a divergent `field_validation_version` while leaving the per-seam class hashes untouched, replay catches the divergence as a result mismatch (different `normalized_value`s flow through to `final_instances`) — but downstream tooling sees the wrong error class. close the gap with the smallest honest pattern: a class-level `LayeredProposalValidator.algorithmic_code_hash()` (mirroring the seam-C / seam-D / seam-G.resolver pattern), capture it in `ReplayArtifact.producer_versions["validator"]`, and widen the gate's live-key set. legacy artifacts (written before this thread) gracefully skip the validator key per the load-bearing legacy-compat pin below.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; replay notes; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam F** (note that `LayeredProposalValidator` is the seam-F concrete; algorithmic, deterministic, no soft-compute), **§7 seam H** ("replay mode determinism: given pinned selector, planner, and resolver `producer_version`s, replay reconstructs `ExtractionResult` bytewise" — this thread widens the pinning surface to include the validator class), **§9 canonical objects** (`ReplayArtifact`), **§13 public api surface**, **§15 anti-patterns**
- [`docs/tasks/m9-phase-1-replay-storage-skeleton.md`](m9-phase-1-replay-storage-skeleton.md) — landed `producer_versions: Mapping[str, str]` shape; narrow phase-1 keys (`"candidate_strategy"`, `"selector"`, `"resolver"`)
- [`docs/tasks/m9-phase-2-replay-re-execution.md`](m9-phase-2-replay-re-execution.md) — landed `check_producer_version_drift`; live-key composition pattern
- [`src/extractx/proposals/validation.py`](../../src/extractx/proposals/validation.py) — `LayeredProposalValidator` (seam F implementation); this is where `algorithmic_code_hash()` lands as a sibling module-level helper
- [`src/extractx/candidates/generators/regex.py`](../../src/extractx/candidates/generators/regex.py) — reference pattern for `algorithmic_code_hash()` (seam C)
- [`src/extractx/selection/algorithmic/singleton.py`](../../src/extractx/selection/algorithmic/singleton.py) — reference pattern (seam D)
- [`src/extractx/instances/resolvers/deterministic.py`](../../src/extractx/instances/resolvers/deterministic.py) — reference pattern (seam G.resolver)
- [`src/extractx/execution/executor/serial.py`](../../src/extractx/execution/executor/serial.py) — `_persist_run` captures `producer_versions`; this is where the new `"validator"` key writes
- [`src/extractx/replay/engine.py`](../../src/extractx/replay/engine.py) — `_live_producer_versions()` and `check_producer_version_drift`; this is where the live-key widens
- [`src/extractx/replay/artifact.py`](../../src/extractx/replay/artifact.py) — **read-only**. `ReplayArtifact.producer_versions: Mapping[str, str]` shape unchanged; the addition is on the keys, not the type

## Goal

widen the replay drift gate to include `LayeredProposalValidator` so that validator code drift surfaces as a typed `replay.producer_version_drift: validator: ...` failure rather than a downstream "result mismatch":

- add a module-level `algorithmic_code_hash()` helper in `src/extractx/proposals/validation.py`, mirroring the seam-C / seam-D / seam-G.resolver pattern verbatim
- extend `SerialExecutor._persist_run` to write `producer_versions["validator"]` from that helper at run time
- extend `replay/engine.py::_live_producer_versions()` to include `"validator"` so the gate compares it
- pin **legacy-compatible** behavior explicitly: artifacts written before this thread (without `"validator"` in their captured `producer_versions`) **proceed through the gate without raising** because the gate iterates **captured** keys against live, not the other way round. add a regression proof so this stays true under future widenings

without bumping `ReplayArtifact.schema_version`, without changing `ReplayArtifact`'s field shape, without per-call `field_validation_version` aggregation, without reporter / interview / storage widening, without seam-class changes beyond the new `validation.py` helper, and without modifying replay reader / artifact / writer.

**"done" in one sentence:** new artifacts capture `producer_versions["validator"]` and the replay drift gate fires `InfrastructureError("replay.producer_version_drift: validator: ...")` on validator-class divergence; legacy artifacts (without the key) replay through the gate without raising; schema_version stays `"v1"` because the addition is forward-compatible on a mapping.

## The contract change

**before this thread:** `ReplayArtifact.producer_versions: Mapping[str, str]` carries:
- `"candidate_strategy"` — `RegexCandidateStrategy.algorithmic_code_hash()`
- `"selector"` — `SingletonSelector.algorithmic_code_hash()`
- `"resolver"` — `DeterministicInstanceResolver.algorithmic_code_hash()`

**after this thread:** the same `Mapping[str, str]` carries the three above **plus**:
- `"validator"` — `LayeredProposalValidator.algorithmic_code_hash()`

the type is unchanged. the **key set is widened** for newly-written artifacts. legacy artifacts retain their narrower key set and replay successfully (per legacy-compat pin in §4).

## Scope

numbered implementation areas. do each in order.

### 1. land `algorithmic_code_hash()` in `src/extractx/proposals/validation.py`

requirements:

- **module-level function only.** no class-level `producer_version` property is added on `LayeredProposalValidator` in this thread. the M9 phase 1 capture pattern uniformly sourced producer-versions from module-level helpers across all three reference seams, regardless of whether the seam class also exposed a class property — this thread follows that capture pattern exactly. note: `DeterministicInstanceResolver` (seam G.resolver) currently has **both** a class `producer_version` property **and** a module-level helper; that double shape is a known pre-existing inconsistency parked for a coordinator-owned harmonization thread, not unwound here
- helper signature:
  ```python
  def algorithmic_code_hash() -> str:
      """return the seam-F validator's `producer_version` string.

      mirrors the pattern used by seams C / D / G.resolver: the
      `code_hash` is composed from the class's fully-qualified name so
      any subclass with different behavior produces a different
      `producer_version` automatically.

      module-level only — this thread does not introduce a class
      `producer_version` property on `LayeredProposalValidator`. the
      M9 phase 1 capture path consumes the module-level helper
      uniformly across all four seams it tracks.
      """
      digest = stable_hash(
          f"{LayeredProposalValidator.__module__}.{LayeredProposalValidator.__qualname__}",
      )
      return algorithmic_producer_version(digest)
  ```
- export from the module's `__all__`
- the helper composes from `LayeredProposalValidator.__module__` + `__qualname__` exactly as the three reference seams do — no per-call material, no spec-version mixing, no field-id mixing
- the result is byte-identical to a hash computed against the same class qualname today (i.e., subsequent runs of the same code produce the same hash)

implementation-shape constraints:

- do **not** introduce a class-level `producer_version` property on `LayeredProposalValidator`. if a future thread harmonizes the three reference seams' shapes, it owns that decision; this thread does not pre-decide it
- do **not** mix `field_validation_version` material into the class-level hash. `field_validation_version` is per-call; `algorithmic_code_hash()` is class-level. they answer different questions
- do **not** rename `LayeredProposalValidator`
- do **not** edit any other seam class, helper, or test in this step

### 2. capture the new key in `SerialExecutor._persist_run`

requirements:

- add `"validator"` to the `producer_versions` mapping built inside `_build_replay_artifact` (or wherever `_persist_run` composes the map — locate the existing call site by searching for the three captured keys)
- the live value: `extractx.proposals.validation.algorithmic_code_hash()`. import via the module path so monkey-patching at the **module attribute** level surfaces during replay (mirrors the M9 phase-2 pattern at `replay/engine.py::_live_producer_versions()`)
- key ordering inside the mapping is not load-bearing (it's a `Mapping[str, str]`, not an ordered dict); deterministic-iteration tests should not depend on key order

implementation-shape constraints:

- do **not** widen `_StrategyOutput` / `StrategyOutput` to carry `producer_versions` — capture happens at the executor, where the seam-class instances are reachable
- do **not** introduce a `validator_code_hash` parameter on `_persist_run`; the helper is called inline like the existing three
- do **not** edit `IndependentStrategy` (it does not own producer-version capture)
- do **not** widen `ReplayArtifact`'s field shape (the type `Mapping[str, str]` already accommodates the new key)

### 3. widen the live-key set in `replay/engine.py::_live_producer_versions()`

requirements:

- import `extractx.proposals.validation as _validation_module` at the module top (mirrors the existing `_regex_module` / `_singleton_module` / `_deterministic_module` imports)
- extend the dict returned by `_live_producer_versions()`:
  ```python
  return {
      "candidate_strategy": _regex_module.algorithmic_code_hash(),
      "selector": _singleton_module.algorithmic_code_hash(),
      "resolver": _deterministic_module.algorithmic_code_hash(),
      "validator": _validation_module.algorithmic_code_hash(),
  }
  ```
- the gate's iteration shape stays the same (`for key, captured_value in captured.items(): ...`). live keys not present in `captured` are not drift — this is the load-bearing invariant for legacy compatibility (§4)
- the typed message prefix `replay.producer_version_drift: ...` is unchanged

implementation-shape constraints:

- do **not** invert the iteration to `for key, live_value in live.items(): ...` — that would break legacy compatibility (a legacy artifact without `"validator"` would suddenly start raising)
- do **not** add a "missing key" diagnostic in the gate — the load-bearing pin is silent skip on missing captured keys
- do **not** widen the gate's signature; `check_producer_version_drift(captured: Mapping[str, str]) -> None` stays as-is

### 4. legacy-artifact compatibility (load-bearing)

requirements:

- this thread does **not** bump `ReplayArtifact.schema_version`. the addition is forward-compatible on a `Mapping[str, str]` because the gate's iteration shape already silently skips live keys not in captured
- legacy artifacts (those written by M9 phase 1 / phase 2, before this thread) carry `producer_versions` with three keys; new artifacts (written after this thread) carry four. the gate handles both:
  - new artifacts: `"validator"` in captured → checked against live → drift surfaces as `replay.producer_version_drift: validator: ...`
  - legacy artifacts: `"validator"` not in captured → no comparison → gate skips silently → replay proceeds
- pin a load-bearing test (proof target §6) that constructs a synthetic legacy artifact with a 3-key `producer_versions` (`"candidate_strategy"` / `"selector"` / `"resolver"` only) and asserts `replay_re_execute(...)` succeeds without raising
- add a brief code-comment in `replay/engine.py::check_producer_version_drift` and in `replay/engine.py::_live_producer_versions()` documenting the load-bearing legacy-compat invariant ("iterate captured keys; live keys not in captured are not drift; this is what makes legacy artifacts replayable across drift-gate widenings")

implementation-shape constraints:

- do **not** add a `legacy_mode` knob, a `strict_legacy_check` flag, or any other policy switch. the legacy-compat behavior is the only behavior in phase 1
- do **not** emit a warning / log / `Reporter` event for legacy artifacts — phase-1 storage is silent on legacy
- do **not** distinguish "old artifact" from "intentionally narrow capture" — the gate does not care which; missing key = skip

### 5. forward-note for schema-version evolution

requirements:

- add a forward-note paragraph to the **`ReplayArtifact` class docstring** (in `src/extractx/replay/artifact.py`), under a new `### schema_version evolution` sub-heading, noting that **adding a key to `producer_versions` is forward-compatible** (no schema bump needed) but **changing the type of an existing field, narrowing a union beyond what historical data carried, or restructuring nested fields would be a `v1 → v2` migration**. this thread does not perform such a migration
- the placement is the `ReplayArtifact` class docstring **only** — do **not** add an inline `# ...` comment above the `schema_version` field declaration; the consolidated docstring location keeps the forward-note discoverable in one place rather than scattered
- the paragraph is one short paragraph under the sub-heading; do not invent a versioning protocol
- this is the schema-version forward-note flagged in the M9 phase 2 final report under "drifts surfaced"

implementation-shape constraints:

- do **not** introduce a v2 type, a migration helper, or a deserializer-version-fork
- do **not** edit `ReplayArtifact`'s logic — only the class docstring is touched
- do **not** add inline field-level comments; consolidated docstring placement only
- this is the only edit to `replay/artifact.py` in this thread; the file is otherwise read-only here

### 6. tests

land focused tests under `tests/replay/` and `tests/proposals/`.

requirements:

- new test file `tests/proposals/test_validator_algorithmic_code_hash.py`:
  - the helper exists and is module-level
  - it returns a stable string (call twice; same hash)
  - it changes if `LayeredProposalValidator.__qualname__` changes (white-box: subclass `LayeredProposalValidator` and confirm the subclass would produce a different hash via the same composition pattern)
  - the prefix is `code:` (or whatever `algorithmic_producer_version(...)` emits — match the seam-D / seam-G.resolver pattern exactly)
- extend `tests/replay/test_source_driven_replay.py` (or new file `tests/replay/test_drift_gate_validator_coverage.py`):
  - happy-path: a freshly-persisted run carries `producer_versions["validator"]` with a value matching `extractx.proposals.validation.algorithmic_code_hash()` at write time
  - drift surface: monkey-patch `extractx.proposals.validation.algorithmic_code_hash` to return a divergent string; replay; assert `InfrastructureError("replay.producer_version_drift: validator: ...")` raised
  - **legacy-compat** (load-bearing per §4): construct a synthetic `ReplayArtifact` with `producer_versions = {"candidate_strategy": ..., "selector": ..., "resolver": ...}` (3-key, no `"validator"`); persist it; `replay_re_execute(artifact, store)` succeeds without raising; result equality holds against the original captured result
  - extra-key-not-in-live: confirm the legacy-compat invariant is symmetric — a captured key not in live (`producer_versions["future_seam"] = "x"`) raises `replay.producer_version_drift: future_seam: ...; live=<missing>`. this is **already** the M9 phase-2 behavior; the test pins it as regression
- existing tests must continue to pass; do **not** modify M9 phase 1 / phase 2 test files

implementation-shape constraints:

- tests must use `tmp_path` and clean up cleanly
- monkey-patches must use `monkeypatch.setattr(extractx.proposals.validation, "algorithmic_code_hash", ...)` so the engine sees the patch
- legacy-compat synthetic artifact construction goes through `ReplayArtifact(...)` directly — do **not** edit prior persisted blobs from M9 phase 1/2 fixtures
- no benchmark / evaluator-only path; tests reuse `replay_re_execute(...)` and the persistence path

## Explicit drifts to acknowledge in the implementation

surface these in code comments or the final report; do not silently invent around them:

1. **gate iteration is captured-keyed (load-bearing for legacy compat)**
   - `for key, captured_value in captured.items(): ...` is the iteration shape. live keys not present in captured are silently skipped. this is what makes drift-gate widening forward-compatible across artifact generations
2. **class-level token, not per-call aggregation**
   - `algorithmic_code_hash()` is the class identity. per-call `field_validation_version` already lives on each `ValidatedField` inside the artifact and surfaces drift via result-mismatch on the existing equality helper. promoting per-call versions to a producer_versions aggregate is a follow-on if class-level proves insufficient

3. **module-level helper only; reference-seam shape inconsistency parked**
   - this thread adds `algorithmic_code_hash()` as a module-level function only; **no class-level `producer_version` property** is added on `LayeredProposalValidator`. the M9 phase 1 capture path consumes the module-level helper uniformly across the three reference seams (C / D / G.resolver). seam G.resolver currently has both a class property and a module helper — that pre-existing double shape is a known inconsistency parked for a coordinator-owned harmonization thread, not unwound here
3. **`ReplayArtifact.schema_version` stays `"v1"`**
   - the addition is forward-compatible on a mapping. a v2 bump would be needed if a future thread changed the type of an existing field, narrowed a union beyond historical contents, or restructured nested fields. forward-note added on `schema_version` per §5
4. **legacy artifacts replay without warnings / diagnostics**
   - phase-1 silent-skip is intentional. a future thread may add a `Reporter` event or diagnostic log when a legacy artifact replays without the validator key, but this thread does not (Reporter is parked behind seam K)
5. **no `Runtime` / executor / strategy / seam changes beyond the targeted ones**
   - `LayeredProposalValidator` gains a sibling helper. `SerialExecutor._persist_run` writes one new key. `replay/engine.py::_live_producer_versions()` gains one entry. nothing else changes

## Guardrails

- **write scope:** `src/extractx/proposals/validation.py` (add `algorithmic_code_hash` helper), `src/extractx/execution/executor/serial.py` (write `"validator"` in `_persist_run`), `src/extractx/replay/engine.py` (extend `_live_producer_versions` + add the load-bearing comment), `src/extractx/replay/artifact.py` (one-paragraph docstring/comment on `schema_version` only — no logic changes), focused tests
- **no docs edits** (`docs/architecture.md`, `docs/adr/`, `CODEX.md`, `CLAUDE.md`, any task brief) — this thread is implementation-only; the seam-K thread carries the §9 / §7 doc updates
- **no `Runtime` changes whatsoever** (`src/extractx/execution/runtime.py` read-only)
- **no `IndependentStrategy` changes** (`src/extractx/execution/strategies/independent.py` read-only)
- **no resolver / planner / candidate-strategy / selector / adapter changes** (the four seam classes besides the validator are read-only; their producer-version helpers stay as-is)
- **no `ReplayArtifact` field-shape change** (only the docstring on `schema_version` is touched; `producer_versions: Mapping[str, str]` stays)
- **no replay reader changes** (`src/extractx/replay/reader.py` read-only)
- **no replay writer changes** (`src/extractx/replay/writer.py` read-only)
- **no widening of `run_extraction(...)` signature**
- **no widening of `extractx.__init__` tier-1 exports**
- **no public api widening** (the new helper is module-level in `validation.py`, not exported from `extractx.__init__`; consumers go through the module if they need it)
- **no `schema_version` bump**
- **no aggregation of per-call `field_validation_version` into a producer-versions key**
- **no legacy-mode policy knob** (silent skip is the only behavior)
- **no Reporter / log emission for legacy artifacts** (parked behind seam K)
- **no dependency changes**
- **no commits or pushes** unless separately asked

## Pushback discipline

if a hard pin contradicts code reality (e.g. one of the three reference seams uses a class-method instead of a module-level helper, the existing executor capture site is structurally different than expected, or the legacy-compat iteration shape was already inverted in an earlier thread), do **not** silently work around it. instead, in the final report under a `## Pushback` heading, write a structured block:

- current contract:
- observed gap or contradiction:
- consequence if implemented as written:
- proposed cleaner pattern:
- seam / ownership impact:
- whether this is clarification vs architecture change:
- proof target:

…and stop coding. the coordinator will adjudicate.

## Focused proof

minimum proof targets:

1. **helper exists and matches reference pattern:** `extractx.proposals.validation.algorithmic_code_hash` is a module-level callable; returns a stable `str`; prefix matches the seam-D / seam-G.resolver pattern (i.e., what `algorithmic_producer_version(...)` emits)
2. **capture coverage:** a freshly-persisted run carries `producer_versions["validator"]` with a value byte-equal to `extractx.proposals.validation.algorithmic_code_hash()` at write time
3. **drift surface:** monkey-patching the validator's `algorithmic_code_hash` to return a divergent string causes `replay_re_execute(...)` to raise `InfrastructureError` whose message starts with `"replay.producer_version_drift: validator: "`
4. **legacy-compat (load-bearing):** a synthetic `ReplayArtifact` with `producer_versions` containing the three pre-this-thread keys (no `"validator"`) replays through `replay_re_execute(...)` without raising, and the reproduced result equals the captured one under the M9 phase-2 equality helper
5. **extra-key regression:** a captured key not present in live (`producer_versions["future_seam"] = "x"`) still raises `replay.producer_version_drift: future_seam: ...; live=<missing>` — the M9 phase-2 invariant stays
6. **gate iteration shape:** white-box read of `replay/engine.py::check_producer_version_drift` confirms the iteration is over `captured.items()` (not `live.items()`); a one-line static check or a comment-grep is acceptable
7. **`schema_version` not bumped:** white-box: `ReplayArtifact.schema_version` is still `Literal["v1"]`. forward-note exists in the docstring/comment
8. **no out-of-scope edits:** diff stat for `src/extractx/execution/runtime.py`, `src/extractx/execution/strategies/independent.py`, `src/extractx/replay/reader.py`, `src/extractx/replay/writer.py`, every non-validator seam class, every doc file is zero in the worker commit
9. **no benchmark-only path:** all tests reach the executor / replay engine via real `run_extraction(...)` (or its `SerialExecutor.execute(...)` equivalent) and real `replay_re_execute(...)`
10. **no class-level `producer_version` property added:** white-box: `LayeredProposalValidator` does not gain a `producer_version` attribute, property, or class method as part of this thread. only the module-level `algorithmic_code_hash()` helper is added. a one-line `hasattr` / `inspect`-based check pins this

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/proposals/validation.py` (new helper)
- `src/extractx/execution/executor/serial.py` (one new key in `_persist_run`)
- `src/extractx/replay/engine.py` (one new live-key entry + load-bearing comment)
- `src/extractx/replay/artifact.py` (schema_version forward-note only — no logic)
- focused tests under `tests/replay/` and `tests/proposals/`

include in your final report:

- exact files changed
- the helper signature as landed (module-level vs class-method) and how it matches the three reference seams
- the executor capture-site (file:line) where `"validator"` writes
- the engine live-key composition (file:line) where `"validator"` joins the dict
- the legacy-compat test path (which file, which assertions)
- confirmation that `schema_version` is unchanged (`grep -c 'Literal."v1"' src/extractx/replay/artifact.py` returns 1)
- any follow-on that should become a coordinator-owned thread instead of widening this one (likely candidates: per-call `field_validation_version` aggregation, Reporter event for legacy-artifact replays, schema_version v2 migration plan)

## Success criteria

- new artifacts carry `producer_versions["validator"]` populated from `LayeredProposalValidator.algorithmic_code_hash()`
- replay drift gate fires `InfrastructureError("replay.producer_version_drift: validator: ...")` on validator divergence
- legacy artifacts (3-key `producer_versions`) replay without raising
- `ReplayArtifact.schema_version` stays `"v1"`; forward-note exists
- gate iteration shape stays captured-keyed (load-bearing for legacy compat)
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`

## Downstream consequences

- once this lands, validator code drift surfaces as a typed pre-gate failure with the correct error class. downstream tooling (CI, regression harness) can pattern-match on `replay.producer_version_drift: validator: ` like it does for the other three keys
- next clean threads (in priority order, all coordinator-owned, none folded into this one):
  1. **seam K phase 1** — type `ExecutionTrace.events` to `tuple[NegativeOutcome, ...]`; delete `_rehydrate_trace` shim; drop `trace.events` exclusion in replay equality; fold the §9 / §7 doc edits
  2. **ADR-0007 status promotion** + residual docs cleanup (one-liner thread, after seam K)
  3. **manifest atomicity / collision** (when there's actual consumer pressure)
  4. **per-call `field_validation_version` aggregation** (only if class-level token proves insufficient)
  5. then breadth (result cache → manual-spec replay → second backend → ...)
- do not fold any of those into this thread
