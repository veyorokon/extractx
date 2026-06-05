# Task: implement seam B pydantic spec construction

*First post-core seam implementation. This task makes the pydantic-native schema surface real: `extract_field`, typed extractx field metadata, `ExtractionSpec.from_pydantic(...)`, cardinality inference, and spec-load validation. It should exercise the T2 core layer directly without drifting into later selection, validation, or execution behavior.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; seam B summary; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam B, §9 canonical objects, §10 three-tier public surface, §12 schema surface (pydantic-native), §13 public api surface, §16 project layout, and §17 proof table entries for seam B/schema**
- [`docs/thread-orchestration.md`](../thread-orchestration.md) — bounded worker thread discipline
- [`docs/adr/0005-candidate-overflow-policy.md`](../adr/0005-candidate-overflow-policy.md) — `PromptPolicy`, `SorterBinding`, and the spec-load rule for `truncate_sorted` without a sorter
- [`docs/tasks/core-contracts-and-objects.md`](core-contracts-and-objects.md) — prior thread; use the landed core layer instead of reinventing core shapes locally

## Goal

implement seam B and the pydantic-native schema declaration surface so a user can define a pydantic schema with `extract_field(...)` metadata and obtain a deterministic `ExtractionSpec` via `ExtractionSpec.from_pydantic(SchemaCls)`.

**"done" in one sentence:** `extract_field(...)` and `ExtractionSpec.from_pydantic(...)` work end-to-end for valid pydantic schemas, cardinality inference and dependency validation follow the architecture exactly, and invalid schema patterns fail at spec load with typed `SpecError`s.

## Scope

numbered implementation areas. do each in order.

### 1. typed extractx metadata container for pydantic fields

implement the typed metadata surface in `src/extractx/schema/metadata.py` and wire `extract_field(...)` to use it.

requirements:

- `extract_field(...)` must remain a thin wrapper over `pydantic.Field`
- extractx metadata must live in a **typed extractx container**, not in ad hoc unstructured `json_schema_extra`
- support the documented arguments from `docs/architecture.md` §12:
  - `description`
  - `cardinality`
  - `priority`
  - `depends_on`
  - `strategy_binding`
  - `validation_binding`
  - `grouping_binding`
  - `prompt_binding`
  - `sorter_binding`
- if additional pydantic `Field(...)` kwargs are passed, they should still flow through to pydantic normally

implementation-shape constraints:

- do not invent extra schema metadata knobs beyond what the docs define
- keep the metadata container minimal and typed
- no schema loading or inference logic in `extract_field.py` beyond wrapping and attaching metadata

### 2. cardinality inference from pydantic annotations

implement the schema inference rules in `src/extractx/schema/inference.py`.

requirements:

- match the architecture table exactly:
  - bare `X` -> `Cardinality.one`
  - `X | None` / `Optional[X]` -> `Cardinality.optional`
  - `list[X]` where `X` is a pydantic model -> `Cardinality.per_instance`
  - `list[X]` where `X` is scalar/non-model -> `Cardinality.many`
  - explicit `cardinality=` in `extract_field(...)` overrides inference
- implement this as pure type-analysis helpers
- keep the inference deterministic

implementation-shape constraints:

- do not infer behavior from field names or descriptions
- do not pull in runtime/execution concerns
- if pydantic annotation edge cases are not covered by the architecture, choose the narrowest honest behavior and call it out in the final report

### 3. `ExtractionSpec.from_pydantic(...)`

implement spec construction in `src/extractx/schema/from_pydantic.py`, with the minimal supporting hook in core needed to make `ExtractionSpec.from_pydantic(...)` actually exist.

requirements:

- `ExtractionSpec.from_pydantic(SchemaCls)` must:
  - accept a pydantic `BaseModel` subclass
  - read `extract_field(...)` metadata from each field
  - infer `FieldSpec.cardinality` when not explicitly provided
  - extract `FieldSpec.value_kind` from `Annotated[T, ValueKind.X]` on the field annotation
  - populate `FieldSpec.python_type`
  - preserve declared `priority` and `depends_on`
  - build a deterministic `ExtractionSpec`
  - compute a deterministic `spec.version`
  - set `source_schema_ref` appropriately and consistently
- if a field annotation carries no `ValueKind` marker, raise `SpecError` at spec load rather than inferring from the python type
- if a field annotation carries multiple `ValueKind` markers, raise `SpecError` rather than picking one silently
- support nested model structure enough to honor the architecture’s `list[SubModel] -> per_instance` semantics
- manual `ExtractionSpec(...)` construction must remain available; do not break it

write-scope note:

- a **small** edit to `src/extractx/core/objects.py` is allowed if needed to attach the `from_pydantic` classmethod or equivalent constructor surface promised by the public api
- keep that edit minimal; seam B owns this constructor surface, not a redesign of core objects

implementation-shape constraints:

- `from_pydantic` must be pure
- no runtime/provider/executor access
- no schema-to-prompt logic
- no candidate/selection/validation execution behavior

### 4. spec-load validation and seam-B `SpecError` triggers

implement validation logic in `src/extractx/schema/validators.py` and/or adjacent pure helpers, covering seam B’s load-time failures.

requirements:

- reject cyclic `depends_on` via `SpecError`
- reject pydantic `field_validator` functions that attempt to parse raw text rather than validate normalized values
  - match the architecture’s **pydantic-as-extractor prohibition**
  - use the narrowest honest detection you can support now
  - if the architecture leaves some ambiguous cases, enforce the clearly-detectable bad pattern and document the remaining limit in the final report
- reject invalid `ValueKind`s if encountered during spec construction
- enforce ADR-0005:
  - if `PromptPolicy.candidate_overflow_policy == "truncate_sorted"` and any `FieldSpec.sorter_binding is None`, raise `SpecError`
- enforce the architecture rule:
  - manual `FieldSpec` with `validation_binding=None` and no pydantic class fallback is invalid
  - if that exact check belongs more naturally in core object validation than pydantic loading, keep it minimal and honest

implementation-shape constraints:

- fail loudly at spec-load
- do not silently coerce invalid schema metadata into defaults
- do not push these checks downstream into seam F or execution

### 5. public schema surface and minimal exports

implement and wire the schema package:

- `src/extractx/schema/extract_field.py`
- `src/extractx/schema/from_pydantic.py`
- `src/extractx/schema/inference.py`
- `src/extractx/schema/metadata.py`
- `src/extractx/schema/validators.py`

make the minimal public wiring changes required so the documented end-user path is real:

- `ExtractionSpec.from_pydantic(...)` exists and works
- `extract_field` is importable from top-level `extractx`

write-scope note:

- touching `src/extractx/__init__.py` is allowed only to expose `extract_field` if needed
- do **not** widen the top-level surface beyond what this task honestly implements

### 6. explicit non-goals for this task

leave these out:

- `schema/to_pydantic.py` real materialization behavior
- branded type aliases in `extractx.types`
- seam A source adaptation
- seam D selector logic
- seam F runtime validation behavior beyond spec-load prohibition checks
- execution/runtime wiring
- any replay/interview logic

typed stubs may remain where needed, but do not invent behavior owned by later tasks.

### 7. focused proof

add focused tests primarily under `tests/schema/`, plus `tests/contracts/` or `tests/invariant/` where appropriate.

minimum proof targets to cover:

- `extract_field(...)` attaches typed extractx metadata and still behaves as a pydantic field declaration helper
- `ExtractionSpec.from_pydantic(Cls)` is pure: same class -> same `spec.version`
- `ExtractionSpec.from_pydantic(Cls)` extracts `FieldSpec.value_kind` from `Annotated[..., ValueKind.X]`
- missing or multiply-declared `ValueKind` markers raise `SpecError`
- dependency cycles in `depends_on` raise `SpecError`
- cardinality inference table is applied correctly
- explicit `cardinality=` overrides inference
- `list[SubModel]` becomes `per_instance`
- `list[Scalar]` becomes `many`
- pydantic-as-extractor prohibition raises `SpecError` on the disallowed pattern
- `candidate_overflow_policy="truncate_sorted"` without `sorter_binding` raises `SpecError`
- top-level `from extractx import extract_field` works if you expose it

## Guardrails

- **write scope:** `src/extractx/schema/**`, focused tests, and only the smallest supporting edits in:
  - `src/extractx/core/objects.py` (for `ExtractionSpec.from_pydantic(...)` hook)
  - `src/extractx/__init__.py` (only if needed to export `extract_field`)
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly. if so, stop and report with the standard pushback shape.
- **no behavior from later seams.** do not implement:
  - seam A adapters
  - seam D selector logic
  - seam E cardinality adaptation behavior
  - seam F runtime normalization/validation behavior beyond spec-load checks
  - runtime/executor/replay/interview behavior
- **no new schema base class.** do not introduce `extractx.Schema` or any parallel schema hierarchy
- **no raw dict metadata bags.** the extractx metadata attached by `extract_field(...)` must stay typed
- **no domain leakage** from any consumer domain
- **no commits or pushes** unless separately asked. leave the branch ready for review

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/schema/extract_field.py`
- `src/extractx/schema/from_pydantic.py`
- `src/extractx/schema/inference.py`
- `src/extractx/schema/metadata.py`
- `src/extractx/schema/validators.py`

with only minimal supporting edits elsewhere if required by the public seam-B surface.

include in your final report:

- exact files changed
- any places where pydantic limitations forced a narrower implementation than the ideal architecture wording
- any remaining ambiguity that should become a follow-on coordinator-owned thread rather than more code

## Success criteria

- `extract_field(...)` is real and typed
- `ExtractionSpec.from_pydantic(...)` is real and deterministic
- seam B `SpecError` triggers covered by the current architecture are enforced
- cardinality inference matches the architecture table
- no later seam behavior is smuggled into schema construction
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pyright`
- new tests cover the listed proof targets
- top-level repo state remains coherent with the architecture/doc pact

## Downstream consequences

- unblocks real schema declaration and spec construction for end users
- gives later seam tasks a real `ExtractionSpec` / `FieldSpec` loading path
- directly feeds:
  - seam D/E/F via typed `FieldSpec` and its bindings
  - seam G via dependency order and field priority
  - public api examples in `docs/architecture.md` §12 / §13
- if this task exposes a real contradiction in the current seam-B contract, that becomes a new coordinator-owned thread before more implementation proceeds
