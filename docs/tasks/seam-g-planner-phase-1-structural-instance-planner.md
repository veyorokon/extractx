# Task: implement seam G.planner phase 1 deterministic structural instance planning

*This is seam G.planner phase 1. Make the planner seam real with one deterministic `StructuralInstancePlanner`, not the soft/neural or graph planners. The first landed planner should produce tentative `InstancePlan`s and pure boundary-defining helpers without pulling in resolver behavior, layer 3, or execution/reporting orchestration.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; seam G summary; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam G.planner in full**, **§7 seam G.resolver** (to understand what planner is *not*), **§7 seam F structural note** (layer 3 stays out), **§9 canonical objects** for `InstanceKey`, `InstancePlan`, `GroupingEvidence`, `GroupingPolicy`, **§10 three-tier public surface**, **§11 iterative pseudocode** (especially the pre-plan C→D-only flow), **§16 project layout**, and **§17 proof table entries for G.planner**
- [`docs/adr/0003-single-canonical-layer3-no-resolver-validators.md`](../adr/0003-single-canonical-layer3-no-resolver-validators.md) — layer 3 stays out of planner and resolver
- [`docs/tasks/core-contracts-and-objects.md`](core-contracts-and-objects.md) — use landed core `InstanceKey`, `InstancePlan`, `GroupingEvidence`, `NegativeOutcome`, and version helpers instead of inventing planner-local shapes
- [`docs/tasks/seam-c-deterministic-candidate-generation.md`](seam-c-deterministic-candidate-generation.md) — boundary-defining pre-plan helpers consume real `CandidateSet`s
- [`docs/tasks/seam-d-algorithmic-selector-phase-1.md`](seam-d-algorithmic-selector-phase-1.md) — boundary-defining pre-plan helpers consume real `Selection`s; `SELECTED` and `AMBIGUOUS` carry candidate ids only
- [`docs/tasks/seam-f-phase-1-candidate-and-field-validation.md`](seam-f-phase-1-candidate-and-field-validation.md) — planner remains upstream of seam F; no canonical `ValidatedField` or layer-3 behavior here

## Goal

implement seam G.planner so a deterministic `StructuralInstancePlanner` can:

- consume a real `DocumentView`, `ExtractionSpec`, and advisory boundary-anchor spans
- emit either:
  - a real `InstancePlan`, or
  - a typed `NegativeOutcome("planning", ...)`
- keep planner output tentative and separate from final instance truth

and, in the same thread, land pure helpers for the tentative boundary-defining pre-pass without implementing the actual C→D orchestration loop.

**"done" in one sentence:** a deterministic `StructuralInstancePlanner` produces tentative `InstancePlan`s (or typed planning negatives) from structural evidence plus advisory boundary anchors, while pure helpers in `instances/boundary.py` encode the boundary-defining ordering and anchor-collection rules without pulling in execution strategy, resolver, or layer-3 behavior.

## Scope

numbered implementation areas. do each in order.

### 1. make the seam-G.planner protocol explicit

implement the `InstancePlanner` callable surface in `src/extractx/core/contracts.py`.

requirements:

- define the phase-1 protocol method explicitly:
  - `plan(document_view: DocumentView, spec: ExtractionSpec, boundary_anchor_spans: tuple[SourceSpan, ...] = ()) -> InstancePlan | NegativeOutcome`
- keep it sync, pure, and deterministic for phase 1
- planner input is:
  - `document_view`
  - `spec`
  - advisory `boundary_anchor_spans` gathered upstream by the iterative pre-plan C→D flow
- planner output is:
  - `InstancePlan` on success
  - `NegativeOutcome(category="planning", ...)` on canonical planner failure

implementation-shape constraints:

- one method only unless the docs already require another
- no async planner protocol in this task
- no `UsageEvent` emission in this task; algorithmic planners do not emit provider usage
- no `ContextPack` parameter in phase 1

### 2. implement the phase-1 structural planner

implement the concrete deterministic planner in `src/extractx/instances/planners/structural.py`.

the concrete class name is fixed for this task:

- `StructuralInstancePlanner`

requirements:

- planner output stays **tentative**
- emit `InstancePlan` with:
  - `tentative_keys`
  - `grouping_evidence`
  - `producer_version`
- emit `NegativeOutcome(category="planning", ...)` for canonical planner failure

phase-1 planning policy is fixed:

- input advisory anchors come from `boundary_anchor_spans`
- planner may also use a narrow deterministic structural fallback derived from the `DocumentView` itself
- the narrowest honest fallback for phase 1 is:
  - if `boundary_anchor_spans` is non-empty:
    - deduplicate identical spans while preserving input order
    - each distinct span becomes one tentative instance anchor
  - else:
    - attempt one document-scope structural anchor derived deterministically from `document_view`
    - the natural phase-1 fallback is one whole-document `SourceSpan` in the `DocumentView`'s declared `text_anchor_space` (covering the full normalized-text byte range for `normalized_text` adapters, or the full source-byte range when that range is directly and honestly knowable for `source_bytes` adapters)
    - if a deterministic document-scope anchor can be formed, emit one tentative key
    - if no advisory anchors and no structural anchor can be formed, emit `NegativeOutcome(category="planning", code="no_tentative_keys", ...)`

`GroupingPolicy.max_instances` is load-bearing in phase 1:

- if the number of tentative keys would exceed `spec.grouping_policy.max_instances`, emit:
  - `NegativeOutcome(category="planning", code="max_exceeded", field_id=None, instance_key=None, candidate_count=None, reason="max_exceeded")`

implementation-shape constraints:

- no resolver behavior
- no merge/split/final-instance semantics
- no candidate generation or selector invocation inside the planner
- no soft compute
- no graph clustering
- no hidden randomness or unstable ordering

### 3. form tentative `InstanceKey`s and `GroupingEvidence` honestly

for every tentative instance the planner emits:

requirements:

- `InstanceKey.group_anchors` is the planner anchor tuple for that tentative instance
- `InstanceKey.ordinal` is deterministic and stable from planner output order
- `InstanceKey.group_id` is a deterministic hash of `(group_anchors, group_key_material)` per the architecture
- `group_key_material` for phase 1 must be a small, deterministic planner-owned tuple sufficient to distinguish otherwise-identical anchors when needed; document the exact tuple in the final report
- `GroupingEvidence` must be:
  - `stage="planned"`
  - `anchor_spans` equal to the planner anchors that informed the plan
  - `producer_version` equal to the planner's `producer_version`
- `GroupingEvidence.clustering_signals` may carry a small deterministic typed mapping describing the structural mode used (`boundary_anchors`, `document_scope_fallback`, counts, etc.), but must stay JSON-safe and minimal
- `GroupingEvidence.confidence` may be `None` for the deterministic planner

implementation-shape constraints:

- do not invent a separate `PlanningEvidence` type
- do not mutate `InstancePlan`, `InstanceKey`, or `GroupingEvidence` after construction
- no timestamps or non-deterministic ids

### 4. land pure boundary-defining pre-plan helpers

implement pure helpers in `src/extractx/instances/boundary.py` for the tentative boundary-defining mechanic described in the architecture.

requirements:

- land helper(s) that make the boundary-defining rules concrete without implementing execution orchestration
- minimum helper surface:
  - `order_boundary_defining_fields(spec: ExtractionSpec) -> tuple[FieldSpec, ...]`
  - deterministic ordering of boundary-defining fields:
    - priority descending
    - declaration order tie-break
  - `collect_advisory_anchors(pairs: Sequence[tuple[CandidateSet, Selection]]) -> tuple[SourceSpan, ...]`
  - advisory anchor extraction from already-produced `CandidateSet` + `Selection` pairs:
    - `SELECTED` contributes its selected candidates' `source_span`s
    - `AMBIGUOUS` contributes all returned ids' `source_span`s in selection order
    - `ABSTAINED` and `NO_CANDIDATES` contribute no anchors
- helpers are pure and mechanical:
  - no `Reporter`
  - no `Budget`
  - no `UsageEvent`
  - no seam E/F invocation
  - no canonical negatives

implementation-shape constraints:

- do not implement the full iterative pre-plan loop from §11 here
- do not call `CandidateStrategy` or `Selector` from inside the helpers
- take already-produced `CandidateSet` / `Selection` inputs
- duplicate advisory anchor spans may be preserved here; planner dedup policy lives in the planner implementation

### 5. producer_version for algorithmic planning

attach `producer_version` to every emitted `InstancePlan` using the existing core helper.

requirements:

- use `algorithmic_producer_version(...)` from `src/extractx/core/versions.py`
- compose `code_hash` using the same stable pattern already used by seams C/D/F:
  - stable hash of `"{PlannerClass.__module__}.{PlannerClass.__qualname__}"`
- do not invent a second producer-version scheme

implementation-shape constraints:

- no model id
- no prompt hash
- no timestamp or runtime-dependent planner identity

### 6. package wiring

implement the minimal instances/planners package surface so seam G.planner phase 1 is importable and testable.

requirements:

- wire:
  - `src/extractx/instances/planners/structural.py`
  - `src/extractx/instances/planners/__init__.py`
  - `src/extractx/instances/boundary.py`
  - `src/extractx/instances/__init__.py`
- leave:
  - `src/extractx/instances/planners/graph.py`
  - `src/extractx/instances/planners/neural.py`
  - `src/extractx/instances/resolvers/**`
  as stubs unless a tiny edit is needed for package coherence

write-scope note:

- the only supporting edits outside `src/extractx/instances/**` should be the smallest ones required in:
  - `src/extractx/core/contracts.py`
  - `src/extractx/core/__init__.py`
- do not widen top-level `extractx/__init__.py` in this task

### 7. explicit non-goals for this task

leave these out:

- `InstanceResolver`
- final instance assignment
- promotion to `ResolvedFieldProposal`
- layer 3 / `model_validator` / `InstanceValidator`
- execution/runtime/reporter/budget behavior
- `UsageEvent` emission
- soft / neural / graph planner behavior
- replay artifact writing
- interview capture
- materialization
- full iterative strategy orchestration
- planner-conditioned retries or failure routing through `ExecutorPolicy`

typed stubs may remain where needed, but do not invent behavior owned by later or separate threads.

## Guardrails

- **write scope:** `src/extractx/instances/**`, focused tests, and only the smallest supporting edits in:
  - `src/extractx/core/contracts.py`
  - `src/extractx/core/__init__.py`
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly. if so, stop and report with the standard pushback shape.
- **no dependency changes** in this task
- **no resolver behavior** in planner code
- **no layer 3**
- **no executor/reporting/budget orchestration**
- **no silent coercion.** planner failure becomes typed `NegativeOutcome("planning", ...)`; structural seam violations in pure helpers may raise a local `ValueError` subtype
- **no commits or pushes** unless separately asked. leave the branch ready for review

## Focused proof

add focused tests primarily under `tests/contracts/` and `tests/instances/`.

minimum proof targets to cover:

- `InstancePlanner.plan(...) -> InstancePlan | NegativeOutcome` exists on the protocol surface
- same `(document_view, spec, boundary_anchor_spans)` yields byte-identical output across repeated calls
- algorithmic planner emits `producer_version = "code:{code_hash}"` using the core helper
- non-empty advisory `boundary_anchor_spans` produce tentative keys anchored to those spans
- duplicate advisory anchor spans are deduplicated by the planner while preserving stable order
- zero boundary-defining anchors with a valid structural fallback produce exactly one tentative key
- zero boundary-defining anchors and no structural fallback produce `NegativeOutcome(category="planning", code="no_tentative_keys", ...)`
- `GroupingPolicy.max_instances` violation produces `NegativeOutcome(category="planning", code="max_exceeded", ...)`
- `GroupingEvidence.stage == "planned"` and its `producer_version` matches the planner's `producer_version`
- planner-produced `InstanceKey.group_anchors` share a single `text_anchor_space` matching the `DocumentView`'s adapter subcontract
- `boundary.py` field-ordering helper sorts by priority descending, then declaration order
- `boundary.py` advisory-anchor helper:
  - includes `SELECTED` ids' spans
  - includes `AMBIGUOUS` ids' spans in selection order
  - ignores `ABSTAINED` / `NO_CANDIDATES`
- no seam E/F behavior is smuggled into boundary helpers or planner
- no resolver behavior is smuggled into planner

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/instances/planners/structural.py`
- `src/extractx/instances/boundary.py`
- `src/extractx/instances/planners/__init__.py`
- `src/extractx/instances/__init__.py`

with only minimal supporting edits elsewhere if required by the seam-G.planner surface.

include in your final report:

- exact files changed
- the concrete planner class name
- the exact `group_key_material` tuple used in `InstanceKey.group_id` hashing
- the deterministic structural fallback used when `boundary_anchor_spans` is empty
- any remaining ambiguity that should become a coordinator-owned follow-on thread rather than more code

## Success criteria

- `InstancePlanner` has an explicit callable surface
- seam G.planner is real for one deterministic `StructuralInstancePlanner`
- planner output is tentative and clearly separate from final instance truth
- boundary-defining pre-plan rules exist as pure helpers, not execution orchestration
- `GroupingPolicy.max_instances` is enforced at the planner seam
- no resolver/layer-3/runtime behavior is smuggled in
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pyright`
- top-level repo state remains coherent with the architecture/doc pact

## Architecture drift acknowledged

these drift points between architecture prose and the landed code/task sequence must be resolved by pinning the worker to **code reality**. do not invent new fields; surface to the coordinator if a new contradiction arises.

1. **planner return type.** the seam map lists `InstancePlan` as planner output, but §7 seam G.planner also defines canonical failure as `NegativeOutcome("planning", "no_tentative_keys")`, and the proof table expects `NegativeOutcome("planning", "max_exceeded")`. phase 1 therefore pins the protocol to `InstancePlan | NegativeOutcome`.
2. **planner inputs: `ContextPack` vs advisory anchors.** §7 seam G.planner lists `bounded ContextPack` as an input, while the iterative pseudocode in §11 calls `G.planner(doc, spec, tuple(boundary_defining_spans))`. phase 1 follows the concrete pseudocode and exposes `boundary_anchor_spans` explicitly on the planner protocol; execution-owned pre-plan orchestration remains a later thread.
3. **boundary-defining pre-plan orchestration location.** the architecture defines a full C→D pre-plan loop with trace-only outcomes, but execution/reporter/budget seams are not landed yet. phase 1 lands only pure helper logic in `instances/boundary.py`, not the orchestration loop.
4. **soft planner shapes remain out of scope.** §7 names `GraphInstancePlanner` and `NeuralInstancePlanner`; phase 1 lands only `StructuralInstancePlanner`. soft planner pinning, usage emission, retries, and replay fixtures remain later threads.

if any additional contradiction surfaces mid-implementation that cannot be resolved by pinning to code reality, stop and report using the pushback shape (current contract / observed gap / consequence / proposed cleaner pattern / seam ownership impact / clarification vs architecture change / proof target).

## Downstream consequences

- gives iterative execution a real tentative-planning surface to run before per-instance extraction
- keeps planner truth clearly tentative so resolver remains the sole owner of final instance truth
- establishes pure boundary-defining helpers the execution substrate can call later without duplicating policy
- leaves resolver, layer 3, runtime orchestration, and soft planners for focused follow-on threads
- if this task exposes a real contradiction in the current seam-G.planner contract, that becomes a new coordinator-owned thread before more implementation proceeds
