# Task: implement seam F layer 3 phase 1 canonical post-resolution instance validation

*This is seam F layer 3. Layers 1 and 2 are already real; `run_extraction(...)` now executes end to end on the M8 narrow path through `G.resolver`. The missing canonical seam is the single post-resolution instance-layer validation phase required by ADR-0003. Keep this thread narrow: one deterministic layer-3 call per `InstanceResult`, no retry orchestration, no replay writer, no reporter threading, no mutation of resolved proposals, no resolver feedback loop.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; seam F / execution notes; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam F in full** (all three layers, especially the structural note and invariants for layer 3), **§7 seam G.resolver** (to keep grouping concerns out of layer 3), **§9 canonical objects** for `InstanceResult`, `ResolvedFieldProposal`, `NegativeOutcome`, `ValidationFailure`, **§10 three-tier public surface**, **§11 execution model** (where layer 3 lives in the strategy/executor flow), **§13 public api surface**, **§15 anti-patterns** (`Dual Normalization`, `Silent None`, `Policy Trapped In Consumer`, `Lifecycle-Object Conflation`), **§16 project layout**, **§17 proof table entries for layer 3**
- [`docs/adr/0003-single-canonical-layer3-no-resolver-validators.md`](../adr/0003-single-canonical-layer3-no-resolver-validators.md) — **load-bearing**. canonical layer 3 runs exactly once per `InstanceResult` that reaches it, post-resolution. resolver never invokes validators; layer 3 failures do not trigger reassignment
- [`docs/tasks/seam-f-phase-1-candidate-and-field-validation.md`](seam-f-phase-1-candidate-and-field-validation.md) — reuse the seam-F phase-1 naming and protocol discipline; extend the same seam rather than inventing parallel validation machinery
- [`docs/tasks/seam-g-resolver-phase-1-deterministic-instance-resolution.md`](seam-g-resolver-phase-1-deterministic-instance-resolution.md) — consume the landed `InstanceResult` shape and preserve resolver ownership of final `InstanceKey` assignment
- [`docs/tasks/m8-phase-1-serial-independent-vertical-slice.md`](m8-phase-1-serial-independent-vertical-slice.md) — layer 3 inserts into the actual M8 call path after resolver and before `ExtractionResult` assembly
- [`src/extractx/proposals/validation.py`](../../src/extractx/proposals/validation.py) — current `LayeredProposalValidator` implementation for layers 1 and 2
- [`src/extractx/execution/strategies/independent.py`](../../src/extractx/execution/strategies/independent.py) and [`src/extractx/execution/executor/serial.py`](../../src/extractx/execution/executor/serial.py) — current M8 call site and failure-routing shape

## Goal

implement the canonical post-resolution instance-layer validation phase so that each `InstanceResult` produced by `G.resolver` is validated exactly once, after final `InstanceKey` assignment, with:

- pydantic `model_validator(mode="after")` on a materialized partial-instance view for pydantic-backed specs
- failures routed as typed `ValidationFailure(layer="instance", ...)` and escalated under the existing no-retry execution policy to `NegativeOutcome(category="validation", code="instance_failure", ...)`

without re-running layers 1 or 2, without re-entering the resolver, and without mutating or dropping `ResolvedFieldProposal`s.

**"done" in one sentence:** seam F layer 3 validates each resolved `InstanceResult` exactly once after `G.resolver`, using pydantic `model_validator(mode="after")` for pydantic-backed specs and no-op pass-through for manual specs, and returns a canonically updated `InstanceResult` or typed instance-layer failure without changing grouping truth or re-running earlier seams.

## Scope

numbered implementation areas. do each in order.

### 1. extend the seam-F protocol explicitly

extend the existing `ProposalValidator` protocol in `src/extractx/core/contracts.py`.

requirements:

- keep seam F as one internal protocol; do **not** introduce a new sibling `InstanceLayerValidator` protocol in this thread
- add a second explicit callable surface:
  - `validate_instance(instance_result: InstanceResult, spec: ExtractionSpec, schema_cls: type[BaseModel] | None = None) -> InstanceResult | ValidationFailure`
- keep the existing `validate(...)` method unchanged
- `validate_instance(...)` is:
  - sync
  - pure
  - deterministic
  - post-resolution only
- `schema_cls` is caller-held runtime context, same principle as layer 2
- the method consumes the whole `InstanceResult` because layer 3 is per-instance cross-field validation, not per-field replay

implementation-shape constraints:

- same protocol, two methods
- no async protocol
- no retry / reporter / runtime parameters here
- no `UsageEvent` emission
- layer 3 returns either:
  - validated pass-through `InstanceResult`, or
  - `ValidationFailure(layer="instance", ...)`
- typed `NegativeOutcome` escalation remains execution-owned, mirroring the M8 layer-2 routing pattern

### 2. implement canonical layer 3 in `LayeredProposalValidator`

extend `src/extractx/proposals/validation.py`.

requirements:

- add `LayeredProposalValidator.validate_instance(...)`
- canonical ordering is fixed:
  1. pydantic `model_validator(mode="after")` on the materialized partial-instance view, when `schema_cls is not None`
- if the pydantic path fails:
  - emit `ValidationFailure(layer="instance", field_id="<instance>", instance_key=<instance_result.instance_key>, reason=<str>, producer_version=None)`
- if the pydantic path succeeds:
  - return the original `InstanceResult` unchanged
- if `schema_cls is None`:
  - phase-1 manual-spec layer 3 is a no-op pass-through
- success returns the original `InstanceResult` unchanged

implementation-shape constraints:

- do **not** mutate `ResolvedFieldProposal`s
- do **not** drop `field_proposals` on failure
- do **not** append negatives inside the validator; execution owns escalation/attachment
- do **not** normalize again; layer 2 remains the single normalization site
- do **not** re-run layers 1 or 2
- do **not** call the resolver or planner
- do **not** introduce grouping logic; `ambiguous_grouping` remains resolver-owned only
- do **not** invent an `InstanceValidator` attachment surface in this thread. extractx `InstanceValidator`s remain a declared protocol but are out of phase-1 scope because the landed spec surface has no honest attachment point

### 3. materialize the partial-instance view honestly

layer 3 needs a materialized instance-shaped object for pydantic-backed specs.

requirements:

- do **not** use the public `.to_pydantic()` stubs on `InstanceResult` / `ExtractionResult`; those remain stubbed
- implement a narrow internal materialization helper for layer 3 only
- phase-1 partial-instance materialization shape is:
  - start from the `ResolvedFieldProposal`s in one `InstanceResult`
  - build a mapping `{field_id: normalized_value}` from the instance's current `field_proposals`
  - materialize that mapping into `schema_cls` with `schema_cls.model_construct(**mapping)` so missing required fields do not fail construction
  - explicitly invoke registered `model_validator(mode="after")` decorators on the constructed instance
- pydantic `model_validator(mode="after")` must run on that materialized partial view
- `model_validator(mode="before")` and `model_validator(mode="wrap")` are out of scope for phase 1 layer 3 and must not fire. if a spec under test declares either, the worker notes the limitation in the final report

implementation-shape constraints:

- keep this helper internal to seam F / execution, not public schema-surface API
- do not widen `schema/to_pydantic.py` into a full materialization thread here
- do not invent a second source of truth for field names or values; use `ResolvedFieldProposal.field_id` and `ResolvedFieldProposal.normalized_value`
- partial-instance materialization must preserve the actual values the resolver emitted; no coercion or inference beyond what the pydantic model path itself does at layer 3
- if explicit `model_validator(mode="after")` invocation requires reaching through pydantic's decorator registry, keep that reach narrow, internal, and documented in the final report rather than inventing a broader abstraction
- catch only `pydantic.ValidationError` and `ValueError` raised inside the `model_validator(mode="after")` invocation; translate either to `ValidationFailure(layer="instance", ...)`. let other exception types (`AttributeError`, `TypeError`, and any other unexpected exception) propagate as implementation defects — do not mask them as typed validator failures

### 4. pin `schema_cls` handoff and instance-validator sourcing

requirements:

- reuse the M8 caller-held `schema_cls` pattern
- executor resolves `schema_cls` once per run and threads it into:
  - seam F layer 2 calls
  - seam F layer 3 calls
- do **not** invent a second schema lookup path from `ExtractionSpec.source_schema_ref`
- phase-1 layer 3 invokes **only** pydantic `model_validator(mode="after")` for pydantic-backed specs
- extractx `InstanceValidator`s remain a declared protocol but have no landed spec-surface attachment point in this thread; attachment is deferred to a separate coordinator-owned thread

implementation-shape constraints:

- no filesystem/import-string schema resolution
- no hidden registry beyond the already-landed `_SCHEMA_CLS_BY_SPEC_VERSION` pattern
- if a pydantic-backed spec reaches layer 3 without a live registered `schema_cls`, fail loudly at execution setup / call site with `InfrastructureError`, consistent with M8

### 5. route layer-3 failures through the existing no-retry execution shape

this thread does **not** widen `ExecutorPolicy`.

requirements:

- `ValidationFailure(layer="instance", ...)` is the typed validator output
- under the only phase-1 policy value (`ExecutorPolicy.on_validation_failure == "fail"`), execution escalates it immediately to:
  - `NegativeOutcome(category="validation", code="instance_failure", field_id=None, instance_key=<same>, reason=<failure.reason>, candidate_count=None)`
- escalation happens at the execution layer, not inside `LayeredProposalValidator`
- the escalated negative is appended immutably to `InstanceResult.negative_outcomes`
- if the instance was previously `complete`, outcome flips to `partial`
- if already `partial`, it stays `partial`
- `field_proposals` remain intact

implementation-shape constraints:

- no retry loop
- no reassignment / resolver re-entry
- no invalidation of already-resolved grouping truth
- no document-scope detached instance-layer negatives

### 6. wire layer 3 into the M8 supported path

insert layer 3 into the actual runtime path:

`... -> G.resolver -> ProposalValidator.validate_instance(...) per InstanceResult -> ExtractionResult`

requirements:

- the invocation lives in the M8 execution path, after resolver output and before final `ExtractionResult` assembly
- pin the executor as the sole layer-3 call site in phase 1
- same call site must be the future reuse point for iterative strategy
- each `InstanceResult` reaching layer 3 is processed exactly once
- executor instantiates `LayeredProposalValidator()` directly in phase 1; do not widen `Runtime` or introduce protocol injection for this thread

implementation-shape constraints:

- do not duplicate the layer-3 invocation in both strategy and executor
- do not let the call site become strategy-specific hidden policy
- no reporter threading in this thread

### 7. keep `InstanceResult` / `ExtractionResult` authority boundaries clean

requirements:

- `InstanceResult.field_proposals` remain canonical resolved proposals
- layer-3 failure does not demote or remove proposals
- `InstanceResult.negative_outcomes` carries the escalated instance-layer negative
- `ExtractionResult.instances` remains canonical
- `ExtractionResult.outcome` rollup remains:
  - `complete` if all instances complete
  - `partial` if any instance partial
  - `failed` only if `instances == ()`

implementation-shape constraints:

- no new result object types
- no “validated instance result” shadow type
- no mutation of existing pydantic frozen objects; rebuild immutably

### 8. package wiring

requirements:

- keep `LayeredProposalValidator` as the concrete class
- update `src/extractx/proposals/__init__.py` exports only as needed
- update `src/extractx/core/contracts.py` protocol surface
- update M8 execution wiring (`IndependentStrategy` / `SerialExecutor`) as needed
- do not widen top-level tier-1 exports in this thread

## Explicit drifts to acknowledge in the implementation

surface these in code comments or the final report; do not silently invent around them:

1. **protocol drift**
   - phase-1 landed `ProposalValidator` only exposes `validate(...)`
   - layer 3 extends the same internal protocol with `validate_instance(...)`

2. **materialization drift**
   - public `.to_pydantic()` remains stubbed
   - layer 3 needs a narrow internal partial-instance materialization helper without widening the full schema-surface thread
   - phase-1 uses `model_construct(**mapping)` plus explicit `model_validator(mode="after")` invocation; `mode="before"` and `mode="wrap"` validators remain out of scope
   - phase-1 catches `pydantic.ValidationError` and `ValueError` from inside the `mode="after"` invocation; other exception types propagate as implementation defects

3. **instance-validator attachment drift**
   - architecture names extractx `InstanceValidator`s conceptually, but the landed spec surface provides no honest attachment point
   - phase-1 therefore defers extractx `InstanceValidator` attachment entirely; layer 3 is pydantic-model-validator-only for pydantic-backed specs and no-op for manual specs

4. **execution ownership drift**
   - M8 currently assembles final `ExtractionResult` after resolver with no layer 3
   - this thread inserts the canonical layer-3 call once, in the executor, before final `ExtractionResult` assembly

## Guardrails

- **write scope:** `src/extractx/proposals/validation.py`, `src/extractx/proposals/__init__.py`, `src/extractx/core/contracts.py`, the smallest necessary edits in the M8 execution path (`src/extractx/execution/strategies/independent.py`, `src/extractx/execution/executor/serial.py`), and focused tests
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly
- **no dependency changes**
- **no resolver changes** other than the call-site wiring after its output
- **no replay/storage implementation**
- **no reporter step-event threading**
- **no interview capture**
- **no pydantic-ai work**
- **no retry-policy widening**
- **no mutation of `ResolvedFieldProposal`s**
- **no group reassignment / grouping logic**
- **no commits or pushes** unless separately asked

## Focused proof

add focused tests under `tests/contracts/`, `tests/proposals/`, and `tests/integration/`.

minimum proof targets:

- **surface:**
  - `ProposalValidator.validate_instance(instance_result, spec, schema_cls=None) -> InstanceResult | ValidationFailure` exists on the protocol surface
  - `LayeredProposalValidator` satisfies the widened protocol structurally
- **single canonical invocation:**
  - in the supported M8 path, each `InstanceResult` reaches layer 3 exactly once
  - resolver does not invoke `model_validator` or `InstanceValidator`
- **pydantic precedence:**
  - a raising pydantic `model_validator(mode="after")` yields `ValidationFailure(layer="instance", ...)`
  - a `mode="after"` validator that raises `ValueError` yields `ValidationFailure(layer="instance", ...)`
  - `ValidationFailure.field_id == "<instance>"`
  - `ValidationFailure.producer_version is None`
  - `model_validator(mode="before")` does not fire in phase 1 layer 3
  - `model_validator(mode="wrap")` does not fire in phase 1 layer 3
  - a `mode="after"` validator that raises `AttributeError` or `TypeError` propagates as an implementation defect (not caught, not translated)
- **manual path:**
  - manual specs (`schema_cls=None`) are byte-identical no-op pass-through at layer 3
  - pydantic-backed specs with no registered `model_validator`s are byte-identical pass-through at layer 3
- **failure escalation:**
  - `ValidationFailure(layer="instance", ...)` escalates under no-retry policy to `NegativeOutcome(category="validation", code="instance_failure", ...)`
  - the negative is appended to `InstanceResult.negative_outcomes`
  - the escalated `NegativeOutcome.field_id is None`
  - `InstanceResult.outcome` flips `complete -> partial`
  - `field_proposals` remain unchanged
- **no reassignment:**
  - layer-3 failure never changes `instance_key`
  - layer-3 failure never removes or re-buckets proposals
- **determinism:**
  - same `(instance_result, spec, schema_cls)` yields byte-identical layer-3 output
  - same full M8 run inputs yield byte-identical post-layer-3 `ExtractionResult`
- **success-path identity:**
  - on layer-3 success, executor returns the original `InstanceResult` reference unchanged (no defensive rebuild)
- **stub honesty:**
  - public `.to_pydantic()` stubs remain `NotImplementedError`
  - layer 3 does not route through those public stubs
  - importing `extractx.schema.to_pydantic` does not expose a new public materialization entry point in this thread

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/proposals/validation.py`
- `src/extractx/proposals/__init__.py`
- `src/extractx/core/contracts.py`
- minimal M8 execution-path wiring

include in your final report:

- exact files changed
- whether layer 3 extended `ProposalValidator` or required a different shape (this brief requires extension of `ProposalValidator`; report if any contradiction forced pushback)
- the exact instance-layer `ValidationFailure -> NegativeOutcome` mapping
- the exact internal materialization helper shape used for pydantic `model_validator`
- confirm that phase-1 layer 3 did **not** source extractx `InstanceValidator`s because no honest attachment surface exists yet
- any follow-on that should become a coordinator-owned thread instead of widening layer 3

## Success criteria

- seam F layer 3 is real
- canonical layer 3 runs exactly once per resolved `InstanceResult`
- pydantic `model_validator(mode="after")` runs for pydantic-backed specs
- manual specs and pydantic specs without registered model validators pass through unchanged
- instance-layer failure is typed and escalates canonically under the existing no-retry execution policy
- no grouping truth changes occur at layer 3
- no proposal mutation/removal occurs at layer 3
- public `.to_pydantic()` stays stubbed; layer 3 uses a narrow internal helper only
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`

## Downstream consequences

- once this lands, the canonical extraction lifecycle is complete on the supported path
- the next clean critical-path thread is **M9 replay/storage** against ADR-0007
- do not fold replay, manifest writing, or storage-shape implementation into this thread
