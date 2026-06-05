# ADR-0008: Adopt observation-shaped LLM extraction vocabulary

**Status:** Accepted
**Date:** 2026-04-30
**Extended by:** [ADR-0010](0010-instance-candidate-strategy-seam.md) — `Cardinality.MANY` now requires a separate instance candidate strategy binding plus an instance proposer binding.

## Context

extractx is pre-1.0 and has one active runtime consumer adopting extractx as its upstream grounded extraction dependency. Prior empirical work showed the same boundary repeatedly: deterministic field-level evidence finding is useful, but deterministic instance assignment does not hold on real multi-instance documents. The production path is therefore grounded candidate generation, LLM classification over bounded IDs, and deterministic validation/sealing after selection.

The current public vocabulary also carries academic names that obscure lifecycle responsibility. `Candidate` is clear for pre-observation options, but historical `ResolvedFieldProposal`, `InstanceResult`, `ExtractionResult`, and `InstanceKey` make the consumer contract harder to read than it needs to be.

## Decision

Adopt an observation-shaped LLM extraction contract and refresh the public vocabulary in the next implementation thread.

Lifecycle names:

- `Candidate` remains the pre-observation option found by deterministic grounding.
- `Observation` becomes the LLM decision tuple: `(instance_id, field_id, evidence_id | None, abstain, reason)`.
- `Evidence` replaces historical `ResolvedFieldProposal` as the post-validation sealed fact that carries `normalized_value`.
- `Instance` replaces historical `InstanceResult`.
- `Extraction` replaces historical `ExtractionResult`.
- historical `InstanceKey` folds into `Instance.instance_id`.

The pre/post lifecycle distinction remains load-bearing: a `Candidate` is an option the system may choose; `Evidence` is a sealed fact after validation. The LLM never authors raw values, normalized values, source spans, evidence spans, or domain identity. It only chooses bounded IDs or abstains.

Instance behavior is driven by cardinality, not by a degenerate singleton proposer:

- `ExtractionSpec.instance_cardinality: Cardinality` is the spec-level switch.
- `Cardinality.ONE` creates one synthetic extraction instance without an `InstanceProposer`.
- `Cardinality.MANY` requires `ExtractionSpec.instance_proposer_binding`; ADR-0010 later splits the bounded-menu source into `ExtractionSpec.instance_candidate_strategy_binding`.
- phase 1 raises `SpecError` if `MANY` is requested without a binding.
- no `SingletonInstanceProposer` type is introduced.
- `InstanceProposer` exists only for `MANY` implementations.

Phase 1 lands observation-shaped LLM output and the cardinality/proposer seam, but not LLM-backed instance assignment. `LLMInstanceProposer` is scheduled phase 2 work with empirical justification, not optional future pressure.

## Consequences

This is a deliberate one-time migration across core, eval tooling, replay artifacts, public exports, and docs. The migration cost is acceptable because extractx is pre-1.0, the active consumer is known, and the new vocabulary makes the durable contract easier to operate.

Replay artifact format must bump. Existing internal replay fixtures are migrated or regenerated as part of the implementation thread; replay and cache remain separate namespaces.

Consumer ingestion becomes simpler because extractx's `Observation` vocabulary aligns with common observation-row concepts. Domain identity still stays outside extractx: `return_id`, account ids, case ids, and similar business identifiers remain consumer-owned.

The current code and architecture doc still use the pre-migration names until the follow-up implementation task lands. That task must update `docs/architecture.md`, public exports, replay, eval scoring, and all tests in one coherent migration.

## Alternatives considered

- **Keep the old names until after LLM observation phase 1.** Rejected. It would ship another narrow shape and force the same migration after more code depends on it.
- **Rename `Candidate` to `Evidence` directly.** Rejected. It collapses the pre-observation option and post-validation sealed fact lifecycle into one name.
- **Introduce `SingletonInstanceProposer`.** Rejected. Single-instance behavior is a no-op implied by `Cardinality.ONE`, not an algorithm. A class for that case creates type noise and makes the testing-only path look more production-like than it is.
- **Defer `Observation` output to phase 2.** Rejected. The observation tuple is the production LLM decision shape. Shipping `Selection` first would recreate the prior deterministic-first trap.

## Related

- [`0002-pydantic-ai-default-selector-and-interview.md`](0002-pydantic-ai-default-selector-and-interview.md)
- [`0004-narrow-interview-scope-to-field-seams.md`](0004-narrow-interview-scope-to-field-seams.md)
- [`0007-storage-shape-authority-and-minimum-skeleton.md`](0007-storage-shape-authority-and-minimum-skeleton.md)
- [`../tasks/llm-extractor-phase-1-grounded-classifier-with-observation-output.md`](../tasks/llm-extractor-phase-1-grounded-classifier-with-observation-output.md)
