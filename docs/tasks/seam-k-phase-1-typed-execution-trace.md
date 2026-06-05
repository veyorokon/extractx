# Task: implement seam K phase 1 — typed `ExecutionTrace.events` and honest replay trace equality

*This is the first seam-K tightening thread after M9 phase 2 and replay drift-gate phase 1. The replay contract now proves the answer on the supported path, but not the forensic trace: `ExecutionTrace.events` is still a placeholder `tuple[Any, ...]`, replay reader carries a `_rehydrate_trace` shim, and replay equality excludes `trace.events`. Keep this thread narrow: tighten the trace contract to the shape the landed code actually emits today, remove the shim, drop the equality exclusion, and make the corresponding architecture wording honest. Do not widen replay storage, producer-version capture, result cache, or reporter behavior.*

## Read first

The exec agent starts cold. Read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; replay/debugging notes; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam H** (replay contract), **§7 seam K** (reporter/trace semantics), **§9 canonical objects** (`ExecutionTrace`, `ExtractionResult`), **§10 three-tier public surface**, **§13 public api surface**, **§17 done criteria**
- [`docs/tasks/m9-phase-1-replay-storage-skeleton.md`](m9-phase-1-replay-storage-skeleton.md) — three named equalities and replay/storage scope
- [`docs/tasks/m9-phase-2-replay-re-execution.md`](m9-phase-2-replay-re-execution.md) — source-driven replay engine and current equality helper
- [`docs/tasks/replay-drift-gate-phase-1-validator-coverage.md`](replay-drift-gate-phase-1-validator-coverage.md) — landed replay drift-gate behavior; read so this thread does not overlap it
- [`src/extractx/core/outcomes.py`](../../src/extractx/core/outcomes.py) — current `ExecutionTrace` placeholder
- [`src/extractx/replay/reader.py`](../../src/extractx/replay/reader.py) — current `_rehydrate_trace` shim
- [`src/extractx/replay/engine.py`](../../src/extractx/replay/engine.py) — current replay equality helper and trace exclusion
- [`src/extractx/execution/executor/serial.py`](../../src/extractx/execution/executor/serial.py) — current trace assembly on the supported path
- [`tests/integration/test_pydantic_seam_f_handoff.py`](../../tests/integration/test_pydantic_seam_f_handoff.py) — current live-path trace-event shape proof
- [`tests/replay/test_source_driven_replay.py`](../../tests/replay/test_source_driven_replay.py) — current replay equality/exclusion surface

## Goal

Land the narrowest honest seam-K phase-1 contract:

- `ExecutionTrace.events` becomes a typed tuple matching the only event shape emitted on the supported path today
- replay reader no longer needs `_rehydrate_trace`
- replay equality no longer excludes `trace.events`
- architecture wording is tightened only in the sections this lane owns

without:
- changing reporter semantics
- adding new event kinds
- widening public api
- touching drift-gate / producer-version logic
- introducing result-cache or storage changes

**"Done" in one sentence:** on the supported path, `ExecutionTrace.events` is typed to the actual emitted event shape, replay reads it without a shim, replay equality compares it directly, and the owned sections of `docs/architecture.md` no longer overstate or understate the trace contract.

## Scope

### 1. Tighten the `ExecutionTrace` object shape

Requirements:

- change `ExecutionTrace.events` in [`src/extractx/core/outcomes.py`](../../src/extractx/core/outcomes.py) from `tuple[Any, ...]` to the narrowest honest phase-1 type
- the type must match the currently emitted event shape on the supported path; do **not** invent a union for future event kinds that are not emitted today
- the current supported-path live event shape is the one already proven in [`test_pydantic_seam_f_handoff.py`](../../tests/integration/test_pydantic_seam_f_handoff.py): typed `NegativeOutcome` payloads
- keep `ExecutionTrace` frozen and otherwise unchanged

Implementation-shape constraints:

- do **not** widen `ExecutionTrace` with additional fields
- do **not** add a schema-version bump
- do **not** change `ArtifactRef`, `ExtractionResult`, or any proposal/result lifecycle object in this step

### 2. Delete replay-side trace rehydration shim

Requirements:

- remove `_rehydrate_trace`-style special casing from [`src/extractx/replay/reader.py`](../../src/extractx/replay/reader.py)
- replay reader should deserialize directly into the now-typed `ExecutionTrace.events` shape
- legacy M9 phase-1 / phase-2 artifacts on disk that serialized `events` as raw dicts fail loudly with a typed exception named `replay.incompatible_trace_payload: <reason>` — there is **no** compat shim, no quiet rebuild path, no `Any` fallback
- any in-tree replay fixtures from M9 phase 1 / phase 2 that contain dict-shaped events are regenerated as part of this thread's test delta; do not coerce them in-flight

Implementation-shape constraints:

- do **not** introduce a new compatibility layer elsewhere to replace the shim
- do **not** add a generic `Any` fallback path
- do **not** widen replay reader to support speculative future event kinds

### 3. Drop `trace.events` exclusion from replay equality

Requirements:

- update [`src/extractx/replay/engine.py`](../../src/extractx/replay/engine.py) replay equality helper(s) so `trace.events` participates directly in equality
- preserve the already-pinned exclusion for `replay_artifact_ref`
- keep the current result-structural equality framing otherwise unchanged
- this brief asserts that on the supported path, every field of `NegativeOutcome` is deterministic across re-execution (no timestamps, no monotonic counters, no run-scoped instance ids that vary). if the worker discovers a non-deterministic field while landing this thread, **stop and surface as pushback** — do not silently re-introduce a `trace.events` exclusion

Implementation-shape constraints:

- do **not** widen equality into full byte-stream result equality
- do **not** add new exclusions
- do **not** couple this thread to drift-gate logic or producer-version map changes

### 4. Tighten only the owned architecture sections

This lane owns the following sections in [`docs/architecture.md`](../architecture.md):

- **§7 seam H — trace / replay-equality half only**
- **§7 seam K — trace-semantics wording only (no reporter behavior widening)**
- **§9 `ExecutionTrace`**

Requirements:

- tighten wording in the owned half of §7 seam H to match the landed seam-K phase-1 contract (typed `events`, no shim, replay equality includes `trace.events` modulo `replay_artifact_ref`)
- in §7 seam K, tighten wording where it currently overstates phase-1 trace semantics; do **not** introduce or widen reporter behavior in either text or scope
- if "bytewise" wording in the owned half of §7 seam H currently overstates what replay equality proves, narrow it honestly
- if `ExecutionTrace.events` wording in §9 is placeholder-shaped or stale, update it to the now-landed typed shape

Implementation-shape constraints:

- **do not edit the storage-wording half of §7 seam H** — Lane C owns it for ADR-0007 promotion. if a trace-side edit forces a co-located storage-wording change, surface as drift and stop short of editing through the boundary
- **do not edit** `docs/architecture.md` §10, §13, §15, §17 — Lane C owns those
- **do not edit** ADRs, `CODEX.md`, task indexes, or any other docs-hygiene file — Lane C owns those
- if you find a needed edit outside the owned sections, surface it in the final report under `## Drift` and stop short of editing it

### 5. Tests

Land focused tests proving:

- live executor path still emits typed trace events in the same supported-path scenarios
- replay read path no longer needs a shim
- replay equality now includes `trace.events`
- existing replay / persistence / drift-gate suites still pass

Minimum proof targets:

1. **typed core shape:** `ExecutionTrace.events` annotation is no longer `tuple[Any, ...]`
2. **shim deleted:** no `_rehydrate_trace` helper remains in [`src/extractx/replay/reader.py`](../../src/extractx/replay/reader.py)
3. **live-path event shape:** the existing supported-path integration test still proves a typed `NegativeOutcome` event appears in `trace.events`
4. **non-empty trace-events round-trip:** at least one replay-equality test in this thread covers a scenario where `trace.events` is non-empty (e.g., a candidate that yields a `NegativeOutcome`) and asserts equality directly — without the exclusion, with typed `NegativeOutcome` payloads on both sides
5. **legacy artifact rejection:** at least one test proves a legacy dict-shaped `events` payload raises `replay.incompatible_trace_payload` on read, not a silent coercion
6. **replay round-trip still passes:** M9 phase 1 + M9 phase 2 + drift-gate replay suites remain green (after fixture regeneration where required)
7. **no public-surface widening:** `extractx.__all__` unchanged in this thread

## Guardrails

- **write scope:** `src/extractx/core/outcomes.py`, `src/extractx/replay/reader.py`, `src/extractx/replay/engine.py`, focused tests + regenerated replay fixtures where the legacy-artifact policy requires it, and only the owned sections of [`docs/architecture.md`](../architecture.md) (trace half of §7 seam H + trace-semantics wording in §7 seam K + §9)
- **do not edit** `docs/architecture.md` outside the owned sections, including the storage-wording half of §7 seam H (Lane C owns that for ADR-0007 promotion)
- **do not edit** `CODEX.md`, ADRs, task index, or any docs-hygiene file; Lane C owns those
- **no changes** to `src/extractx/execution/runtime.py`
- **no changes** to `src/extractx/execution/policy.py`
- **no changes** to `src/extractx/execution/executor/serial.py` unless a test-only import or comment is absolutely required; if required, surface pushback first
- **no changes** to replay drift-gate logic or producer-version keys
- **no schema-version bump**
- **no public api widening**
- **no reporter feature expansion**
- **no commits or pushes** unless separately asked

## Pushback discipline

If a hard pin contradicts code reality, do **not** silently work around it. In the final report, add:

- current contract:
- observed gap or contradiction:
- consequence if implemented as written:
- proposed cleaner pattern:
- seam / ownership impact:
- whether this is clarification vs architecture change:
- proof target:

and stop coding.

## Deliverable

Code, focused tests, and the owned `docs/architecture.md` section updates in the repo.

Include in the final report:

- exact files changed
- the landed `ExecutionTrace.events` type
- confirmation that `_rehydrate_trace` (or equivalent shim) is deleted
- the replay-equality helper location where `trace.events` re-enters comparison
- the exact `docs/architecture.md` sections edited
- any doc drift found outside the owned sections

## Success criteria

- `ExecutionTrace.events` is typed to the actual supported-path event shape
- replay reader no longer carries the trace rehydration shim
- replay equality no longer excludes `trace.events`
- supported-path replay / persistence / drift-gate tests still pass
- only owned sections of `docs/architecture.md` were edited
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`

## Downstream consequences

- once this lands, replay proves both the answer and the current phase-1 forensic trace on the supported path
- the next clean docs thread can update the remaining architecture/public-surface wording without carrying seam-K implementation decisions
- after this closes, Lane A can safely pick up `to_pydantic` materialization without carrying replay-trace looseness forward
