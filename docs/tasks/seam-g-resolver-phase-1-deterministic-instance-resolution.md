# Task: implement seam G.resolver phase 1 deterministic instance resolution

*This is seam G.resolver phase 1. Make the resolver seam real with one deterministic `DeterministicInstanceResolver`, not the soft/neural or graph resolvers. The first landed resolver should own final `InstanceKey` assignment, promote `ValidatedField`s into `ResolvedFieldProposal`s, and emit `InstanceResult`s under the documented precedence rule without pulling in layer 3, execution orchestration, or materialization.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; seam G summary; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam G.resolver in full**, **§7 seam G.planner** (to understand tentative planner output), **§7 seam F structural note** (layer 3 stays out), **§9 canonical objects** for `ValidatedField`, `ResolvedFieldProposal`, `InstanceResult`, `InstanceKey`, `GroupingEvidence`, **§10 three-tier public surface**, **§11 independent + iterative pseudocode**, **§16 project layout**, and **§17 proof table entries for G.resolver**
- [`docs/adr/0003-single-canonical-layer3-no-resolver-validators.md`](../adr/0003-single-canonical-layer3-no-resolver-validators.md) — **load-bearing**. resolver does not invoke validators; precedence authority #4 is `InstancePlan` priors, not validator consistency
- [`docs/adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md`](../adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md) — `group_anchors` are source-anchored and may carry either `text_anchor_space`
- [`docs/tasks/core-contracts-and-objects.md`](core-contracts-and-objects.md) — use landed core `InstanceKey`, `InstanceResult`, `ResolvedFieldProposal`, `NegativeOutcome`, `GroupingEvidence`, and version helpers instead of inventing resolver-local shapes
- [`docs/tasks/seam-e-cardinality-selection-adapter-phase-1.md`](seam-e-cardinality-selection-adapter-phase-1.md) — resolver consumes `ProposedField.tentative_instance_key` semantics established there
- [`docs/tasks/seam-f-phase-1-candidate-and-field-validation.md`](seam-f-phase-1-candidate-and-field-validation.md) — resolver consumes real `ValidatedField`s; layer 3 stays out
- [`docs/tasks/seam-g-planner-phase-1-structural-instance-planner.md`](seam-g-planner-phase-1-structural-instance-planner.md) — resolver consumes optional `InstancePlan`; planner output is tentative and advisory

## Goal

implement seam G.resolver so a deterministic `DeterministicInstanceResolver` can:

- consume all real `ValidatedField`s, all real `CandidateSet`s, the `ExtractionSpec`, and an optional `InstancePlan`
- assign final `InstanceKey`s under the documented precedence rule
- promote accepted `ValidatedField`s into `ResolvedFieldProposal`
- emit `tuple[InstanceResult, ...]`

with ambiguity and cardinality failures represented as typed `NegativeOutcome`s rather than hidden policy or validator-guided backtracking.

**"done" in one sentence:** a deterministic `DeterministicInstanceResolver` consumes `ValidatedField`s + `CandidateSet`s + `ExtractionSpec` + optional `InstancePlan`, applies the documented precedence rule without invoking validators, promotes accepted fields into `ResolvedFieldProposal`, and emits stable `InstanceResult`s with `GroupingEvidence(stage="resolved", ...)`.

## Scope

numbered implementation areas. do each in order.

### 1. make the seam-G.resolver protocol explicit

implement the `InstanceResolver` callable surface in `src/extractx/core/contracts.py`.

requirements:

- define the phase-1 protocol method explicitly:
  - `resolve(validated_fields: tuple[ValidatedField, ...], candidate_sets: tuple[CandidateSet, ...], spec: ExtractionSpec, instance_plan: InstancePlan | None = None) -> tuple[InstanceResult, ...]`
- keep it sync, pure, and deterministic for phase 1
- resolver input is:
  - all `ValidatedField`s for the run
  - all `CandidateSet`s for the run
  - the `ExtractionSpec` for field metadata (`GroupingBinding`, `Cardinality`, declaration order)
  - optional `InstancePlan`
- resolver output is:
  - `tuple[InstanceResult, ...]`

implementation-shape constraints:

- one method only unless the docs already require another
- no async resolver protocol in this task
- no `UsageEvent` emission in this task; algorithmic resolvers do not emit provider usage
- `instance_plan` is `InstancePlan | None`, never `NegativeOutcome`; planning failure stays upstream of the resolver

### 2. implement the deterministic resolver

implement the concrete deterministic resolver in `src/extractx/instances/resolvers/deterministic.py`.

the concrete class name is fixed for this task:

- `DeterministicInstanceResolver`

requirements:

- resolver owns final instance truth
- consume:
  - `validated_fields`
  - `candidate_sets`
  - `spec`
  - optional `instance_plan`
- emit:
  - `tuple[InstanceResult, ...]`

phase-1 structural policy is fixed:

- if `instance_plan` is present:
  - start from `instance_plan.tentative_keys` in plan order as the initial tentative buckets
- if `instance_plan` is absent:
  - synthesize exactly one document-scope tentative bucket for the run
  - the natural phase-1 fallback is a single tentative key whose `group_anchors` are the stable ordered unique `ValidatedField.proposed.source_span`s for the run
  - if `validated_fields` is empty, emit `()`

- after assignment:
  - instances that resolve to zero proposals are dropped
  - each remaining instance emits:
    - `instance_key`
    - `outcome`
    - `field_proposals`
    - `negative_outcomes`
    - `grouping_evidence`

implementation-shape constraints:

- no soft compute
- no graph partitioning
- no validator invocation
- no backtracking based on validator outcomes
- no materialization

### 3. apply the precedence rule explicitly

implement the documented precedence rule in pure resolver-owned logic, using helpers in `src/extractx/instances/precedence.py` if helpful.

the load-bearing precedence order is:

1. explicit `GroupingBinding`
2. source-anchor continuity
3. candidate co-occurrence
4. `InstancePlan` tentative scaffolds

requirements:

- authority #1 — explicit `GroupingBinding`
  - resolver must read `GroupingBinding` from `spec.fields`
  - phase-1 role handling:
    - `boundary_defining`:
      - if `validated.proposed.tentative_instance_key` is present and matches a tentative bucket, that bucket wins unless a higher-authority structural contradiction makes the assignment impossible
    - `boundary_consuming`:
      - field must attach to an existing bucket; it does not define a new boundary
      - bucket assignment falls through to authorities #2–4; there is no special `boundary_consuming` tie-break beyond "does not create a new boundary"
    - `neutral` or no binding:
      - no special authority-1 effect
- authority #2 — source-anchor continuity
  - use `ValidatedField.proposed.source_span` against tentative bucket anchors
  - the narrowest honest phase-1 rule is:
    - same `source_ref`
    - same `text_anchor_space`
    - interval overlap wins over non-overlap
    - if exactly one bucket overlaps, it wins
- authority #3 — candidate co-occurrence
  - join `ValidatedField.proposed.candidate_id_refs` back to the matching `CandidateSet` by `(field_id, tentative_instance_key)`:
    - `CandidateSet.field_id == ValidatedField.proposed.field_id`
    - `CandidateSet.instance_hint == ValidatedField.proposed.tentative_instance_key`
  - phase-1 candidate co-occurrence is a narrow deterministic structural heuristic:
    - compare the referenced candidate span(s) against tentative bucket anchors
    - exact overlap beats positive byte-gap; smaller byte-gap beats larger byte-gap
  - do not interpret arbitrary `DistanceMetric.params` in phase 1
- authority #4 — `InstancePlan` priors
  - if authorities 1–3 do not pick a unique bucket and `instance_plan` is present, use tentative-bucket prior only as the lowest authority
  - deterministic tie-break remains tentative key order

implementation-shape constraints:

- do not reintroduce ADR-0003’s removed authority “validator consistency” / “layer-3 consistency”
- no hidden fifth authority
- if a field cannot be assigned uniquely after authorities 1–4, emit typed ambiguity rather than inventing a winner

### 4. emit typed ambiguity and cardinality negatives

requirements:

- if authorities 1–4 leave grouping ambiguous for a proposal, emit:
  - `NegativeOutcome(category="resolution", code="ambiguous_grouping", field_id=<affected>, instance_key=<tentative>, reason="ambiguous_grouping")`
- attach the ambiguity negative to the tentative instance with the strongest partial signal from authorities 1–4
- deterministic tie-break for equal-strength partial signal:
  - tentative key order
- the affected proposal does **not** become a `ResolvedFieldProposal`

resolution-stage cardinality handling in phase 1:

- if `FieldSpec.cardinality is Cardinality.ONE` and the resolver would otherwise produce proposals for that field in more than one final instance, emit:
  - `NegativeOutcome(category="resolution", code="cardinality.one_multiple_instances", field_id=<field>, instance_key=<affected>, reason="cardinality.one_multiple_instances")`
- the affected proposals do not become `ResolvedFieldProposal`s
- if `FieldSpec.cardinality is Cardinality.PER_INSTANCE` and the resolver would otherwise produce more than one surviving proposal for that field in the same final instance, emit:
  - `NegativeOutcome(category="resolution", code="cardinality.per_instance_multi_in_instance", field_id=<field>, instance_key=<affected>, reason="cardinality.per_instance_multi_in_instance")`
- the affected proposals do not become `ResolvedFieldProposal`s
- phase-1 resolver does **not** invent additional `cardinality.optional_*` or `cardinality.many_*` codes unless the existing architecture later requires them

implementation-shape constraints:

- ambiguity and cardinality negatives are typed outcomes, not raised exceptions
- no document-scope free-floating negatives; negatives land on an `InstanceResult`

### 5. promote `ValidatedField` to `ResolvedFieldProposal` honestly

for every validated field that survives resolution:

requirements:

- promote without mutation:
  - `field_id = validated.proposed.field_id`
  - `instance_key = final InstanceKey`
  - `raw_value = validated.proposed.raw_value`
  - `evidence_text = validated.proposed.evidence_text`
  - `source_span = validated.proposed.source_span`
  - `evidence_spans = validated.proposed.evidence_spans`
  - `normalized_value = validated.normalized_value`
  - `proposal_provenance` is built from the landed lifecycle data already present on the types:
    - `strategy_id = validated.proposed.strategy_id`
    - `candidate_id_refs = validated.proposed.candidate_id_refs`
    - `selector_producer_version = validated.proposed.selector_producer_version`
    - `grounded_producer_version = validated.proposed.grounded_producer_version`
    - `field_validation_version = validated.field_validation_version`
  - exact field set must match the landed `ProposalProvenance` type; do not invent new provenance fields
- no mutation of `ValidatedField`
- no enrichment from runtime/replay/interview layers in this task

implementation-shape constraints:

- do not synthesize new evidence spans
- do not normalize again
- do not materialize to pydantic here

### 6. form final `InstanceKey`s and `GroupingEvidence` honestly

for every final instance the resolver emits:

requirements:

- final `InstanceKey.group_anchors` must reference real `SourceSpan`s
- phase-1 final anchor policy is fixed:
  - if one or more surviving proposals in the instance come from `boundary_defining` fields, final `group_anchors` are those proposals' `source_span`s in stable field/declaration order
  - else if the tentative bucket had anchors, carry those anchors forward
  - else use the stable ordered unique `source_span`s of the surviving proposals in that instance
- final `InstanceKey.group_id` is a deterministic hash of `(group_anchors, group_key_material)`
- `group_key_material` for phase 1 must be a small deterministic resolver-owned tuple; document it in the final report
- `GroupingEvidence` must be:
  - `stage="resolved"`
  - `anchor_spans` equal to the final `group_anchors`
  - `producer_version` equal to the resolver’s `producer_version`
- `GroupingEvidence.clustering_signals` may carry a small deterministic JSON-safe mapping describing which authority resolved the instance and whether tentative priors were used
- `GroupingEvidence.confidence` may be `None` for the deterministic resolver

instance outcome in phase 1:

- `complete` when `negative_outcomes == ()`
- `partial` when one or more `NegativeOutcome`s landed on the instance

implementation-shape constraints:

- do not invent a separate resolved-evidence type
- no timestamps or non-deterministic ids
- `group_anchors` may carry mixed `text_anchor_space`s across a group if that is what the assigned proposals/planner anchors legitimately contain; do not force uniformity across a final group

### 7. producer_version for algorithmic resolution

attach `producer_version` to every emitted `GroupingEvidence(stage="resolved", ...)` using the existing core helper.

requirements:

- use `algorithmic_producer_version(...)` from `src/extractx/core/versions.py`
- compose `code_hash` using the same stable pattern already used by seams C/D/F/G.planner:
  - stable hash of `"{ResolverClass.__module__}.{ResolverClass.__qualname__}"`
- do not invent a second producer-version scheme

implementation-shape constraints:

- no model id
- no prompt hash
- no timestamp or runtime-dependent resolver identity

### 8. package wiring

implement the minimal resolver package surface so seam G.resolver phase 1 is importable and testable.

requirements:

- wire:
  - `src/extractx/instances/resolvers/deterministic.py`
  - `src/extractx/instances/resolvers/__init__.py`
  - `src/extractx/instances/precedence.py`
  - `src/extractx/instances/__init__.py`
- leave:
  - `src/extractx/instances/resolvers/graph.py`
  - `src/extractx/instances/resolvers/neural.py`
  as stubs unless a tiny edit is needed for package coherence

write-scope note:

- the only supporting edits outside `src/extractx/instances/**` should be the smallest ones required in:
  - `src/extractx/core/contracts.py`
  - `src/extractx/core/__init__.py`
- do not widen top-level `extractx/__init__.py` in this task

### 9. explicit non-goals for this task

leave these out:

- seam F layer 3
- `InstanceValidator` invocation
- pydantic `model_validator` invocation
- execution/runtime/reporter/budget behavior
- `UsageEvent` emission
- soft / neural / graph resolver behavior
- replay artifact writing
- interview capture
- materialization
- planner orchestration
- retry loops or executor-policy escalation

typed stubs may remain where needed, but do not invent behavior owned by later or separate threads.

## Guardrails

- **write scope:** `src/extractx/instances/**`, focused tests, and only the smallest supporting edits in:
  - `src/extractx/core/contracts.py`
  - `src/extractx/core/__init__.py`
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly. if so, stop and report with the standard pushback shape.
- **no dependency changes** in this task
- **no layer 3**
- **no execution/reporting/budget orchestration**
- **no silent coercion.** ambiguity and resolution-stage cardinality failures become typed `NegativeOutcome`s
- **no commits or pushes** unless separately asked. leave the branch ready for review

## Focused proof

add focused tests primarily under `tests/contracts/` and `tests/instances/`.

minimum proof targets to cover:

- `InstanceResolver.resolve(...) -> tuple[InstanceResult, ...]` exists on the protocol surface
- same `(validated_fields, candidate_sets, spec, instance_plan)` yields byte-identical output across repeated calls
- algorithmic resolver emits `producer_version = "code:{code_hash}"` using the core helper
- if `instance_plan` is absent and validated fields are present, resolver synthesizes one document-scope final instance
- `boundary_defining` field with matching `tentative_instance_key` resolves to that tentative bucket
- source-anchor continuity picks the unique overlapping bucket when one exists
- candidate co-occurrence breaks a tie only when source continuity did not decide uniquely
- `InstancePlan` priors are used only as the lowest authority
- ambiguous grouping emits `NegativeOutcome(category="resolution", code="ambiguous_grouping", ...)` attached to one tentative/final instance, and the affected proposal is absent from `field_proposals`
- `Cardinality.ONE` spread across multiple final instances emits `NegativeOutcome(category="resolution", code="cardinality.one_multiple_instances", ...)`
- surviving `ValidatedField`s are promoted into `ResolvedFieldProposal`s without mutation
- emitted `InstanceResult.grouping_evidence.stage == "resolved"`
- emitted `InstanceResult.outcome` is `partial` iff `negative_outcomes` is non-empty
- no layer-3 behavior is smuggled in; a raising `model_validator` / `InstanceValidator` is never invoked by the resolver
- no planner failure is accepted as resolver input; `instance_plan` is `InstancePlan | None` only

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/instances/resolvers/deterministic.py`
- `src/extractx/instances/resolvers/__init__.py`
- `src/extractx/instances/precedence.py`
- `src/extractx/instances/__init__.py`

with only minimal supporting edits elsewhere if required by the seam-G.resolver surface.

include in your final report:

- exact files changed
- the concrete resolver class name
- the exact `group_key_material` tuple used in final `InstanceKey.group_id` hashing
- the exact phase-1 candidate co-occurrence heuristic landed
- how `ProposalProvenance` was assembled from the landed lifecycle objects
- any remaining ambiguity that should become a coordinator-owned follow-on thread rather than more code

## Success criteria

- `InstanceResolver` has an explicit callable surface
- seam G.resolver is real for one deterministic `DeterministicInstanceResolver`
- resolver is the sole owner of final instance truth
- the precedence rule is explicit and enforced in the documented order
- ambiguity and resolution-stage `cardinality.one_*` failures are typed `NegativeOutcome`s
- emitted `InstanceResult`s carry `GroupingEvidence(stage="resolved", ...)`
- no layer-3/runtime/soft-resolver behavior is smuggled in
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pyright`
- top-level repo state remains coherent with the architecture/doc pact

## Architecture drift acknowledged

these drift points between architecture prose and the landed code/task sequence must be resolved by pinning the worker to **code reality**. do not invent new fields; surface to the coordinator if a new contradiction arises.

1. **resolver inputs omit field metadata in seam prose.** §7 seam G.resolver lists `ValidatedField + CandidateSet + optional InstancePlan + GroupingBinding per field`, but landed lifecycle objects do not carry `GroupingBinding` or `Cardinality`. phase 1 therefore adds `spec: ExtractionSpec` explicitly to the resolver protocol so grouping bindings, field cardinality, and declaration order are available without hidden registries or ambient lookups.
2. **ADR-0003 removed validator-consistency authority.** the deterministic resolver stub still mentions `layer-3 consistency`; this is drift. phase 1 uses the accepted precedence order only: `GroupingBinding` → source-anchor continuity → candidate co-occurrence → `InstancePlan` priors.
3. **planner failure stays upstream.** seam G.planner phase 1 returns `InstancePlan | NegativeOutcome`; the resolver never consumes planning failure. `instance_plan` is therefore `InstancePlan | None`, not a planner union type.
4. **`DistanceMetric` is still a placeholder core type.** phase 1 resolver lands one narrow deterministic candidate co-occurrence heuristic and does not attempt to interpret arbitrary `DistanceMetric.params` as a generic metric DSL.

if any additional contradiction surfaces mid-implementation that cannot be resolved by pinning to code reality, stop and report using the pushback shape (current contract / observed gap / consequence / proposed cleaner pattern / seam ownership impact / clarification vs architecture change / proof target).

## Downstream consequences

- gives the rebuild its first real end-to-end multi-instance truth owner
- unblocks seam F layer 3 as a later post-resolution thread
- keeps planner truth tentative and validator truth separate
- leaves execution substrate, replay integration, and soft resolvers for focused follow-on threads
- if this task exposes a real contradiction in the current seam-G.resolver contract, that becomes a new coordinator-owned thread before more implementation proceeds
