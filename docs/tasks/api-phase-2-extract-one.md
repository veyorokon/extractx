# Task: implement api phase 2 — `extract_one(...)` materializing helper

*Lane A / product-surface implementation. `extract(...)` is the schema-first result path and `to_pydantic(...)` now materializes canonical `InstanceResult`s into pydantic models. This thread adds the first narrow materializing convenience: `extract_one(document, schema, *, store=None, capture_interviews=False) -> BaseModel`. It compiles through `extract(...)`, not through a parallel engine path, and raises a typed public exception carrying the full `ExtractionResult` when one materialized object is not available.*

## Read first

- [`AGENTS.md`](../../AGENTS.md)
- [`CODEX.md`](../../CODEX.md)
- [`CLAUDE.md`](../../CLAUDE.md)
- [`docs/architecture.md`](../architecture.md) — §10 public surface, §13 public api surface, §15 anti-patterns (`Duplicate Overlapping Path`, `Silent None`, `Canonical/Derived Smear`, `Benchmark-Only Execution Path`)
- [`docs/tasks/api-phase-1-extract-function.md`](api-phase-1-extract-function.md) — future-thread guardrail: materializing helpers must compile through `extract(...)`
- [`docs/tasks/to-pydantic-phase-1-materialization.md`](to-pydantic-phase-1-materialization.md) — `.to_pydantic(...)` contract; result projection may return `[]` for failed results
- [`src/extractx/api.py`](../../src/extractx/api.py)
- [`src/extractx/core/exceptions.py`](../../src/extractx/core/exceptions.py)
- [`src/extractx/__init__.py`](../../src/extractx/__init__.py)
- [`tests/api/test_extract_function.py`](../../tests/api/test_extract_function.py)
- [`tests/schema/test_to_pydantic.py`](../../tests/schema/test_to_pydantic.py)

## Goal

Add exactly one materializing public helper:

```python
async def extract_one(
    document: str | bytes,
    schema: type[BaseModel],
    *,
    store: ExtractxStore | None = None,
    capture_interviews: bool = False,
) -> BaseModel: ...
```

and one typed public exception:

```python
class ExtractionFailed(Exception):
    result: ExtractionResult
```

`extract_one(...)` should make the common happy path terse:

```python
invoice = await extract_one(doc, Invoice)
```

without hiding failure evidence. If extraction does not produce exactly one materialized object, callers get `ExtractionFailed` with the full `ExtractionResult` attached.

**"done" in one sentence:** `await extract_one(doc, Invoice)` returns the single materialized `Invoice` on the complete one-instance path, while failed, partial, and multi-instance outcomes raise `ExtractionFailed(result=...)`; no new execution path is introduced.

## Scope

### 1. add `ExtractionFailed`

Location: [`src/extractx/core/exceptions.py`](../../src/extractx/core/exceptions.py)

Requirements:

- define `class ExtractionFailed(Exception)`.
- constructor signature:
  ```python
  def __init__(self, message: str, *, result: ExtractionResult) -> None: ...
  ```
- attach `self.result = result`.
- do not make it a pydantic model.
- use `TYPE_CHECKING` or local imports if needed to avoid import cycles.
- update module docstring's exception list from four to five public exception types.
- export it from:
  - `src/extractx/core/__init__.py`
  - `src/extractx/__init__.py`
- add `"ExtractionFailed"` to `extractx.__all__` in alphabetical order.

Failure message prefix:

- all `extract_one(...)`-raised failures use prefix `"extract_one.failed: "`.
- keep messages diagnostic but short; the attached `result` is the authority.

### 2. implement `extract_one(...)`

Location: [`src/extractx/api.py`](../../src/extractx/api.py)

Requirements:

- signature exactly:
  ```python
  async def extract_one(
      document: str | bytes,
      schema: type[BaseModel],
      *,
      store: ExtractxStore | None = None,
      capture_interviews: bool = False,
  ) -> BaseModel: ...
  ```
- implementation must call `await extract(document, schema, store=store, capture_interviews=capture_interviews)`.
- do **not** duplicate `ExtractionSpec.from_pydantic(...)`, `Runtime()`, `ExecutorPolicy(...)`, or `SerialExecutor(...)` construction.
- after `extract(...)` returns:
  1. if `result.outcome != "complete"` or any `instance.outcome != "complete"`, raise `ExtractionFailed("extract_one.failed: extraction outcome was ...", result=result)`.
  2. call `items = result.to_pydantic(schema)`.
  3. if `len(items) != 1`, raise `ExtractionFailed("extract_one.failed: expected exactly one materialized instance; got ...", result=result)`.
  4. return `items[0]`.
- let `SpecError`, `InfrastructureError`, and materialization `SpecError` from `to_pydantic(...)` propagate unchanged. `ExtractionFailed` is for run outcome / object-count failure after a run result exists, not for setup or schema-contract errors.
- no `extract_many(...)` in this thread.

Design note:

- `ExtractionResult.to_pydantic(schema)` returning `[]` for a failed result is correct for a projection over canonical `instances`.
- `extract_one(...)` is a convenience API with a stricter promise. It treats failed/partial/zero/many as an exception because its return type promises one object.

### 3. docs updates for public surface

Update only live public-surface docs:

- [`docs/architecture.md`](../architecture.md)
  - §10 public surface table: add `extract_one` and `ExtractionFailed` to end-user public.
  - §13 public api surface: add a short `extract_one(...)` subsection after schema-first `extract(...)`.
  - §13 exception taxonomy: add `ExtractionFailed`.
  - §15 anti-patterns if needed: state materializing helpers compile through `extract(...)`; no parallel sugar pipeline.
- [`CODEX.md`](../../CODEX.md)
  - main entrypoints line should mention `extract_one(...)` as the single-object materializing helper, while keeping `extract(...)` as the result path and `run_extraction(...)` as the engine path.
- [`docs/tasks/README.md`](README.md)
  - add this task to the index.

Do not rewrite old historical task briefs that say `extract_one` was deferred. Those are record-of-ask artifacts.

### 4. tests

Add focused tests under `tests/api/` or extend `tests/api/test_extract_function.py`.

Minimum proof targets:

1. **surface present and async:** `from extractx import extract_one`; `inspect.iscoroutinefunction(extract_one)`; signature matches exactly.
2. **tier-1 export:** `extract_one` and `ExtractionFailed` appear in `extractx.__all__`; `extractx.extract_one is extractx.api.extract_one`.
3. **happy path:** `await extract_one(doc, Schema)` returns an instance of `Schema` with expected values.
4. **compiles through `extract(...)`:** monkeypatch `extractx.api.extract` with an async spy returning a constructed `ExtractionResult`; assert `extract_one(...)` calls it with the same `document`, `schema`, `store`, and `capture_interviews`.
5. **failed outcome raises:** a no-match document raises `ExtractionFailed`; `exc.value.result.outcome == "failed"` and the full `ExtractionResult` is attached.
6. **partial outcome raises:** a document/spec producing a partial result raises `ExtractionFailed`; attached result is partial.
7. **many materialized objects raises:** a constructed or real result with two complete instances causes `ExtractionFailed` after materialization; attached result is the original result.
8. **setup errors propagate:** non-`BaseModel` schema still raises `SpecError`, not `ExtractionFailed`.
9. **storage threads through:** passing `store=LocalFilesystemStore(tmp_path)` returns a model and the spy or resulting attached behavior proves persistence still flows through `extract(...)` (prefer spy for call-through proof; avoid a second storage assertion if existing extract tests already cover storage).
10. **smoke surface:** update `tests/smoke/test_import.py` so the minimal public import surface includes callable `extract_one`.
11. **no out-of-scope edits:** zero-line diff for execution, replay, storage, schema materializer internals, and old historical task briefs.

## Guardrails

- **write scope:** `src/extractx/api.py`, `src/extractx/core/exceptions.py`, `src/extractx/core/__init__.py`, `src/extractx/__init__.py`, focused tests under `tests/api/`, `docs/architecture.md`, `CODEX.md`, `docs/tasks/README.md`.
- no engine / executor / strategy changes.
- no replay / storage implementation changes.
- no `extract_many(...)`.
- no `Extractor`.
- no `ExtractOptions`.
- no `mode` / `strategy` knob.
- no new result-shape fields.
- no mutation of `ExtractionResult`.
- no commits or pushes unless separately asked.

## Pushback discipline

If a hard pin contradicts code reality, stop and report:

- current contract:
- observed gap or contradiction:
- consequence if implemented as written:
- proposed cleaner pattern:
- seam / ownership impact:
- clarification vs architecture change:
- proof target:

Pushback examples:

- adding `ExtractionFailed.result` creates an import cycle that cannot be solved cleanly with `TYPE_CHECKING`.
- `extract_one(...)` cannot call `extract(...)` without losing required storage/capture behavior.
- existing public docs define exactly four exception types as a hard invariant that must be amended by ADR rather than task.

## Deliverable

Implementation + docs + tests.

Final report must include the standard evidence bundle from [`docs/process/evidence-bundle.md`](../process/evidence-bundle.md):

- preflight
- files changed
- implementation notes
- test delta
- proof:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`
- drifts
- pushback-or-omit
- commit hash if committed

## Success criteria

- `extract_one(...)` is public, async, and materializes one pydantic object.
- no duplicate execution path: implementation calls `extract(...)`.
- failed / partial / zero / many object outcomes raise `ExtractionFailed(result=...)`.
- setup/schema errors propagate as their original exception types.
- docs and tier-1 exports reflect the new public symbol and exception.
- full proof gate passes.

## Downstream consequences

- `extract_many(...)` remains deferred. It should compile through `extract(...)` and `result.to_pydantic(schema)` if/when it lands.
- This helper gives examples and notebooks a clean one-object path without making `ExtractionResult` less canonical.
