# Task: implement seam C phase 1 deterministic candidate generation

*This is seam C phase 1. Make the deterministic candidate-generation seam real with one honest first strategy, not the whole generator catalog at once. The first landed strategy should be explicit regex-based generation driven by declared `StrategyBinding` params, not hidden inference from field descriptions or `ValueKind`.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; seam C summary; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam C, §7 seam F layer 1, §8 soft-compute discipline (to understand what seam C is *not*), §9 canonical objects, §10 three-tier public surface, §14 extensibility map, §16 project layout, and §17 proof table entries for seam C**
- [`docs/adr/0005-candidate-overflow-policy.md`](../adr/0005-candidate-overflow-policy.md) — seam C output is canonical/full; truncation happens later under strategy-owned policy before seam D
- [`docs/adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md`](../adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md) — source-span validity rules and adapter-subcontract discipline
- [`docs/tasks/core-contracts-and-objects.md`](core-contracts-and-objects.md) — prior thread; use the landed core layer instead of reinventing candidate shapes
- [`docs/tasks/seam-a-linearizable-document-adapters.md`](seam-a-linearizable-document-adapters.md) — seam C phase 1 is designed to sit on top of linearizable `DocumentView`s from seam A phase 1

## Goal

implement seam C so a deterministic `CandidateStrategy` can enumerate a canonical `CandidateSet` from a `FieldSpec` and `DocumentView`, with stable candidate ids, valid source spans, and no hidden policy about what patterns to look for.

**"done" in one sentence:** an explicitly-bound regex `CandidateStrategy` can generate deterministic, source-valid `CandidateSet`s from linearizable `DocumentView`s, and seam C’s canonical/full-output contract is enforced in code and tests.

## Scope

numbered implementation areas. do each in order.

### 1. make the seam-C protocol explicit

implement the `CandidateStrategy` callable surface in `src/extractx/core/contracts.py`.

requirements:

- define the protocol method explicitly:
  - `generate(field_spec: FieldSpec, document_view: DocumentView, instance_hint: InstanceHint | None = None) -> CandidateSet`
- keep it sync and deterministic
- do not add selector/runtime/executor concerns here
- `instance_hint` is accepted by the protocol surface even if the first regex strategy does not materially narrow generation by it yet; if unused by the first strategy, pass it through honestly into `CandidateSet.instance_hint`

implementation-shape constraints:

- one method only unless the docs already require another
- no async generator protocol in this task
- no retry, budget, or producer-version concerns here; seam C is deterministic

### 2. land candidate-set helpers owned by seam C

implement the seam-C helper surface in `src/extractx/candidates/candidate_set.py`.

requirements:

- keep `Candidate` / `CandidateSet` as the canonical object shapes from core
- add only the narrowest honest helpers needed for seam C:
  - deterministic `candidate_id` construction per the architecture
  - candidate-set construction/uniqueness checks
  - any local helper for source-span validity checks against `DocumentView.anchor_map`
- fail loudly if candidate ids collide within one `CandidateSet`
- preserve seam C’s rule:
  - **no dedup by normalized value, ever**

implementation-shape constraints:

- do not move canonical object definitions out of `core/objects.py`
- do not add sorting/truncation behavior here
- do not add selector-facing summary shaping here; that belongs later at seam D

### 3. implement phase-1 regex candidate generation

implement `src/extractx/candidates/generators/regex.py` as the first real `CandidateStrategy`.

requirements:

- the regex strategy is **opt-in and explicit**
  - it runs only when `FieldSpec.strategy_binding` names this strategy
  - patterns come from `StrategyBinding.params`, not from `FieldSpec.description`, `ValueKind`, or any hidden defaults
- generate a `CandidateSet` with:
  - deterministic `strategy_id`
  - stable candidate ordering
  - valid `Candidate.source_span`
  - optional `evidence_spans` only when genuinely needed
  - `normalized_structural_payload = None` for the phase-1 regex strategy; future strategies that emit structural matches own their own payload shape
- support linearizable seam-A outputs only in phase 1
  - text / markdown / generic HTML `DocumentView`s with `text_anchor_space="source_bytes"`
- every emitted candidate span must be recoverable through `anchor_map` inversion
- `CandidateSet.instance_hint` must faithfully carry the supplied `instance_hint`

implementation-shape constraints:

- no heuristic pattern inference from prose descriptions
- no NER, no clause segmentation, no table parsing, no hybrid composition
- no value-based dedup
- no hidden context-window or prompt budgeting logic
- if regex matching against HTML normalized text cannot preserve source-byte recoverability honestly, stop and push back rather than smuggling in fuzzy reconstruction logic

### 4. strategy-binding contract for phase 1

make the phase-1 regex strategy’s binding contract explicit in code and tests.

requirements:

- declare a narrow `StrategyBinding.params` shape for the regex strategy sufficient for v1
- patterns must be user-declared and deterministic
- if required params are missing or malformed, fail loudly at the earliest honest seam
  - if this belongs in seam C rather than seam B, that is acceptable for phase 1; document the choice in the final report
- keep the params shape minimal

implementation-shape constraints:

- do not generalize to a plugin registry or dynamic strategy config DSL
- do not add “smart defaults” based on `ValueKind`
- do not add a generic multi-strategy planner in this task

### 5. source-span validity and adapter-subcontract discipline

enforce seam C’s span-validity invariants for the candidates this task emits.

requirements:

- for seam-A phase-1 linearizable adapters, all emitted spans must:
  - carry `text_anchor_space="source_bytes"`
  - be recoverable from `DocumentView.anchor_map` by inversion over one or more normalized-text byte offsets
- regex matches run against `DocumentView.normalized_text`; match spans are translated back to `source_bytes` via `anchor_map` inversion. if the inversion is non-contiguous, preserve evidential distinctness via `evidence_spans` and an honest `source_span` envelope, or stop and push back if that cannot be represented without smear
- all spans emitted by a strategy for a given `DocumentView` must share that `DocumentView`’s `text_anchor_space`
- multibyte UTF-8 cases must remain honest
  - normalized-text offsets used for inversion must stay aligned/in-domain

implementation-shape constraints:

- do not implement seam F layer 1 here
- only add the seam-C-local checks needed to keep the generator honest

### 6. package wiring

implement the minimal candidates package surface so seam C is importable and testable.

requirements:

- wire:
  - `src/extractx/candidates/__init__.py`
  - `src/extractx/candidates/generators/__init__.py`
- keep public/plugin-public imports honest
- leave `ner.py`, `clause.py`, `table.py`, `hybrid.py`, and `grounded/neural.py` as stubs unless a tiny edit is needed for package coherence

write-scope note:

- the only supporting edits outside `src/extractx/candidates/**` should be the smallest ones required in:
  - `src/extractx/core/contracts.py`
- do not widen top-level `extractx/__init__.py` in this task

### 7. explicit non-goals for this task

leave these out:

- seam D selector work
- candidate sorting or truncation policy
- `C.alt` grounded proposal generation
- NER/clause/table/hybrid generators
- any llm-backed logic
- seam E adaptation behavior
- seam F validation behavior beyond seam-C-local honesty checks
- executor/runtime/replay/reporting behavior
- strategy selection when `FieldSpec.strategy_binding is None`
  - architecture already says that absence is meaningful and later interpretation is an executor-policy concern

typed stubs may remain where needed, but do not invent behavior owned by later or separate threads.

## Guardrails

- **write scope:** `src/extractx/candidates/**`, focused tests, and only the smallest supporting edits in:
  - `src/extractx/core/contracts.py`
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly. if so, stop and report with the standard pushback shape.
- **no dependency changes** unless a minimal regex helper dependency is strictly required and cannot be avoided. if that happens, stop and push back before editing `pyproject.toml`.
- **no behavior from later seams.** do not implement:
  - seam D selector behavior
  - seam E cardinality logic
  - seam F runtime validation layers
  - runtime/executor/replay behavior
- **no hidden policy in field descriptions.** `FieldSpec.description` is not a pattern language.
- **no dedup by normalized value.** if two matches have the same text but distinct evidence/spans, both remain.
- **no commits or pushes** unless separately asked. leave the branch ready for review.

## Focused proof

add focused tests primarily under `tests/contracts/` and `tests/candidates/`.

minimum proof targets to cover:

- `CandidateStrategy.generate(...) -> CandidateSet` exists on the protocol surface
- same `(FieldSpec, DocumentView, InstanceHint)` yields the same `CandidateSet`
- `candidate_id` is deterministic for identical candidate content
- candidate ids are unique within one `CandidateSet`
- regex patterns come from explicit binding params; no hidden inference from description or `ValueKind`
- all candidate spans emitted against seam-A phase-1 `DocumentView`s:
  - carry `text_anchor_space="source_bytes"`
  - are recoverable through `anchor_map` inversion
- `CandidateSet.instance_hint` faithfully carries the supplied hint
- repeated equal-text matches are **not** deduplicated when their evidential identity differs
- empty regex match set yields an empty `CandidateSet`, not an exception and not a `NegativeOutcome`; seam C is deterministic and empty enumeration is a valid canonical output. `NO_CANDIDATES` is a seam-D outcome when the selector sees an empty candidate list
- malformed or incomplete regex strategy params fail loudly at the earliest honest seam chosen by the implementation

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/candidates/candidate_set.py`
- `src/extractx/candidates/generators/regex.py`

with only minimal supporting edits elsewhere if required by the seam-C surface.

include in your final report:

- exact files changed
- the phase-1 regex binding params shape you chose
- whether any part of param validation landed at seam C rather than seam B, and why
- any remaining ambiguity that should become a coordinator-owned follow-on thread rather than more code

## Success criteria

- `CandidateStrategy` has an explicit callable surface
- seam C is real for one deterministic, explicit regex strategy
- `CandidateSet` construction is deterministic and canonical/full
- candidate ids are deterministic and unique
- source spans emitted by the regex strategy are honest against linearizable seam-A `DocumentView`s
- no selector/runtime/prompt logic is smuggled into seam C
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pyright`
- top-level repo state remains coherent with the architecture/doc pact

## Downstream consequences

- gives seam D a real canonical `CandidateSet` surface to classify among
- gives seam G real candidate evidence to consume later for grouping/resolution
- leaves richer candidate-generation strategies (`ner`, `clause`, `table`, `hybrid`) and `C.alt` for focused follow-on threads rather than faking a broad first implementation
- if this task exposes a real contradiction in the current seam-C contract, that becomes a new coordinator-owned thread before more implementation proceeds
