# ADR-0011: Add an inspectable extraction plan and dry-run surface

**Status:** Accepted
**Date:** 2026-04-30

## Context

extractx now has multiple named seams and two soft-compute decision points on the
production path: field observation and, for `Cardinality.MANY`, instance
proposal. Operators need to inspect what a run will do before spending LLM calls
or writing replay artifacts.

The useful preview is not a shell-command echo. extractx's analogue is a typed
execution plan derived from the same runtime path: what document/spec will be
used, which strategies and soft producers are bound, what deterministic
candidate menus exist, and what replay identities will govern soft calls.

## Decision

Introduce an inspectable `ExtractionPlan` value layer and a dry-run CLI/API
surface.

The plan is derived state, not authority. `Extraction` and `ReplayArtifact`
remain the canonical outputs of a completed run.

Two dry-run levels are accepted:

1. **static plan**: compile schema/spec and runtime policy, but do not adapt the
   document or generate candidates. This catches missing bindings, unsupported
   cardinality, missing runtime capabilities, and planned soft producers.
2. **grounded plan**: adapt the document and run deterministic candidate
   generation, including instance candidate generation, but do not call soft
   producers. This emits field candidate counts, instance candidate menus,
   projected soft-call identities, and replay-relevant hashes.

Both levels must use the same spec compiler, document adapter, candidate
strategies, context builders, and binding validation used by live extraction.
There must not be a benchmark-only or preview-only execution path.

## Contract

`ExtractionPlan` should expose:

- intent: document id/source ref, schema/spec version, strategy
- steps: ordered high-level steps the executor will perform
- bindings: candidate strategies, instance candidate strategy, instance
  proposer, selectors, validators
- capabilities required: LLM, storage, budget, optional extras
- deterministic hashes: document hash, spec hash/version, candidate set hashes,
  instance candidate set hash when grounded
- soft-call identities: prompt id/hash, model id, temperature, seed, producer
  code hash, and the universal identity components for selector/proposer calls
- warnings: candidate overflow, unsupported preview detail, missing optional
  instrumentation

The plan may include human-readable labels, but JSON is the stable inspection
surface. Sensitive runtime data must be redacted.

Dry-run must not:

- call an LLM
- write replay artifacts
- mutate storage
- infer domain identity
- become replay authority

## Consequences

This gives operators a cheap way to inspect:

- whether `Cardinality.MANY` has both required bindings
- which instance candidates the proposer will choose from
- which field candidates a selector will see
- how many soft calls a run is expected to make
- which hashes should appear later in replay artifacts

The grounded dry-run becomes the natural place to debug poor instance candidate
menus before changing LLM prompts.

Usage and cost remain runtime facts. Dry-run may project call counts and model
ids, but actual `UsageEvent` capture belongs to live soft-compute calls.

## Alternatives considered

- **Only print the Python command or CLI args.** Rejected. The important
  extractx decision surface is not the shell command; it is the compiled spec,
  candidate menus, soft producer bindings, and replay identities.
- **Add dry-run only to eval tooling.** Rejected. Eval should consume the same
  plan surface as operators; the plan belongs next to runtime orchestration.
- **Make the plan canonical.** Rejected. Plans are derived previews. Replay and
  extraction artifacts remain authority after execution.

## Related

- [`0001-pass-through-operational-metadata.md`](0001-pass-through-operational-metadata.md)
- [`0007-storage-shape-authority-and-minimum-skeleton.md`](0007-storage-shape-authority-and-minimum-skeleton.md)
- [`0009-llm-instance-proposer-for-many-cardinality.md`](0009-llm-instance-proposer-for-many-cardinality.md)
- [`0010-instance-candidate-strategy-seam.md`](0010-instance-candidate-strategy-seam.md)
- [`../tasks/extraction-plan-dry-run-phase-1.md`](../tasks/extraction-plan-dry-run-phase-1.md)
