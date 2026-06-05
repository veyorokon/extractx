# Task: implement api-redesign phase 1 — schema-first `extract(...)` function

*This is the first phase of the user-facing api redesign. the engine is real (M8 + F.layer3 + M9 phase 1 + M9 phase 2 + drift-gate phase 1) but the user-facing call shape is too internal: callers construct `ExtractionSpec` / `Runtime` / `ExecutorPolicy` / pass `strategy="independent"` to invoke a four-line setup. introduce **exactly one** schema-first sugar function — `extract(document, schema, *, store=None, capture_interviews=False) -> ExtractionResult` — that compiles the internal seams away from the happy path. no `Extractor`, no `ExtractOptions`, no `extract_one`, no `extract_many`, no `mode`/`strategy` knob, no widening of `run_extraction(...)` or any engine contract. `run_extraction(...)` stays intact as the advanced/engine api for plugin authors and tests.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§10 three-tier public surface** (tier-1 vs tier-2 vs internal; `extract` widens tier-1 by one symbol), **§13 public api surface** (current wording lists `run_extraction` as the user-facing entry; this thread does **not** edit that wording — doc cleanup is a follow-on), **§15 anti-patterns** (`Benchmark-Only Execution Path`, `Duplicate Overlapping Path`, `Silent None`)
- [`docs/tasks/m8-phase-1-serial-independent-vertical-slice.md`](m8-phase-1-serial-independent-vertical-slice.md) — current `run_extraction` shape; `SerialExecutor` construction; `ExecutorPolicy` defaults
- [`docs/tasks/m9-phase-1-replay-storage-skeleton.md`](m9-phase-1-replay-storage-skeleton.md) — `SerialExecutor.__init__(*, storage=None)` opt-in persistence; `extract`'s `store` parameter threads through
- [`src/extractx/api.py`](../../src/extractx/api.py) — current `run_extraction(...)` lives here; the new `extract(...)` lands here too (colocate the user-facing surface in one module)
- [`src/extractx/__init__.py`](../../src/extractx/__init__.py) — tier-1 exports; widens by one symbol (`extract`) only
- [`src/extractx/execution/executor/serial.py`](../../src/extractx/execution/executor/serial.py) — `SerialExecutor.__init__(*, storage)` and the `capture_interview_transcripts` pre-run gate; both flow through `extract`'s parameters
- [`src/extractx/execution/policy.py`](../../src/extractx/execution/policy.py) — `ExecutorPolicy` shape; current defaults
- [`src/extractx/core/objects.py`](../../src/extractx/core/objects.py) — `ExtractionSpec.from_pydantic(...)` classmethod
- [`src/extractx/storage/protocol.py`](../../src/extractx/storage/protocol.py) — `ExtractxStore` Protocol (the type for `store`)

## Goal

implement exactly one new public function:

```python
async def extract(
    document: str | bytes,
    schema: type[BaseModel],
    *,
    store: ExtractxStore | None = None,
    capture_interviews: bool = False,
) -> ExtractionResult: ...
```

that internally compiles to the current engine surface:

```python
spec = ExtractionSpec.from_pydantic(schema)
runtime = Runtime()
policy = ExecutorPolicy(
    strategy="independent",
    capture_interview_transcripts=capture_interviews,
)
executor = SerialExecutor(storage=store)
return await executor.execute(document, spec, runtime, policy)
```

without:
- adding `Extractor` / `ExtractOptions` / `extract_one` / `extract_many` / a `mode` or `strategy` knob
- widening `run_extraction(...)` signature
- widening `ExecutorPolicy` / `Runtime` / `SerialExecutor.__init__(...)`
- changing the engine path
- adding a `str → LocalFilesystemStore` polymorphism on `store`
- editing docs (architecture / ADRs / CODEX)

`run_extraction(...)` stays as-is for plugin authors, advanced callers, and tests.

**"done" in one sentence:** `from extractx import extract` works; `await extract(doc, Invoice)` returns an `ExtractionResult` byte-equal under pydantic structural equality to the same call routed through the explicit four-line `run_extraction(...)` setup; passing `store=LocalFilesystemStore(...)` opts into the M9 persistence path; passing `capture_interviews=True` raises `InfrastructureError` (executor's pre-run gate).

## Scope

numbered implementation areas. do each in order.

### 1. land `extract(...)` in `src/extractx/api.py`

requirements:

- function signature exactly:
  ```python
  async def extract(
      document: str | bytes,
      schema: type[BaseModel],
      *,
      store: ExtractxStore | None = None,
      capture_interviews: bool = False,
  ) -> ExtractionResult: ...
  ```
- the function lives in `src/extractx/api.py` next to `run_extraction(...)`. **do not create a new module** for it; colocate the user-facing surface
- internal compilation:
  ```python
  spec = ExtractionSpec.from_pydantic(schema)
  runtime = Runtime()
  policy = ExecutorPolicy(
      strategy="independent",
      capture_interview_transcripts=capture_interviews,
  )
  executor = SerialExecutor(storage=store)
  return await executor.execute(document, spec, runtime, policy)
  ```
- the function is async; callers `await` it (mirrors `run_extraction`)
- on success, returns `ExtractionResult` exactly as `executor.execute(...)` produced it (no post-processing; `replay_artifact_ref` is `""` when `store is None`, content-hash id when `store` is set, per M9 phase 1)
- on `capture_interviews=True`, the executor's pre-run gate raises `InfrastructureError` ("phase-1 does not implement interview capture; ExecutorPolicy.capture_interview_transcripts must remain False until the capture thread lands"). `extract` does not catch or rewrite this — the error propagates to the caller as-is. this is honest phase-1 behavior

implementation-shape constraints:

- do **not** call `run_extraction(...)` from inside `extract(...)`. `run_extraction` constructs `SerialExecutor()` without a `storage` parameter; `extract(...)` needs to thread `store` through. the two functions construct their own executors and call `.execute(...)` directly. this is a small parallel construction path, but it preserves `run_extraction`'s signature exactly (no widening) and the divergence is bounded to one extra line
- do **not** introduce a `str → LocalFilesystemStore` polymorphism on `store`. callers pass an `ExtractxStore` instance or `None`. constructing the store with a path is the caller's responsibility
- do **not** introduce `extract_one` / `extract_many` / `Extractor` / `ExtractOptions` in this thread. each is a follow-on with its own justification
- do **not** widen `extract`'s signature with `runtime: Runtime | None = None` or `policy: ExecutorPolicy | None = None`. those exist as the engine api (`run_extraction`); `extract` is the schema-first surface
- do **not** add a `strategy` / `mode` knob. the only strategy that exists today is `"independent"`; phase-1 hard-codes it. a future thread that lands `IterativeStrategy` will add the knob with real semantics
- do **not** rewrite or wrap `InfrastructureError` raised by the executor's pre-run gate. it propagates as-is

### 2. tier-1 export

requirements:

- add `extract` to `src/extractx/__init__.py`:
  - `from .api import extract, run_extraction` (extend the existing line)
  - add `"extract"` to `__all__` (alphabetical position)
- the function is **end-user public** per architecture §10 (the new schema-first entry alongside `run_extraction`)
- no other tier-1 surface widens in this thread

implementation-shape constraints:

- do **not** add `Extractor` / `ExtractOptions` / `extract_one` / `extract_many` to `__all__`. they don't exist yet
- do **not** re-export `ExtractxStore` or `LocalFilesystemStore` from tier-1. callers who want persistence import from `extractx.storage` (internal); promoting storage types to tier-1 is a separate decision

### 3. tests

land focused tests under `tests/api/` (new directory; mirrors the future expansion of api surfaces) or extend `tests/integration/`. worker chooses; pin which file in the report.

minimum proof targets:

- **surface present:** `from extractx import extract` works; `inspect.iscoroutinefunction(extract)` is true; signature matches the brief verbatim (use `inspect.signature` to verify parameter names + defaults + kinds)
- **happy-path equivalence:** `await extract(doc, Invoice)` returns an `ExtractionResult` structurally equal to the same run routed through the explicit `run_extraction(...)` four-line setup. small pydantic-backed spec + small text input
- **storage opt-in:** `await extract(doc, Invoice, store=LocalFilesystemStore(tmp_path))` populates `replay_artifact_ref` (non-empty) and writes the M9 phase-1 layout (`objects/source/`, `objects/spec/`, `objects/replay/`, `runs/`). same byte content as the explicit `SerialExecutor(storage=...).execute(...)` path
- **storage opt-out:** `await extract(doc, Invoice)` (no `store`) leaves `replay_artifact_ref == ""`; no filesystem writes occur
- **`capture_interviews=True` raises:** `await extract(doc, Invoice, capture_interviews=True)` raises `InfrastructureError`. proof shape: assert `isinstance(exc, InfrastructureError)` AND a stable substring like `"interview capture" in str(exc)`. **do not full-string-match the message** — the executor's pinned wording may evolve and full-match is brittle
- **`run_extraction` is unchanged:** the existing `run_extraction(...)` signature, behavior, and tests continue to pass byte-identically. white-box: `inspect.signature(run_extraction)` returns the same parameter set as before (no new parameters)
- **manual-spec via `extract` is rejected by `from_pydantic`:** passing a non-`BaseModel` class to `extract(doc, NotAModel)` surfaces a `SpecError` (or whatever `ExtractionSpec.from_pydantic` raises today on non-pydantic input). do not catch / rewrite

implementation-shape constraints:

- tests must use `tmp_path` for storage tests; clean up cleanly
- no benchmark-only execution path — tests use real `extract(...)` and real `run_extraction(...)`, no parallel pipeline (architecture §15 anti-pattern `Benchmark-Only Execution Path`)
- structural equality check on `ExtractionResult` excludes the same fields M9 phase 2's equality helper excludes when comparing across executor invocations (specifically: `replay_artifact_ref` differs when one path persists and the other does not). reuse the helper if it's available; otherwise a small ad-hoc comparison is fine

### 4. backward-compatibility check

requirements:

- existing tests that import `run_extraction` from `extractx.__init__` continue to import cleanly
- existing tests that exercise the four-line setup (`spec / runtime / policy / run_extraction(...)`) continue to pass byte-identically
- this is a regression check, not a code change

implementation-shape constraints:

- do **not** modify any existing test
- do **not** modify `run_extraction(...)` in `src/extractx/api.py`
- do **not** modify any existing seam class, executor, strategy, runtime, policy, or core type

## Explicit drifts to acknowledge in the implementation

surface these in code comments or the final report; do not silently invent around them:

1. **parallel executor construction in `extract` and `run_extraction`**
   - `extract(...)` constructs `SerialExecutor(storage=store)`; `run_extraction(...)` constructs `SerialExecutor()` (no storage). both call `.execute(...)`. small divergence; the alternative (calling `run_extraction` from `extract`) would require widening `run_extraction`'s signature with `store=`, which the user explicitly forbade in this thread. acknowledged trade-off: the user-facing surface stays clean at the cost of one extra line of construction in each function
2. **pydantic-backed only**
   - `extract(schema: type[BaseModel])` accepts pydantic classes only. manual `ExtractionSpec` callers continue to use `run_extraction(...)`. this is consistent with M9 phase 2's pydantic-only replay scope
3. **storage opaque to `extract`**
   - `store: ExtractxStore | None = None` accepts a constructed store instance only. no `str → LocalFilesystemStore` polymorphism; that's caller responsibility. keeps `extract`'s surface narrow
4. **`capture_interviews=True` propagates the executor's pre-run-gate `InfrastructureError`**
   - the M8 executor gates `capture_interview_transcripts=True` to a typed setup-time failure. `extract` does not catch or rewrite this; callers see the executor's pinned message verbatim. honest phase-1 behavior
5. **architecture §13 wording is stale-but-not-edited in this thread**
   - architecture §13 currently lists `run_extraction` as "the single function exposed to end users." adding `extract` makes that wording slightly stale ("two functions"). doc updates are deferred to a separate thread (the ADR-0007 status promotion + residual docs cleanup thread queued after seam K phase 1). this thread is **implementation-only**
6. **`run_extraction` becomes the advanced/engine api de facto**
   - by introducing `extract` as the schema-first sugar, `run_extraction` retroactively becomes "the four-line explicit construction path." it stays available, semver-stable, and used by tests / plugin authors / advanced callers. no deprecation, no soft-removal in this thread

7. **`Runtime()` bare construction; soft-compute path implicit**
   - phase-1 algorithmic slice consumes no env-bound capabilities, so `Runtime()` and `Runtime.from_env()` are equivalent today (per the M8 runtime.py docstring). when soft-compute lands and `from_env()` widens to read provider keys, callers who need env-bound `Runtime` ergonomics under `extract(...)` will either use `run_extraction(...)` (the engine path, where they construct `Runtime.from_env()` themselves) or a future `extract(...)` widening (e.g. `runtime: Runtime | None = None` parameter, or env-reading-on-default). this thread does **not** pre-decide that future ergonomic. the trade-off is named here so the soft-compute thread inherits the open question rather than discovering it as a surprise

## Guardrails

- **write scope:** `src/extractx/api.py` (add `extract` function next to `run_extraction`), `src/extractx/__init__.py` (add `extract` to imports + `__all__`), focused tests under `tests/api/` or `tests/integration/`
- **no docs edits** (`docs/architecture.md`, `docs/adr/`, `CODEX.md`, `CLAUDE.md`, any task brief) — this thread is implementation-only; doc updates (§13 wording stale-flag, etc.) belong to a separate cleanup thread
- **no widening of `run_extraction(...)` signature**
- **no widening of `ExecutorPolicy` / `Runtime` / `SerialExecutor.__init__(...)`**
- **no `Extractor` class**
- **no `ExtractOptions` container**
- **no `extract_one` / `extract_many` functions**
- **no `mode` / `strategy` knob**
- **no `str → LocalFilesystemStore` polymorphism on `store`**
- **no new exceptions** (existing `SpecError` / `InfrastructureError` / `CapabilityError` propagate as-is)
- **no engine / seam / strategy / executor changes**
- **no manifest / replay / storage logic changes**
- **no `pyproject.toml` changes**
- **no commits or pushes** unless separately asked

## Pushback discipline

if a hard pin contradicts code reality (e.g. `SerialExecutor.__init__(*, storage=...)` doesn't exist as expected, or `ExecutorPolicy` has changed shape since M8), do **not** silently work around it. instead, in the final report under a `## Pushback` heading, write a structured block:

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

1. **surface present and async:** `from extractx import extract` works; `inspect.iscoroutinefunction(extract)` is true; `inspect.signature(extract)` returns parameters `(document, schema, *, store=None, capture_interviews=False)` with the expected types
2. **tier-1 export:** `extract` appears in `extractx.__all__` (alphabetical position)
3. **happy-path equivalence:** `await extract(doc, Invoice)` returns an `ExtractionResult` structurally equal to the same run constructed via explicit `spec = ExtractionSpec.from_pydantic(Invoice); runtime = Runtime(); policy = ExecutorPolicy(strategy="independent"); await run_extraction(doc, spec, runtime, policy)`. comparison excludes `replay_artifact_ref` (both should be `""` in the no-store case)
4. **storage opt-in:** `await extract(doc, Invoice, store=LocalFilesystemStore(tmp_path))` populates `replay_artifact_ref` (non-empty); writes M9 phase-1 layout under `tmp_path`; same artifact bytes as the explicit `SerialExecutor(storage=...).execute(...)` path
5. **storage opt-out:** `await extract(doc, Invoice)` returns `replay_artifact_ref == ""`; no filesystem writes
6. **`capture_interviews=True` raises:** the executor's pre-run gate fires `InfrastructureError`; `extract` propagates verbatim. proof shape: `isinstance(exc, InfrastructureError)` AND `"interview capture" in str(exc)` (substring match — not full-string)
7. **`run_extraction` unchanged:** `inspect.signature(run_extraction)` returns the same parameters as before (no `store`, no `schema`); existing tests using `run_extraction` continue to pass
8. **pydantic-backed only:** `await extract(doc, NonBaseModelClass)` raises `SpecError` (or whatever `ExtractionSpec.from_pydantic` raises today on non-pydantic input); the error is not caught or rewritten
9. **no out-of-scope edits:** zero-line diff for `src/extractx/execution/runtime.py`, `src/extractx/execution/policy.py`, `src/extractx/execution/executor/serial.py`, `src/extractx/execution/strategies/independent.py`, every seam-class file, every `core/` file, every `replay/` file, every `storage/` file, every `schema/` file, every doc file
10. **no benchmark-only path:** all tests use real `extract(...)` and real `run_extraction(...)`; no parallel pipeline

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/api.py` (one new function)
- `src/extractx/__init__.py` (one new export)
- focused tests under `tests/api/` (new directory) or `tests/integration/`

include in your final report:

- exact files changed
- the `extract(...)` signature as landed (vs the brief's signature — surface drift if any)
- where the function lives (`src/extractx/api.py`:line)
- the test file(s) added
- confirmation that `run_extraction(...)` signature is unchanged (zero-line diff on the function definition)
- confirmation that no engine / seam / executor / runtime / policy / replay / storage code was modified
- any follow-on that should become a coordinator-owned thread instead of widening this one (likely candidates: §13 doc wording update, `extract_one` once `to_pydantic()` lands, `Extractor` once amortizable state shows up, `ExtractOptions` once ≥5 knobs exist)

## Success criteria

- `extract` exists in `src/extractx/api.py`, exported from `extractx.__init__`
- happy-path equivalence with `run_extraction(...)` four-line setup is proven structurally
- storage opt-in writes the M9 phase-1 layout
- `capture_interviews=True` raises the executor's pinned `InfrastructureError`
- `run_extraction` and existing tests are unchanged
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`

## Downstream consequences

- once this lands, the schema-first happy path is one function call. callers no longer carry engine vocabulary into application code unless they need it
- `run_extraction(...)` retroactively becomes "the explicit four-line engine path" — semver-stable, available for plugin authors / tests / advanced callers
- next clean threads (in priority order, all coordinator-owned, none folded into this one):
  1. **seam K phase 1** — type `ExecutionTrace.events`, delete `_rehydrate_trace` shim, drop `trace.events` exclusion in replay equality, fold §9 / §7 doc edits
  2. **ADR-0007 status promotion + residual docs cleanup** — including the §13 "single function" wording update to reflect `extract` as the new schema-first entry
  3. **manifest atomicity / collision** (when there's actual consumer pressure)
  4. **`extract_one(...)`** — once `ExtractionResult.to_pydantic(Cls)` lands; thin wrapper that materializes + raises `ExtractionFailed(result=...)` on partial/failed (new exception class, public-surface decision)
  5. **`Extractor` class** — once amortizable state actually exists (model client warmup, store connection pool, registry warmup)
  6. **`ExtractOptions` container** — once ≥5 knobs exist that warrant the bag
  7. then breadth (result cache → manual-spec replay → second backend → ...)
- do not fold any of those into this thread

**future-thread guardrail (load-bearing for the next API thread):** when `extract_one` / `extract_many` / `Extractor` land, they must compile through `extract(...)` (or `extract`'s internal compilation steps), **not** duplicate the spec/runtime/policy/executor construction in their own bodies. otherwise drift between sugar layers becomes inevitable.
