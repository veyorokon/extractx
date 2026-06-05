# Task: implement seam D phase 1 deterministic algorithmic selector

*This is seam D phase 1. Make the selector seam real with one deterministic algorithmic selector, not the llm-backed path. The first landed selector should prove the seam contract without inventing prompt policy, soft-compute behavior, or seam E cardinality logic.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; seam D summary; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam D, §7 seam E (to understand what seam D is *not*), §8 soft-compute discipline (to understand what phase 1 is intentionally excluding), §9 canonical objects, §10 three-tier public surface, §13 public api surface notes around interview, §16 project layout, and §17 proof table entries for seam D**
- [`docs/adr/0002-pydantic-ai-default-selector-and-interview.md`](../adr/0002-pydantic-ai-default-selector-and-interview.md) — this task is intentionally *not* implementing the default llm-backed selector yet; read for boundary awareness
- [`docs/adr/0004-narrow-interview-scope-to-field-seams.md`](../adr/0004-narrow-interview-scope-to-field-seams.md) — interview capability exists, but is out of scope for this deterministic selector thread
- [`docs/adr/0005-candidate-overflow-policy.md`](../adr/0005-candidate-overflow-policy.md) — seam D sees a bounded view signal via `ContextPack.candidate_overflow`, but phase 1 should not invent new overflow policy
- [`docs/tasks/core-contracts-and-objects.md`](core-contracts-and-objects.md) — prior thread; use the landed core `Selection` / `ContextPack` / `RenderedPrompt` objects instead of reinventing them
- [`docs/tasks/seam-c-deterministic-candidate-generation.md`](seam-c-deterministic-candidate-generation.md) — seam D phase 1 is designed to consume the real `CandidateSet` surface landed by seam C

## Goal

implement seam D so a deterministic algorithmic selector can consume a real `CandidateSet` and emit a real `Selection`, with the id-only contract enforced, correct distinction between `NO_CANDIDATES` and `AMBIGUOUS`, and algorithmic `producer_version` pinned via the existing core helper.

**"done" in one sentence:** a deterministic singleton-or-ambiguous selector consumes a real `CandidateSet`, emits a real `Selection` with `producer_version="code:{code_hash}"`, and proves the seam D contract without bringing in llm/prompt/interview behavior.

## Scope

numbered implementation areas. do each in order.

### 1. make the seam-D protocol explicit

implement the `Selector` callable surface in `src/extractx/core/contracts.py`.

requirements:

- define the protocol method explicitly:
  - `select(field_spec: FieldSpec, candidate_set: CandidateSet, context_pack: ContextPack, instance_state: InstanceState | None = None) -> Selection`
- keep it sync and deterministic for phase 1
- the selector may consume the real `CandidateSet` object; do not invent a new public `CandidateSummary` type in this task
- if the implementation wants a summary projection, derive it internally from `CandidateSet` without widening the public surface
- in architecture §7 seam D, "candidate summaries" describes the selector's internal projection/view for prompting or comparison; phase 1 keeps the parameter type as the full `CandidateSet`

implementation-shape constraints:

- one method only unless the docs already require another
- no async selector protocol in this task
- no `UsageEvent` emission in this task; algorithmic selectors do not emit provider usage

### 2. implement the phase-1 selector policy

implement one deterministic algorithmic selector in `src/extractx/selection/algorithmic/`.

phase-1 policy is fixed:

- if `len(candidate_set.candidates) == 0`:
  - emit `Selection(outcome="NO_CANDIDATES", selected_candidate_ids=())`
- if `len(candidate_set.candidates) == 1`:
  - emit `Selection(outcome="SELECTED", selected_candidate_ids=(sole_id,))`
- if `len(candidate_set.candidates) > 1`:
  - emit `Selection(outcome="AMBIGUOUS", selected_candidate_ids=(all ids in canonical candidate order))`
  - canonical candidate order means `tuple(c.candidate_id for c in candidate_set.candidates)`; do not invent lexical, hash, or alphabetical reordering

requirements:

- this selector is deterministic and algorithmic
- no arbitrary tie-break (`first candidate wins`) is allowed
- no “select all” collapse into `SELECTED`
- no abstention heuristic in phase 1
  - `ABSTAINED` is a real seam-D outcome, but this first selector need not emit it
- `reason` policy for phase 1: `reason=None` for `SELECTED` and `NO_CANDIDATES`; for `AMBIGUOUS`, either `None` or one fixed static label such as `"algorithmic_multi_candidate"` is acceptable. do not emit prose derived from candidate content

implementation-shape constraints:

- do not invent scoring, ranking, confidence, or normalization heuristics
- do not inspect candidate source spans for selection policy
- do not leak seam E cardinality behavior backward into seam D

### 3. enforce the id-only contract at the selector boundary

implement the shared selector-boundary enforcement in `src/extractx/selection/selector.py` and/or adjacent local helpers.

requirements:

- seam D must enforce:
  - `selected_candidate_ids ⊆ input candidate_ids`
  - no fabrication
- this enforcement belongs at the selector boundary, not downstream
- keep the wrapper/harness generic enough that later llm-backed selectors can reuse the same enforcement path

implementation-shape constraints:

- do not implement pydantic-ai integration in this task
- do not implement prompt rendering in this task
- do not add retry or validation loops here

### 4. producer_version for algorithmic selectors

attach `producer_version` to every emitted `Selection` using the existing core helper.

requirements:

- use `algorithmic_producer_version(...)` from `src/extractx/core/versions.py`
- compose its `code_hash` using the same stable pattern already used by the phase-1 regex candidate strategy:
  - stable hash of `"{SelectorClass.__module__}.{SelectorClass.__qualname__}"`
- do not invent a second producer-version scheme

implementation-shape constraints:

- no model id
- no prompt-template hash
- no timestamp or runtime-dependent producer identity

### 5. package wiring

implement the minimal selection package surface so seam D phase 1 is importable and testable.

requirements:

- wire:
  - `src/extractx/selection/__init__.py`
  - `src/extractx/selection/algorithmic/__init__.py`
- `src/extractx/selection/context_pack.py` may hold helper/builders only if genuinely useful, but must not re-declare the canonical `ContextPack` type from `core/objects.py`
- leave `selection/llm/**` and `selection/prompts/**` as stubs unless a tiny edit is needed for package coherence

write-scope note:

- the only supporting edits outside `src/extractx/selection/**` should be the smallest ones required in:
  - `src/extractx/core/contracts.py`
- do not widen top-level `extractx/__init__.py` in this task

### 6. explicit non-goals for this task

leave these out:

- llm-backed selection
- `extras/pydantic_ai/`
- prompt rendering / `Prompt` implementations
- interview capture or `.interview()` rehydration
- `UsageEvent` emission
- seam E `SelectionAdapter` cardinality mapping
- runtime/executor/reporter behavior
- algorithmic abstention heuristics
- selector behavior conditioned on `ContextPack.candidate_overflow`
  - the signal may be present, but this first selector need not use it

typed stubs may remain where needed, but do not invent behavior owned by later or separate threads.

## Guardrails

- **write scope:** `src/extractx/selection/**`, focused tests, and only the smallest supporting edits in:
  - `src/extractx/core/contracts.py`
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly. if so, stop and report with the standard pushback shape.
- **no dependency changes** in this task
- **no behavior from later seams.** do not implement:
  - seam E adaptation
  - llm provider integration
  - prompt templates
  - runtime/executor/replay/interview behavior
- **no fabrication.** if a selector policy wants ids not present in the input set, that is a seam violation, not a downstream cleanup problem
- **no hidden tie-break policy.** multiplicity stays explicit as `AMBIGUOUS` in phase 1
- **no commits or pushes** unless separately asked. leave the branch ready for review.

## Focused proof

add focused tests primarily under `tests/contracts/` and `tests/selection/`.

minimum proof targets to cover:

- `Selector.select(...) -> Selection` exists on the protocol surface
- same `(field_spec, candidate_set, context_pack, instance_state)` yields byte-identical `Selection` across repeated calls
- algorithmic selector emits `producer_version = "code:{code_hash}"` using the core helper
- empty `CandidateSet` -> `NO_CANDIDATES` with empty ids
- singleton `CandidateSet` -> `SELECTED` with the sole id
- multi-candidate `CandidateSet` -> `AMBIGUOUS` with all ids in canonical input order
- emitted `selected_candidate_ids` are always a subset of input ids
- no seam-E/cardinality behavior is smuggled into selection
- `ContextPack` and `InstanceState` can be passed without changing deterministic behavior for this first selector
- `ABSTAINED` is not emitted by this phase-1 selector unless you hit a true contract reason you must surface via pushback

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/selection/selector.py`
- `src/extractx/selection/algorithmic/`

with only minimal supporting edits elsewhere if required by the seam-D surface.

include in your final report:

- exact files changed
- the concrete selector class name you chose
- how you composed `producer_version`
- any remaining ambiguity that should become a coordinator-owned follow-on thread rather than more code

## Success criteria

- `Selector` has an explicit callable surface
- seam D is real for one deterministic algorithmic selector
- the id-only contract is enforced at the selector boundary
- `NO_CANDIDATES`, `SELECTED`, and `AMBIGUOUS` are emitted honestly by phase 1 policy
- `producer_version` for algorithmic selection follows the documented `code:{code_hash}` shape using the core helper
- no llm/prompt/interview/runtime behavior is smuggled into seam D
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pyright`
- top-level repo state remains coherent with the architecture/doc pact

## Downstream consequences

- gives seam E a real `Selection` surface to adapt from
- proves the first honest `DocumentView -> CandidateSet -> Selection` path
- leaves llm-backed selection, prompts, interview capture, and `extras/pydantic_ai` for the later M5 soft-compute thread instead of mixing them into the first selector implementation
- if this task exposes a real contradiction in the current seam-D contract, that becomes a new coordinator-owned thread before more implementation proceeds
