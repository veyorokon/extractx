# ADR-0009: LLM instance proposer for many-cardinality extraction

**Status:** Accepted
**Date:** 2026-04-30
**Extended by:** [ADR-0010](0010-instance-candidate-strategy-seam.md) — bounded `InstanceCandidateSet` construction is a named `InstanceCandidateStrategy` seam, separate from proposal.

## Context

ADR-0008 established the production extraction shape: deterministic candidate grounding, LLM classification over bounded IDs, deterministic validation and sealing, and no domain identity in extractx.

The remaining gap is multi-instance extraction. Prior empirical work repeatedly showed that deterministic instance assignment does not generalize on real multi-instance documents. Treating phase-2 instance assignment as optional future pressure would repeat that failure mode.

## Decision

`Cardinality.MANY` is served by a named `InstanceProposer` seam. Phase 2 lands an LLM-backed implementation, `LLMInstanceProposer`, as scheduled production work.

The schema/spec owns the extraction instance type:

- `ExtractionSpec.instance_type` is the class-like extraction type name.
- `ExtractionSpec.from_pydantic(SchemaCls)` defaults `instance_type` to `SchemaCls.__name__`.
- callers may override `instance_type` when the pydantic class name is not the desired extraction vocabulary.
- the LLM never authors `instance_type`; prompts receive it as bounded schema context.

The instance proposer output remains narrow:

```python
class InstanceProposerResponse(BaseModel):
    selected_instance_ids: tuple[str, ...]
    reason: str | None = None
```

The proposer selects from an input `InstanceCandidateSet`. It does not assign fields to instances, return per-field mappings, create business identities, or emit normalized values. Per-field observation remains downstream through the existing `Observation` contract.

## Contract

The universal soft-call identity for instance proposal is:

```text
sha256(
  document_hash
  + spec_version
  + instance_candidate_set_hash
  + rendered_prompt_hash
  + model_id
  + temperature
  + seed
  + producer_code_hash
)
```

This key identifies the soft-compute decision for replay and forensics. It is not a generic result cache namespace. If a cache is added later, it must remain a separate namespace that may reuse the same identity components but must not become replay authority.

Bounded IDs come from `InstanceCandidateSet`, built deterministically before the LLM call from document-local anchors and schema-owned `instance_type`. The LLM may only return `selected_instance_ids` present in that set.

Validation outcomes:

- selected id outside the candidate set -> conflicting proposer output
- duplicate selected ids -> conflicting proposer output
- empty selected set -> insufficient instance proposal
- malformed structured output -> proposer output error
- provider timeout, rate limit, or auth failure -> infrastructure error

`Cardinality.ONE` remains the no-proposer path. There is no `SingletonInstanceProposer`; single-instance behavior is not an algorithm. Any deterministic singleton fixture helper is testing-only and must be runtime-rejected from production proposer binding paths.

## Consequences

Phase-1 foundation must expose the `InstanceProposer` protocol, `InstanceCandidateSet`, `InstanceProposerResponse`, `ExtractionSpec.instance_type`, `ExtractionSpec.instance_cardinality`, and `ExtractionSpec.instance_proposer_binding`, while failing loudly for unsupported `Cardinality.MANY` execution until phase 2 lands.

Phase 2 implements `LLMInstanceProposer` and proves it on a known multi-instance document. The proof must exercise the real prompt, bounded candidate ids, structured output validation, replay metadata capture, and downstream observation/reconstruction path.

Domain identity remains outside extractx. The selected extraction instance ids are document-local handles; consumers map them to `return_id`, account id, case id, or other business identifiers after extraction.

## Alternatives considered

- **Reuse deterministic instance assignment for phase 2.** Rejected. Prior empirical systems already showed this fails on real multi-instance documents.
- **Let the LLM author instances freely.** Rejected. It would violate ADR-0008's bounded-ID doctrine and make replay/validation weak.
- **Return per-field mappings from the proposer.** Rejected. That smears instance proposal with observation. The proposer selects instances; observation assigns evidence to instances and fields.
- **Add `SingletonInstanceProposer`.** Rejected. `Cardinality.ONE` is a no-op path, not a proposer implementation.

## Related

- [`0008-observation-shaped-llm-extraction.md`](0008-observation-shaped-llm-extraction.md)
- [`../tasks/llm-instance-proposer-phase-2.md`](../tasks/llm-instance-proposer-phase-2.md)
