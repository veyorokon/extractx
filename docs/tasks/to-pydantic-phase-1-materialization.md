# Task: implement `to_pydantic` phase 1 — result materialization

*Lane A / product-surface implementation. `extract(...)` now gives users a clean schema-first path, but the returned `ExtractionResult` still cannot materialize into the schema class. This thread lands the narrow, cardinality-aware derived projection: `InstanceResult.to_pydantic(Cls)` and `ExtractionResult.to_pydantic(Cls)`. It does not add `extract_one`, `extract_many`, `Extractor`, `ExtractOptions`, a strategy knob, manual-spec replay, result cache, usage capture, interview capture, or any new execution path.*

## Read first

the exec agent starts cold. read these before editing:

- [`AGENTS.md`](../../AGENTS.md) — seam / contract / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local canonical nouns and forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§9 canonical objects** (`ResolvedFieldProposal`, `InstanceResult`, `ExtractionResult`), **§12 pydantic schema surface** (`to_pydantic` is materialization, not extraction), **§13 public api surface**, **§15 anti-patterns** (`Canonical/Derived Smear`, `Silent None`, `Duplicate Overlapping Path`)
- [`docs/tasks/api-phase-1-extract-function.md`](api-phase-1-extract-function.md) — `extract(...)` landed as the schema-first path; this thread is the next user-facing projection
- [`src/extractx/core/outcomes.py`](../../src/extractx/core/outcomes.py) — current stubs on `InstanceResult` / `ExtractionResult`; canonical result shapes
- [`src/extractx/schema/to_pydantic.py`](../../src/extractx/schema/to_pydantic.py) — empty module reserved for this implementation
- [`src/extractx/schema/inference.py`](../../src/extractx/schema/inference.py) — cardinality inference table; reuse it rather than inventing annotation parsing
- [`src/extractx/proposals/validation.py`](../../src/extractx/proposals/validation.py) — note `_layer3_field_mapping(...)` is layer-3-only and collapses duplicates; do **not** reuse it for public materialization
- existing stub-honesty tests:
  - [`tests/contracts/test_extraction_result_stream.py`](../../tests/contracts/test_extraction_result_stream.py)
  - [`tests/contracts/test_extraction_result_rollup.py`](../../tests/contracts/test_extraction_result_rollup.py)
  - [`tests/proposals/test_layer3_proposal_validator.py`](../../tests/proposals/test_layer3_proposal_validator.py)
  - [`tests/integration/test_replay_round_trip_e2e.py`](../../tests/integration/test_replay_round_trip_e2e.py)

## Goal

implement pydantic materialization as a derived projection over canonical `InstanceResult.instances[*].field_proposals`.

public behavior:

```python
invoice: Invoice = result.instances[0].to_pydantic(Invoice)
invoices: list[Invoice] = result.to_pydantic(Invoice)
```

phase-1 contract:

- `InstanceResult.to_pydantic(Cls)` materializes **one** instance.
- `ExtractionResult.to_pydantic(Cls)` materializes every instance in order and returns `list[Cls]`.
- `ExtractionResult(outcome="failed", instances=())` returns `[]`; it does not raise just because the run failed. the canonical data to project is `instances`, and there are none.
- partial instances are not silently skipped. `ExtractionResult.to_pydantic(Cls)` attempts to materialize every instance. if any instance cannot satisfy `Cls`, the call raises from that instance.
- materialization is cardinality-aware:
  - `Cardinality.ONE`: exactly one proposal becomes the scalar value; zero proposals on a required field fail loudly; zero proposals on a field with a pydantic default are omitted so `model_construct(...)` applies the default; more than one proposal fails loudly before any value is picked.
  - `Cardinality.OPTIONAL`: one proposal becomes the scalar value; zero proposals become `None`; more than one proposal fails loudly.
  - `Cardinality.MANY`: all proposals become a list in proposal order; zero proposals become `[]`.
  - `Cardinality.PER_INSTANCE`: out of scope in phase 1; fail loudly with a pinned message prefix if a requested schema field infers or declares `per_instance`.
- unknown resolved proposal fields fail loudly before calling pydantic. do not let pydantic's default `extra="ignore"` silently drop an extracted value.
- materialization is not a second validation seam. seam F layer 2 already ran pydantic field validators during extraction, and seam F layer 3 already ran supported `model_validator(mode="after")` validators post-resolution. after the cardinality-aware mapping and precondition checks pass, build the object with `Cls.model_construct(**mapping)`. do not call `model_validate(...)` in phase 1.

**"done" in one sentence:** `result.to_pydantic(Invoice)` and `result.instances[0].to_pydantic(Invoice)` produce real pydantic model instances from resolved proposals, preserving cardinality semantics (`one` / `optional` / `many`), failing loudly on unknown fields or impossible cardinality, and leaving extraction / replay / layer-3 validation behavior unchanged.

## Scope

### 1. implement `src/extractx/schema/to_pydantic.py`

required functions:

```python
def instance_to_pydantic(instance: InstanceResult, cls: type[BaseModel]) -> BaseModel: ...
def result_to_pydantic(result: ExtractionResult, cls: type[BaseModel]) -> list[BaseModel]: ...
```

implementation shape:

- validate `cls` is a pydantic `BaseModel` subclass. if not, raise `SpecError` with prefix:
  - `"to_pydantic.invalid_schema: ..."`
- read `cls.model_fields` in declaration order.
- for each model field, rebuild the original annotation via `field_info.rebuild_annotation()` and use `schema.inference.analyze_field_annotation(field_id, annotation)` to infer cardinality.
- if the field has `extract_field(cardinality=...)` metadata overriding inference, honor that override. use the same metadata reader pattern as `from_pydantic(...)`; if there is no public helper, factor a tiny private helper in `to_pydantic.py` rather than widening schema metadata public surface in this thread.
- group `instance.field_proposals` by `field_id` in proposal order. values come from `ResolvedFieldProposal.normalized_value`, not `raw_value`.
- before mapping fields, verify every proposal `field_id` exists in `cls.model_fields`. if not, raise `SpecError` with prefix:
  - `"to_pydantic.unknown_field: ..."`
- build a mapping for pydantic:
  - `one`: length 1 -> scalar; length 0 -> omit; length > 1 -> `SpecError("to_pydantic.cardinality: ...")`
  - `optional`: length 1 -> scalar; length 0 -> `None`; length > 1 -> `SpecError("to_pydantic.cardinality: ...")`
  - `many`: list of all values, including `[]` on length 0
  - `per_instance`: `SpecError("to_pydantic.per_instance_unsupported: ...")`
- before construction, fail on missing required scalar fields with prefix:
  - `"to_pydantic.missing_required: ..."`
- call `cls.model_construct(**mapping)` and return the resulting model instance.

guardrails:

- do **not** use `_layer3_field_mapping(...)`. it is layer-3-only, collapses duplicate fields, and is not cardinality-aware.
- do **not** use `model_validate(...)` for public materialization in phase 1. users are receiving a projection of already-validated canonical proposals; rerunning validators would make `.to_pydantic(...)` a hidden validation seam.
- do **not** mutate `InstanceResult`, `ExtractionResult`, `ResolvedFieldProposal`, or any proposal object.
- do **not** require an `ExtractionSpec` parameter. phase 1 derives cardinality from the schema class so the public method remains `to_pydantic(Cls)`.
- do **not** implement nested `per_instance` materialization in this thread.

### 2. wire the core methods

in [`src/extractx/core/outcomes.py`](../../src/extractx/core/outcomes.py):

- `InstanceResult.to_pydantic(self, cls)` lazily imports and calls `extractx.schema.to_pydantic.instance_to_pydantic(self, cls)`.
- `ExtractionResult.to_pydantic(self, cls)` lazily imports and calls `extractx.schema.to_pydantic.result_to_pydantic(self, cls)`.

implementation-shape constraints:

- keep lazy imports inside the methods to avoid core → schema import-cycle risk.
- do not change `ExtractionResult.usage()` or `.interview()`; they remain `NotImplementedError`.
  - current implementation note: ADR-0015 later replaced `.usage()` with a captured usage-event projection; `.interview()` remains stubbed
- do not change `ExtractionResult.proposals()`, `.negatives()`, or `.stream()`.
- do not add new public exports unless needed for tests inside `extractx.schema`. top-level `extractx.__all__` should not widen in this thread.

### 3. update tests

replace stub-honesty assertions for `.to_pydantic()` with materialization proofs. keep stub-honesty assertions for `.usage()` and `.interview()`.

minimum proof targets:

1. **instance happy path:** one complete instance with scalar fields materializes to an instance of the requested pydantic class with expected values.
2. **result happy path:** `ExtractionResult.to_pydantic(Cls)` returns a `list[Cls]` in instance order.
3. **failed result projection:** an `ExtractionResult` with `instances=()` returns `[]`.
4. **optional missing:** an optional field with no resolved proposal materializes as `None` instead of raising.
5. **many missing:** a `list[...]` field with no resolved proposals materializes as `[]`.
6. **many populated:** multiple proposals for a `many` field materialize as a list preserving proposal order.
7. **one missing:** a required `one` field with no proposal raises `SpecError` with prefix `"to_pydantic.missing_required: "`.
8. **one duplicate / optional duplicate:** multiple proposals for a scalar field raise `SpecError` with prefix `"to_pydantic.cardinality: "`.
9. **unknown proposal field:** a proposal whose `field_id` is not on the requested schema raises `SpecError` with prefix `"to_pydantic.unknown_field: "` even if the pydantic model would ignore extra fields.
10. **per-instance unsupported:** a `list[SubModel]` / `Cardinality.PER_INSTANCE` field raises `SpecError` with prefix `"to_pydantic.per_instance_unsupported: "`.
11. **pydantic validators do not run again:** a pydantic validator or model validator that would fail under `model_validate(...)` does not run during materialization; `.to_pydantic(...)` uses `model_construct(...)` after its own cardinality / field-shape preconditions.
12. **layer 3 independence:** seam-F layer 3 tests still prove layer 3 does not route through public `.to_pydantic(...)`. update the old stub-honesty test to monkeypatch `InstanceResult.to_pydantic` to raise an assertion if called, then run layer 3 successfully.
13. **replay round trip:** replay-reproduced results can call `.to_pydantic(Cls)` successfully on the supported pydantic-backed path; `.usage()` / `.interview()` still raise `NotImplementedError`.
    - current implementation note: ADR-0015 later replaced `.usage()` with a captured usage-event projection
14. **no out-of-scope edits:** zero-line diff for `src/extractx/api.py`, `src/extractx/execution/`, `src/extractx/replay/`, `src/extractx/storage/`, docs, and task briefs.

test placement:

- prefer a new focused file under `tests/schema/test_to_pydantic.py` for the materializer contract.
- update existing tests that currently assert `.to_pydantic()` stubs; do not leave contradictory tests.

### 4. docs / public surface restraint

do not edit docs in this thread. architecture already names `.to_pydantic(Cls)` as the desired materialization surface. any wording cleanup discovered during implementation goes in the final report under `Drift`.

do not add:

- `extract_one(...)`
- `extract_many(...)`
- `Extractor`
- `ExtractOptions`
- a new exception class
- a `Runtime` / `ExecutorPolicy` / strategy parameter
- replay artifact `to_pydantic(...)`
- result-cache behavior

## Guardrails

- **write scope:** `src/extractx/schema/to_pydantic.py`, `src/extractx/core/outcomes.py`, focused tests under `tests/schema/`, plus existing tests that currently assert `.to_pydantic()` is stubbed.
- **no docs edits** in implementation.
- **no top-level `extractx.__all__` widening**.
- **no engine / executor / strategy / replay / storage changes**.
- **no mutation of canonical result objects**.
- **no `model_validate(...)` in public materialization**.
- **no `per_instance` support in phase 1**.
- **no commits or pushes** unless separately asked.

## Pushback discipline

if a hard pin contradicts code reality, stop and report structured pushback:

- current contract:
- observed gap or contradiction:
- consequence if implemented as written:
- proposed cleaner pattern:
- seam / ownership impact:
- whether this is clarification vs architecture change:
- proof target:

examples that require pushback:

- `analyze_field_annotation(...)` cannot be reused without requiring new public surface.
- pydantic `model_fields` cannot recover the cardinality metadata needed for explicit overrides.
- existing result objects do not carry enough information to distinguish `many` from scalar for a schema field.
- `model_construct(...)` cannot preserve pydantic defaults in the required phase-1 cases.

## Deliverable

land the code and tests only. final report must include:

- preflight (`pwd`, branch, status, `git log -1`)
- files changed
- implementation notes
- test delta (`+N tests`, total passing count)
- proof:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`
- drifts, if any
- pushback, if any; omit the section if none

## Success criteria

- `InstanceResult.to_pydantic(Cls)` returns real pydantic objects.
- `ExtractionResult.to_pydantic(Cls)` returns `list[Cls]`.
- cardinality-aware projection is proven for `one`, `optional`, and `many`.
- unknown fields and impossible scalar cardinality fail loudly before pydantic can silently drop or pick values.
- materialization does not re-run pydantic validators.
- layer 3 remains independent from public materialization.
- replay-reproduced supported-path results materialize.
- full proof gate passes.

## Downstream consequences

- unblocks a future `extract_one(...)` / `extract_many(...)` discussion.
- gives benchmark and smoke sidecars a clean schema-object projection without inventing a parallel materializer.
- leaves nested `per_instance` materialization as a separate design thread.
