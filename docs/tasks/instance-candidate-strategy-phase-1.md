# Task: Instance candidate strategy phase 1

Implement ADR-0010: make bounded instance candidate generation an explicit seam
instead of hidden proposer-helper logic.

## Read first

- `docs/architecture.md`
- `docs/adr/0008-observation-shaped-llm-extraction.md`
- `docs/adr/0009-llm-instance-proposer-for-many-cardinality.md`
- `docs/adr/0010-instance-candidate-strategy-seam.md`
- `src/extractx/instances/proposer.py`
- `src/extractx/candidates/generators/regex.py`
- `src/extractx/execution/strategies/independent.py`

## Goal

`Cardinality.MANY` uses an explicit `InstanceCandidateStrategy` binding to build
the bounded menu consumed by `InstanceProposer`. The current line-based grouping
becomes a baseline strategy implementation; regex/defined-term anchors can be
added behind the same seam without changing proposer or selector contracts.

## Scope

### 1. Core contract

- Add `InstanceCandidateStrategy` protocol.
- Add `InstanceCandidateStrategyBinding`.
- Add `ExtractionSpec.instance_candidate_strategy_binding`.
- Register binding classes in schema summary / replay rehydration paths wherever
  `instance_proposer_binding` is already handled.

### 2. Baseline implementation

- Move current `build_instance_candidate_set(...)` behavior into a named
  baseline strategy class.
- Keep behavior deterministic and source-span-valid.
- Preserve current tests by routing through the new binding.

### 3. Regex / defined-term implementation

- Add a first regex-backed instance candidate strategy.
- Reuse shared helper logic from field regex generation where appropriate:
  param validation, byte anchoring, context window construction, stable hashing.
- Do not make field `RegexCandidateStrategy` return `InstanceCandidateSet`.

### 4. Runtime enforcement

- `Cardinality.ONE`: reject instance candidate strategy bindings as unused.
- `Cardinality.MANY`: require both instance candidate strategy binding and
  instance proposer binding before extraction begins.
- Empty/duplicate/invalid instance candidate sets fail loudly with typed
  contract errors.

## Guardrails

- No domain identity (`return_id`, account id, case id, etc.) in extractx.
- No deterministic production authority for final multi-instance assignment.
- No singleton proposer type.
- No LLM calls in instance candidate strategies.
- No preview-only code path.

## Deliverable

Code, tests, and docs updates.

## Success criteria

- Unit/contract tests prove `Cardinality.MANY` requires both bindings.
- Contract tests prove invalid strategy outputs fail loudly.
- Existing LLM instance proposer tests pass through the new strategy seam.
- Regex instance candidate strategy produces deterministic ids and valid spans.
- Replay artifact still captures `InstanceCandidateSet` and proposer metadata.
- `uv run pytest -q`, ruff, pyright, and `packages/extractx_eval` proof remain
  green.

## Downstream consequences

- ADR-0011 grounded dry-run can expose instance candidate menus cleanly.
- Prompt iteration focuses on proposer behavior only after the candidate menu is
  visible and correct.
