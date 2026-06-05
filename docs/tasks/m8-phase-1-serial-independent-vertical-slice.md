# Task: implement M8 phase 1 boring vertical slice (`run_extraction` + `SerialExecutor` + `IndependentStrategy`)

*This is the first operational execution slice. The seam implementations A/C/D/E/F.layer1/F.layer2/G.resolver are already real; the missing piece is the executor-owned path that actually wires them together and returns a real `ExtractionResult`. Keep this slice boring: one executor, one strategy, one supported document shape, one supported extraction path. Do not pull in iterative planning, layer 3, replay, interview capture, async execution, or pydantic-ai.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; execution notes; public-surface discipline
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§3 product boundary**, **§7 seams I/J/K in full**, **§7 seams A/C/D/E/F/G.resolver** (to understand the already-landed seam contracts you are wiring), **§9 canonical objects** for `ExtractionResult`, `InstanceResult`, `NegativeOutcome`, `ValidationFailure`, `ExecutionTrace`, **§10 three-tier public surface**, **§11 execution model and strategy pseudocode**, **§13 public api surface**, **§15 anti-patterns** (`Dual Normalization`, `Silent None`, `Duplicate Overlapping Path`), **§16 project layout**, **§17 proof table execution entries**
- [`docs/adr/0001-pass-through-operational-metadata.md`](../adr/0001-pass-through-operational-metadata.md) — `Budget` receives raw usage passthrough; no pricing logic in core
- [`docs/adr/0003-single-canonical-layer3-no-resolver-validators.md`](../adr/0003-single-canonical-layer3-no-resolver-validators.md) — layer 3 stays out of this slice; resolver does not invoke validators
- [`docs/tasks/seam-a-linearizable-document-adapters.md`](seam-a-linearizable-document-adapters.md) — use the landed `TextAdapter`; do not reinvent seam A
- [`docs/tasks/seam-c-deterministic-candidate-generation.md`](seam-c-deterministic-candidate-generation.md) — use the landed regex strategy as the only extraction producer in this slice
- [`docs/tasks/seam-d-algorithmic-selector-phase-1.md`](seam-d-algorithmic-selector-phase-1.md) — use the landed `SingletonSelector`; no llm selector path in this slice
- [`docs/tasks/seam-e-cardinality-selection-adapter-phase-1.md`](seam-e-cardinality-selection-adapter-phase-1.md) — seam E negative behavior is already canonical
- [`docs/tasks/seam-f-phase-1-candidate-and-field-validation.md`](seam-f-phase-1-candidate-and-field-validation.md) — seam F layer 2 takes caller-held `schema_cls`; execution owns the handoff
- [`docs/tasks/seam-g-resolver-phase-1-deterministic-instance-resolution.md`](seam-g-resolver-phase-1-deterministic-instance-resolution.md) — resolver is already real; do not widen it casually

## Goal

make `run_extraction(document, spec, runtime, policy)` honestly runnable for one narrow in-process path:

- `SerialExecutor`
- `IndependentStrategy`
- `TextAdapter`
- explicit regex candidate generation
- algorithmic singleton selection
- landed seam E adapter
- landed seam F layers 1+2
- landed deterministic resolver

return a real `ExtractionResult` for that path, with typed partial failures, deterministic trace stub, and no fake replay / interview / materialization behavior.

**"done" in one sentence:** `run_extraction(...)` can execute a text document against an explicitly regex-bound spec under `ExecutorPolicy(strategy="independent")` and return a real `ExtractionResult` assembled from the landed seams, while all unsupported execution paths fail loudly before the run starts.

## Scope

numbered implementation areas. do each in order.

### 1. make the execution surfaces explicit

land the minimum concrete execution-owned surfaces in:

- `src/extractx/api.py`
- `src/extractx/execution/runtime.py`
- `src/extractx/execution/policy.py`
- `src/extractx/execution/executor/protocol.py`
- `src/extractx/execution/executor/serial.py`
- `src/extractx/execution/strategies/independent.py`

requirements:

- `run_extraction(...)` remains `async`
- define a real `Executor` protocol with one phase-1 method:
  - `execute(document, spec, runtime, policy) -> ExtractionResult`
- define a real `SerialExecutor` implementing that protocol
- define a real internal `IndependentStrategy`
- define a real `Runtime` container with enough surface to bind:
  - `llm`
  - `nlp`
  - `fetch`
  - `budget`
  - `reporter`
- define a real `ExecutorPolicy` container with the minimum phase-1 surface:
  - `strategy: Literal["independent", "iterative"]`
  - `capture_interview_transcripts: bool = False`
  - `on_validation_failure: Literal["fail"] = "fail"`

implementation-shape constraints:

- this slice lands **one** executor (`SerialExecutor`) and **one** strategy (`IndependentStrategy`) only
- `AsyncExecutor` remains a typed stub
- `IterativeStrategy` remains a typed stub
- `Runtime.from_env()` may land, but for this slice it must succeed without provider keys when the run uses only the algorithmic path; missing `llm` / `nlp` / `fetch` is **not** a `CapabilityError` for this slice because no step consumes them
- `Budget` and `Reporter` must be bound on `Runtime`, but the algorithmic path in this slice does not have to invoke them
- do not introduce retry objects, async task graphs, or remote executors

### 2. pin the supported runnable surface narrowly

this vertical slice is intentionally narrower than the eventual product.

phase-1 supported inputs and execution paths:

- `document`:
  - `str` → encoded as UTF-8 and adapted with `TextAdapter`
  - `bytes` → adapted with `TextAdapter`
- `policy.strategy`:
  - only `"independent"`
- field extraction path:
  - only `FieldSpec.strategy_binding.kind == "candidate"`
  - only explicit `StrategyBinding.cls` naming `RegexCandidateStrategy`

phase-1 unsupported paths must fail loudly **before the run begins**:

- `policy.strategy == "iterative"` → `InfrastructureError`
- `FieldSpec.strategy_binding is None` → `InfrastructureError`
- `FieldSpec.strategy_binding.kind == "grounded_proposal"` → `InfrastructureError`
- any candidate strategy class other than `RegexCandidateStrategy` → `InfrastructureError`
- non-`str` / non-`bytes` document inputs → `InfrastructureError`

implementation-shape constraints:

- do **not** invent default strategy selection for `strategy_binding=None`
- do **not** wire `HtmlAdapter`, `MarkdownAdapter`, PDF, image, or paginated-visual inputs in this slice
- do **not** treat unsupported execution shapes as typed `NegativeOutcome`s; these are setup-time unsupported-surface failures, so they fail as `InfrastructureError` before the run starts

### 3. pin the schema-class handoff into seam F

seam F layer 2 already takes `schema_cls: type[BaseModel] | None = None`. execution owns the handoff.

requirements:

- do **not** resolve a live schema class from `ExtractionSpec.source_schema_ref`
- do **not** widen `run_extraction(...)` with a new public `schema_cls` parameter
- implement the smallest honest in-process handoff:
  - when `ExtractionSpec.from_pydantic(schema_cls)` builds a spec, register the live `schema_cls` in a narrow internal registry keyed by `spec.version`
  - executor resolves `schema_cls` once per run from that registry using `spec.version`
  - executor passes the resolved `schema_cls` (or `None`) to every seam-F `validate(...)` call
- manual specs stay on the manual seam-F path (`schema_cls=None`)

implementation-shape constraints:

- the registry is internal execution/schema plumbing, not a second canonical truth object
- no filesystem persistence, no import-by-string, no reflection from `SchemaRef.ref`
- if a spec claims to be pydantic-backed but no live class is registered in-process, fail loudly at executor setup with `InfrastructureError` rather than inventing a second resolution path

### 4. implement the independent execution order

wire the landed seams in `IndependentStrategy` exactly once, in declaration order, with no planner involvement:

introduce one tiny internal helper for this slice:

- `_build_independent_context_pack(spec: ExtractionSpec, field_spec: FieldSpec) -> ContextPack`

its phase-1 shape is fixed:

- `schema_description` = deterministic whole-spec description derived from `spec.fields`
- `document_summary` = `""`
- `field_context` = `{field_spec.field_id: field_spec.description}`
- `prior_proposals` = `()`
- `retry_feedback` = `()`
- `bounds` = default `ContextBudget()`
- `candidate_overflow` = `None`

do not call bare `ContextPack()` in this slice; the required fields must be set explicitly by this helper (or an exactly equivalent explicit construction at the call site).

for each `FieldSpec` in `spec.fields`:

1. seam C — `RegexCandidateStrategy.generate(field_spec, document_view, instance_hint=None)`
2. seam D — `SingletonSelector.select(field_spec, candidate_set, _build_independent_context_pack(spec, field_spec), instance_state=None)`
3. seam E — `CardinalitySelectionAdapter.adapt(selection, candidate_set, field_spec)`
4. seam F.layer1 + F.layer2 — `LayeredProposalValidator.validate(proposed, field_spec, document_view, schema_cls=schema_cls)`

after the per-field loop:

5. seam G.resolver — `DeterministicInstanceResolver.resolve(validated_fields, candidate_sets, spec, instance_plan=None)`
6. build `ExtractionResult`

requirements:

- preserve `spec.fields` declaration order throughout the per-field pass
- the independent-strategy context is built explicitly by `_build_independent_context_pack(...)`, not by assuming `ContextPack()` defaults:
  - deterministic whole-spec `schema_description`
  - empty `document_summary`
  - current-field-only `field_context`
  - no prior proposals
  - no retry feedback
  - no candidate overflow metadata
  - default bounds
- collect:
  - all `CandidateSet`s
  - all `ValidatedField`s
  - all pre-resolver negatives
- pre-resolver negatives in this slice are:
  - seam E `NegativeOutcome`
  - seam F layer-1 `NegativeOutcome`
  - seam F layer-2 `ValidationFailure` escalated per section 5 below

implementation-shape constraints:

- do not call seam G.planner in this slice
- do not call seam F layer 3 in this slice
- do not emit or consume `InstanceState`
- do not call `Budget.check()` or `Budget.record(...)` in this slice; the landed path is algorithmic and emits no `UsageEvent`
- do not invoke `Reporter` from the strategy

### 5. route seam-F `ValidationFailure` honestly under no-retry policy

this is the first place where execution owns real failure routing.

requirements:

- phase-1 `ExecutorPolicy.on_validation_failure="fail"` means:
  - every `ValidationFailure(layer="field", ...)` is escalated immediately to:
    - `NegativeOutcome(category="validation", code="field_failure", field_id=<same>, instance_key=None, reason=<failure.reason>, candidate_count=None)`
- the escalated negative joins the pre-resolver negative list in field order
- there is no retry loop
- there is no conversion back into `ValidationFailure` after escalation

attachment rule in this independent/document-scope slice:

- if resolver returns one or more `InstanceResult`s, attach **all** pre-resolver negatives to the single returned instance
  - phase-1 independent strategy with `plan=None` should yield at most one final instance from the landed resolver
- if resolver returns `()`:
  - return `ExtractionResult(outcome="failed", instances=(), ...)`
  - do **not** fabricate a negative-only `InstanceResult`
  - include the pre-resolver negatives in the minimal `ExecutionTrace.events` payload for diagnosis in this degenerate case

implementation-shape constraints:

- do not invent instance-layer `validation.instance.*` handling here
- do not widen `NegativeOutcome` or `ExtractionResult` to carry detached negatives
- do not mutate resolver-owned `InstanceResult`s; rebuild the instance immutably when appending pre-resolver negatives

### 6. assemble `ExtractionResult` honestly

requirements:

- `ExtractionResult.instances` is canonical
- `ExtractionResult.proposals()` / `.negatives()` / `.stream()` continue to work through the landed core methods
- `.to_pydantic()` / `.usage()` / `.interview()` remain typed stubs raising `NotImplementedError`
  - current implementation note: ADR-0015 later replaced `.usage()` with a captured usage-event projection; `.interview()` remains stubbed
- build:
  - `document_id = document_view.document_id`
  - `spec_version = spec.version`
  - `strategy = "independent"`
  - `instances = <final tuple>`
  - `trace = ExecutionTrace(trace_id=<deterministic>, events=<minimal tuple>)`
  - `replay_artifact_ref = ""`

phase-1 outcome rollup:

- `complete` when `instances != ()` and every `InstanceResult.outcome == "complete"`
- `partial` when any instance is `partial`
- `failed` when `instances == ()`

trace / replay rules for this slice:

- `ExecutionTrace` is a minimal honest stub, not a fake OTEL export
- `trace_id` must be deterministic over stable run material; a sensible phase-1 shape is a `stable_hash(...)` over `(document_id, spec.version, "serial", "independent")`
- `events` may be `()` on success
- when the run ends with `instances == ()` after pre-resolver negatives occurred, record a minimal deterministic event payload containing those negatives so the failure is inspectable
- `replay_artifact_ref` stays the empty string; replay is owned by a later thread

implementation-shape constraints:

- do not invent a real replay artifact writer
- do not invent `.usage()` capture or interview capture
- do not fabricate `ExecutionTrace` semantics beyond what this slice actually does

### 7. keep Reporter and Budget honest but minimal

requirements:

- land a real default `TokenCountBudget` in `src/extractx/execution/budget.py`
- land a real minimal no-op reporter implementation in `src/extractx/execution/reporter.py`
- `Runtime` must bind both

phase-1 discipline:

- `TokenCountBudget` is constructible and obeys the `Budget` protocol, but this slice does not emit `UsageEvent`s, so its counters stay at zero
- reporter is bound and constructible, but this slice does not thread step events through it; `ExecutionTrace` assembly remains executor-owned in phase 1

implementation-shape constraints:

- do not add pricing logic
- do not add OTEL exporters
- do not widen the `Reporter` protocol just to simulate activity

### 8. package wiring and public surface

requirements:

- export the landed execution surfaces honestly:
  - `Runtime`
  - `ExecutorPolicy`
- wire package `__init__` files accordingly
- update the public smoke test so it proves `run_extraction(...)` is callable and actually returns an `ExtractionResult` on the supported slice

write-scope note:

- the main edits should be in:
  - `src/extractx/api.py`
  - `src/extractx/execution/**`
  - minimal wiring in `src/extractx/__init__.py`
  - the smallest supporting internal registry edit under `src/extractx/schema/**`
  - focused tests under `tests/contracts/`, `tests/integration/`, `tests/smoke/`, and `tests/strategies/`

## Explicit drifts to acknowledge in the implementation

surface these in code comments or final report; do not silently invent around them:

1. **schema-class handoff drift**
   - `ExtractionSpec.source_schema_ref` is not a live schema resolution path
   - this slice uses an in-process registry keyed by `spec.version`

2. **strategy-default drift**
   - docs say `strategy_binding=None` is an executor-policy concern
   - this slice does **not** solve generic default strategy selection; it supports only explicit regex-bound fields

3. **reporter/trace drift**
   - `Reporter` protocol is still intentionally thin
   - this slice assembles a minimal `ExecutionTrace` directly in the executor rather than pretending seam K is fully landed

4. **runtime capability drift**
   - `Runtime` binds `llm` / `nlp` / `fetch`, but the supported path does not consume them
   - missing soft-compute capabilities are therefore not setup failures in this slice

## Guardrails

- **write scope:** `src/extractx/api.py`, `src/extractx/execution/**`, the narrow internal schema registry edit needed for pydantic-backed specs, minimal package-export edits, and focused tests
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly
- **no dependency changes**
- **no iterative strategy**
- **no seam G.planner wiring**
- **no seam F layer 3**
- **no replay writer / manifest / interview implementation**
- **no pydantic-ai selector path**
- **no default strategy invention for `strategy_binding=None`**
- **no default adapter inference beyond the narrow `str | bytes -> TextAdapter` rule**
- **no fake budget exhaustion or fake usage events**
- **no commits or pushes** unless separately asked

## Focused proof

add focused tests under `tests/contracts/`, `tests/integration/`, `tests/smoke/`, and `tests/strategies/`.

minimum proof targets:

- **surface:**
  - `Runtime` is constructible and exported at the top level
  - `ExecutorPolicy` is constructible and exported at the top level
  - `run_extraction(...)` returns `ExtractionResult` on the supported path
- **determinism:**
  - same `(document, spec, runtime, policy)` yields byte-identical `ExtractionResult`
  - same `(document, spec, runtime, policy)` yields byte-identical `ExecutionTrace.trace_id`
- **supported-path integration:**
  - one real text document + one explicit regex-bound spec + `Runtime()` + `ExecutorPolicy(strategy="independent")` runs end to end and yields a real `ExtractionResult`
  - the result contains one `InstanceResult`
  - the flattened `ResolvedFieldProposal`s match the validated values from the landed seams
- **pydantic-backed seam-F handoff:**
  - a spec built via `ExtractionSpec.from_pydantic(...)` with a pydantic `field_validator` reaches seam F via the executor-owned `schema_cls` handoff and produces the validated normalized value
  - executor does **not** resolve the schema class from `source_schema_ref`
- **manual seam-F path:**
  - a manually constructed spec with explicit `ValidationBinding.normalizer` runs end to end with `schema_cls=None`
- **unsupported-path failure:**
  - `policy.strategy="iterative"` raises `InfrastructureError` before the run starts
  - `strategy_binding=None` raises `InfrastructureError` before the run starts
  - `grounded_proposal` binding raises `InfrastructureError` before the run starts
  - non-`str` / non-`bytes` document input raises `InfrastructureError`
- **failure routing:**
  - a seam-F layer-2 `ValidationFailure` becomes `NegativeOutcome(category="validation", code="field_failure", ...)`
  - when at least one field validates, that negative lands on the sole final `InstanceResult`
  - when no field validates and only pre-resolver negatives exist, `ExtractionResult.outcome == "failed"` and `instances == ()`
- **result rollup:**
  - no negatives → `ExtractionResult.outcome == "complete"`
  - one or more negatives on the sole instance → `ExtractionResult.outcome == "partial"`
  - zero instances → `ExtractionResult.outcome == "failed"`
- **stub honesty:**
  - `.to_pydantic()` still raises `NotImplementedError`
  - `.usage()` still raises `NotImplementedError`
    - current implementation note: ADR-0015 later replaced `.usage()` with a captured usage-event projection
  - `.interview()` still raises `NotImplementedError`
- **smoke:**
  - replace the old “exposed but unimplemented” smoke expectation with a real supported-slice smoke test

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/api.py`
- `src/extractx/execution/runtime.py`
- `src/extractx/execution/policy.py`
- `src/extractx/execution/executor/protocol.py`
- `src/extractx/execution/executor/serial.py`
- `src/extractx/execution/strategies/independent.py`
- `src/extractx/execution/budget.py`
- `src/extractx/execution/reporter.py`

with the smallest supporting edits in:

- `src/extractx/__init__.py`
- `src/extractx/schema/**` (only for the narrow in-process `schema_cls` registry)

include in your final report:

- exact files changed
- the supported document surface for this slice
- the exact unsupported execution paths that fail before the run starts
- the concrete `ValidationFailure -> NegativeOutcome` mapping you landed
- the exact `trace_id` composition
- the exact internal `schema_cls` handoff shape you landed (and confirm it does **not** use `source_schema_ref`)
- any follow-on that should become a coordinator-owned thread rather than widening this slice

## Success criteria

- `run_extraction(...)` is real for one narrow supported path
- `SerialExecutor` and `IndependentStrategy` are real
- `Runtime` and `ExecutorPolicy` are real and exported at the top level
- the landed seams A/C/D/E/F.layer1/F.layer2/G.resolver are wired in order
- `ValidationFailure` routing is explicit and deterministic under no-retry policy
- `ExtractionResult` is assembled honestly with canonical `instances`
- unsupported strategies / document shapes fail loudly before the run starts
- replay, interview, async execution, iterative strategy, and layer 3 remain out of scope and honestly stubbed
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`

## Downstream consequences

- this slice is the denominator for the first real benchmark corpus
- the next clean thread after this is:
  - seam F layer 3 **or**
  - a narrow execution follow-on if the implementation exposes a sharper substrate gap than expected
- do **not** broaden document types, strategy defaults, or replay in the same thread just because the execution code is now open
