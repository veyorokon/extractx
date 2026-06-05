# Task: Extraction plan and dry-run phase 1

Implement ADR-0011: add an inspectable plan surface for extraction runs.

## Read first

- `docs/architecture.md`
- `docs/adr/0001-pass-through-operational-metadata.md`
- `docs/adr/0007-storage-shape-authority-and-minimum-skeleton.md`
- `docs/adr/0009-llm-instance-proposer-for-many-cardinality.md`
- `docs/adr/0010-instance-candidate-strategy-seam.md`
- `docs/adr/0011-inspectable-extraction-plan-and-dry-run.md`
- `src/extractx/api.py`
- `src/extractx/execution/executor/serial.py`
- `src/extractx/execution/strategies/independent.py`
- `src/extractx/cli/run.py`
- `src/extractx/cli/inspect.py`

## Goal

Operators can inspect what extractx will do without calling soft producers or
mutating storage. Static dry-run validates bindings and planned producers;
grounded dry-run also adapts the document and emits deterministic candidate
menus and soft-call identity inputs.

## Scope

### 1. Plan value layer

- Add `ExtractionPlan` and `ExtractionPlanStep` typed objects.
- Include intent, bindings, required capabilities, ordered steps, hashes,
  warnings, and projected soft-call identities.
- Provide JSON serialization with sensitive values redacted.

### 2. Static dry-run

- Compile schema/spec and runtime policy.
- Validate unsupported cardinality and missing bindings.
- Do not adapt document or generate candidates.
- Expose the planned candidate strategies, instance candidate strategy,
  instance proposer, selectors, and required capabilities.

### 3. Grounded dry-run

- Adapt the document and run deterministic candidate generation.
- Build field `CandidateSet`s and, for `Cardinality.MANY`, the
  `InstanceCandidateSet`.
- Do not call selector/proposer LLMs.
- Emit candidate counts, bounded ids, context snippets, hashes, and projected
  soft-call identity components.

### 4. CLI/API surface

- Add a small dry-run JSON surface under the existing CLI.
- Keep public API narrow; prefer one explicit function over a mode flag smeared
  through `extract(...)`.
- Eval tooling may consume this surface later, but must not get a private
  benchmark-only planner.

## Guardrails

- Dry-run never calls an LLM.
- Dry-run never writes replay artifacts or mutates stores.
- Plan is derived inspection data, not canonical authority.
- Use the real runtime/spec/candidate path; no duplicate preview implementation.
- No domain identity in plan output.
- No pricing logic in core. Usage and cost are live-run facts.

## Deliverable

Code, tests, CLI docs, and architecture updates.

## Success criteria

- Static dry-run catches missing `Cardinality.MANY` bindings.
- Grounded dry-run emits the same candidate ids as a live run would use.
- A contract test proves dry-run does not call selector/proposer LLMs.
- A contract test proves dry-run does not write replay artifacts.
- JSON output is stable enough for tooling snapshots.
- `uv run pytest -q`, ruff, pyright, and `packages/extractx_eval` proof remain
  green.

## Downstream consequences

- Eval can report candidate-menu defects separately from selector/proposer
  mistakes.
- Replay debugging has a pre-run counterpart for comparing intended vs captured
  producer identities.
