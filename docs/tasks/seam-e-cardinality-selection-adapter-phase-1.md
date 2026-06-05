# Task: implement seam E phase 1 cardinality-aware selection adaptation

*This is seam E phase 1. Make the `SelectionAdapter` seam real as a pure, deterministic adapter from `Selection + CandidateSet + FieldSpec` into `ProposedField[]` or a typed `NegativeOutcome`. This thread is intentionally mechanical: it proves the cardinality table and lifecycle projection without smuggling in seam F normalization, seam D policy, or runtime/executor behavior.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; seam E summary; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam E, §7 seam F (to understand what seam E is *not*), §9 canonical objects, §10 three-tier public surface, §13 public api surface notes around lifecycle objects, §16 project layout, and §17 proof table entries for seam E**
- [`docs/adr/0003-single-canonical-layer3-no-resolver-validators.md`](../adr/0003-single-canonical-layer3-no-resolver-validators.md) — seam E stops at `ProposedField`; seam F/G own later lifecycle transitions
- [`docs/tasks/core-contracts-and-objects.md`](core-contracts-and-objects.md) — prior thread; use the landed core `ProposedField`, `NegativeOutcome`, `CandidateSet`, and `Selection` objects instead of reinventing them
- [`docs/tasks/seam-c-deterministic-candidate-generation.md`](seam-c-deterministic-candidate-generation.md) — seam E consumes the real `CandidateSet` surface from seam C
- [`docs/tasks/seam-d-algorithmic-selector-phase-1.md`](seam-d-algorithmic-selector-phase-1.md) — seam E consumes the real `Selection` surface from seam D

## Goal

implement seam E so a deterministic `SelectionAdapter` can consume a real `Selection`, `CandidateSet`, and `FieldSpec`, then emit either:

- a tuple of real `ProposedField`s, or
- one typed `NegativeOutcome`

according to the architecture's seam-E cardinality table and lifecycle invariants.

**"done" in one sentence:** a deterministic cardinality-aware `SelectionAdapter` turns `Selection + CandidateSet + FieldSpec` into `ProposedField[]` or a typed `NegativeOutcome`, with exact seam-E table behavior, honest lifecycle projection, and no seam-F normalization or validation logic mixed in.

## Scope

numbered implementation areas. do each in order.

### 1. make the seam-E protocol explicit

implement the `SelectionAdapter` callable surface in `src/extractx/core/contracts.py`.

requirements:

- define the protocol method explicitly:
  - `adapt(selection: Selection, candidate_set: CandidateSet, field_spec: FieldSpec) -> tuple[ProposedField, ...] | NegativeOutcome`
- keep it sync, pure, and deterministic
- keep the seam narrow:
  - consumes only `Selection`, `CandidateSet`, `FieldSpec`
  - emits only `tuple[ProposedField, ...] | NegativeOutcome`
- do not add validation/runtime/retry/reporter concerns here

implementation-shape constraints:

- one method only unless the docs already require another
- no async adapter protocol in this task
- no `UsageEvent` emission in this task

### 2. implement the phase-1 cardinality adapter

implement one deterministic seam-E adapter in `src/extractx/proposals/adapter.py`.

the concrete class name is fixed for this task:

- `CardinalitySelectionAdapter`

requirements:

- implement the seam-E cardinality table exactly
- preserve the lifecycle boundary:
  - seam E produces `ProposedField`
  - seam E does **not** produce `ValidatedField`
  - seam E does **not** normalize
  - seam E does **not** invoke pydantic or extractx validators

for this task, make the table concrete as follows:

- if `selection.outcome != "SELECTED"`:
  - emit one `NegativeOutcome`
  - use:
    - `category="selection"`
    - `code=selection.outcome.lower()`
    - `field_id=field_spec.field_id`
    - `instance_key=candidate_set.instance_hint`
    - `candidate_count=len(candidate_set.candidates)`
    - `reason=selection.reason or selection.outcome.lower()`
- if `selection.outcome == "SELECTED"`:
  - let `k = len(selection.selected_candidate_ids)`
  - let `c = field_spec.cardinality`
  - dispatch exactly as:
    - `Cardinality.ONE`
      - `k = 0` -> `NegativeOutcome(category="adaptation", code="empty_selection", ...)`
      - `k = 1` -> one `ProposedField`
      - `k > 1` -> `NegativeOutcome(category="validation", code="cardinality.one_expected_many_selected", ...)`
    - `Cardinality.OPTIONAL`
      - `k = 0` -> `NegativeOutcome(category="selection", code="abstained", ...)`
      - `k = 1` -> one `ProposedField`
      - `k > 1` -> `NegativeOutcome(category="validation", code="cardinality.optional_expected_many_selected", ...)`
    - `Cardinality.MANY`
      - `k = 0` -> empty tuple `()`
      - `k = 1` -> one `ProposedField` in a tuple
      - `k > 1` -> `k` `ProposedField`s in a tuple
    - `Cardinality.PER_INSTANCE`
      - treat as `Cardinality.ONE` within the provided `candidate_set.instance_hint`

clarifications that belong to this task:

- the architecture's seam-E prose says `NegativeOutcome(category from outcome)` for non-`SELECTED` outcomes; in the actual core type system, `NegativeOutcome.category` is the seam/domain bucket, so seam E phase 1 should make that concrete as `category="selection"` and `code=selection.outcome.lower()`
- the architecture prose says `ProposedField.instance_key`; the canonical landed field is `ProposedField.tentative_instance_key`
- the architecture prose says `ProposedField.normalized_value` at seam E; the canonical landed field is `ProposedField.normalized_hint`. do not invent a `normalized_value` field on `ProposedField`
- for every seam-E `NegativeOutcome`, including cardinality-mismatch and `empty_selection` cases, use `candidate_count=len(candidate_set.candidates)` so the diagnostic answers "how many candidates existed at the seam" rather than "how many ids the selector returned"
- for cardinality-mismatch and `empty_selection` negatives emitted from the `SELECTED` path, set `reason=code`; do not generate prose from candidate content and do not interpolate dynamic text such as the observed `k`

implementation-shape constraints:

- no cardinality recovery beyond the documented table
- no "best effort" coercion from `AMBIGUOUS` to one value
- no dedup, ranking, or collapse across selected ids
- no hidden dependence on `FieldSpec.value_kind`, `description`, or validation bindings

### 3. project selected candidates into `ProposedField` honestly

for every `ProposedField` seam E emits, project directly from the selected `Candidate` and the seam-D `Selection`.

requirements:

- selected candidates are resolved by `selection.selected_candidate_ids`, in that order
- for each selected candidate, construct:
  - `field_id = field_spec.field_id`
  - `tentative_instance_key = candidate_set.instance_hint`
  - `raw_value = candidate.text`
  - `evidence_text = candidate.text`
  - `source_span = candidate.source_span`
  - `evidence_spans = candidate.evidence_spans`
  - `normalized_hint = candidate.normalized_hint`
  - `candidate_id_refs = (candidate.candidate_id,)`
  - `strategy_id = candidate_set.strategy_id`
  - `selector_producer_version = selection.producer_version`
  - `grounded_producer_version = None`
- preserve selected-id order exactly for the returned `ProposedField` tuple
- do not re-slice document text from `source_span`
- do not synthesize `source_span` or `evidence_spans`
- do not inspect or copy `candidate.context` into `evidence_text`

implementation-shape constraints:

- no bundling of multiple selected ids into one `ProposedField`
- no mutation of core lifecycle objects after construction
- no normalization hints derived from raw text at this seam; only carry `candidate.normalized_hint` through unchanged

### 4. enforce the mechanical boundary checks at seam E

implement the narrowest honest structural checks in `src/extractx/proposals/adapter.py` and/or adjacent local helpers.

requirements:

- fail loudly if `candidate_set.field_id != field_spec.field_id`
- fail loudly if a selected id cannot be resolved to a `Candidate` in `candidate_set`
- fail loudly if `selection.selected_candidate_ids` contains the same id more than once
  - this is a seam-D contract violation, not a data-driven `NegativeOutcome`
- keep these as implementation-defect failures, not typed negatives

implementation-shape constraints:

- do not re-implement seam-D's full selector boundary here
- do not silently deduplicate repeated selected ids
- do not widen the public exception surface; a local `ValueError` subtype in the proposals package is acceptable

### 5. package wiring

implement the minimal proposals package surface so seam E phase 1 is importable and testable.

requirements:

- wire:
  - `src/extractx/proposals/__init__.py`
  - `src/extractx/proposals/adapter.py`
- export the concrete adapter and any local seam-E helpers that are genuinely reusable inside the proposals subsystem
- leave:
  - `src/extractx/proposals/validation.py`
  - `src/extractx/proposals/provenance.py`
  as stubs unless a tiny edit is needed for package coherence

write-scope note:

- the only supporting edits outside `src/extractx/proposals/**` should be the smallest ones required in:
  - `src/extractx/core/contracts.py`
  - `src/extractx/core/__init__.py`
- do not widen top-level `extractx/__init__.py` in this task

### 6. explicit non-goals for this task

leave these out:

- seam F layer 1 / 2 / 3 validation
- pydantic coercion or `field_validator` invocation
- extractx `FieldValidator` or `InstanceValidator` invocation
- runtime/executor/retry/reporter behavior
- llm/prompt/interview behavior
- `UsageEvent` emission
- `C.alt` grounded proposal generation
- instance resolution / `InstanceResolver`
- materialization / `.to_pydantic(...)`
- replay/interview/provenance writing behavior beyond carrying ids/version strings already present on `Selection` / `CandidateSet`

typed stubs may remain where needed, but do not invent behavior owned by later or separate threads.

## Guardrails

- **write scope:** `src/extractx/proposals/**`, focused tests, and only the smallest supporting edits in:
  - `src/extractx/core/contracts.py`
  - `src/extractx/core/__init__.py`
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly. if so, stop and report with the standard pushback shape.
- **no dependency changes** in this task
- **no seam-F behavior.** normalization happens exactly once, later, at seam F layer 2
- **no silent coercion.** cardinality mismatches become typed `NegativeOutcome`s; structural seam violations fail loudly
- **no widening the end-user public surface** in this task
- **no commits or pushes** unless separately asked. leave the branch ready for review.

## Focused proof

add focused tests primarily under `tests/contracts/` and `tests/proposals/`.

minimum proof targets to cover:

- `SelectionAdapter.adapt(...) -> tuple[ProposedField, ...] | NegativeOutcome` exists on the protocol surface
- same `(selection, candidate_set, field_spec)` yields byte-identical output across repeated calls
- non-`SELECTED` outcomes map to one `NegativeOutcome` with:
  - `category="selection"`
  - `code=selection.outcome.lower()`
- `Cardinality.ONE` table behavior:
  - `SELECTED + k=0` -> `adaptation.empty_selection`
  - `SELECTED + k=1` -> one `ProposedField`
  - `SELECTED + k>1` -> `validation.cardinality.one_expected_many_selected`
- `Cardinality.OPTIONAL` table behavior:
  - `SELECTED + k=0` -> `selection.abstained`
  - `SELECTED + k=1` -> one `ProposedField`
  - `SELECTED + k>1` -> `validation.cardinality.optional_expected_many_selected`
- `Cardinality.MANY` table behavior:
  - `SELECTED + k=0` -> empty tuple
  - `SELECTED + k=1` -> one `ProposedField`
  - `SELECTED + k>1` -> `k` `ProposedField`s in selected-id order
- `Cardinality.PER_INSTANCE` is treated as `one`, with `tentative_instance_key = candidate_set.instance_hint`
- every emitted `ProposedField` copies the documented fields directly from the selected `Candidate`, `CandidateSet`, and `Selection`
- selected ids that are missing, duplicated, or tied to the wrong field fail loudly as seam violations rather than returning a typed `NegativeOutcome`
- no normalization, validation, or seam-F behavior is smuggled into seam E

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/proposals/adapter.py`
- `src/extractx/proposals/__init__.py`

with only minimal supporting edits elsewhere if required by the seam-E surface.

include in your final report:

- exact files changed
- the concrete adapter class name
- how non-`SELECTED` outcomes were mapped into `NegativeOutcome`
- any remaining ambiguity that should become a coordinator-owned follow-on thread rather than more code

## Success criteria

- `SelectionAdapter` has an explicit callable surface
- seam E is real for one deterministic cardinality-aware adapter
- seam E table behavior is implemented exactly and proven by focused tests
- emitted `ProposedField`s preserve the documented lifecycle boundary and copy candidate grounding honestly
- structural seam violations fail loudly instead of silently coercing or inventing negatives
- no seam-F/runtime/llm behavior is smuggled into seam E
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pyright`
- top-level repo state remains coherent with the architecture/doc pact

## Downstream consequences

- gives seam F a real `ProposedField` surface to validate
- proves the first honest `DocumentView -> CandidateSet -> Selection -> ProposedField` path
- leaves normalization, pydantic/extractx validators, and layer-3 instance validation to their owning seam instead of mixing them into adaptation
- if this task exposes a real contradiction in the current seam-E contract, that becomes a new coordinator-owned thread before more implementation proceeds
