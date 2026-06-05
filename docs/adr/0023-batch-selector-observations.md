# ADR-0023: Batch Selector Observations

**Status:** Accepted
**Date:** 2026-05-05

## Context

ADR-0008 made the selector seam observation-shaped: soft compute chooses
bounded candidate ids and never authors values, spans, evidence, or instance
ids. The current runnable strategy applies that contract one field at a time:
for each `FieldSpec`, extractx builds one `CandidateSet`, renders one prompt,
and asks one selector for one `Observation`.

That shape is correct but expensive for schemas with many fields. A
single-instance, ten-field schema pays ten LLM round trips even though all
candidate sets are already known before selection begins. Consumers now have
benchmarks showing that round-trip count, not candidate generation or
validation, is the dominant cost for high-volume extraction.

The tempting alternative is a one-shot value extractor that returns
`{value, evidence_text}` for all fields. That is rejected for this thread
because it moves soft compute from classification into value authorship and
weakens the core grounding guarantee.

## Decision

extractx will add a batch selector seam that returns canonical
`Observation` objects for multiple fields in one soft-compute call.

The batch selector is a sibling of the existing single-field `Selector`, not a
replacement for downstream seams. Candidate generation, candidate filtering,
cardinality adaptation, field validation, object validation, resolution,
replay, and benchmark scoring remain unchanged.

## Contract

```python
class BatchSelector(Protocol):
    def select_many(
        self,
        *,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
        context_pack: ContextPack,
        instance_state: InstanceState | None = None,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> tuple[Observation, ...]: ...
```

The returned observations must satisfy the same id-only contract as the
single-field selector:

- every `Observation.field_id` must match one input `CandidateSet.field_id`;
- every selected candidate id must belong to that field's candidate set;
- `Observation` must not carry raw values, normalized values, source text, or
  spans;
- `NO_CANDIDATES` may only be emitted for empty candidate sets;
- `ABSTAINED` carries no selected ids;
- `AMBIGUOUS` carries at least one selected id.

The batch helper additionally enforces:

- no duplicate `(field_id, instance_id)` observations;
- every non-auto-selected input candidate set receives one observation;
- output order is canonicalized to input candidate-set order.

## Strategy Behavior

`ExecutorPolicy.strategy="batch"` runs the same candidate-generation stage as
`"independent"`, then performs one batch-selection call for the unresolved
field candidate sets. Downstream adaptation, validation, and resolution reuse
the existing independent path.

Phase 1 batch mode is intentionally strict: fields that need soft selection
must be bound to a batch-capable selector. extractx does not silently fall back
to per-field LLM calls inside a run declared as batch. If a field has no
candidate set, or the deterministic selection gate can safely choose, the
strategy may emit the same observation it would emit in the independent path
without entering the batch call.

`PydanticAIBatchSelector` is intended to be the drop-in batch sibling of
`PydanticAISelector`: same `model_id`, provider, temperature, and seed
configuration shape; different prompt and DTO because the output covers many
fields. A schema adopting batch mode should flip all soft-selected fields to a
batch-capable selector together. Mixed legacy/batch selector bindings fail
loudly in phase 1 rather than hiding per-field fallbacks inside a batch run.

Future phases may add explicit fallback policy, for example "batch first, then
per-field repair for omitted or malformed fields." That must be a named
strategy/policy, not implicit behavior in `strategy="batch"`.

## Pydantic-AI Extra

`PydanticAIBatchSelector` is the default batch-capable LLM implementation. It
renders all requested fields and their bounded candidate ids into one prompt,
expects a structured list of field decisions, then maps those DTOs into
canonical `Observation` objects.

The provider may emit one usage event for the whole batch. Per-field cost
allocation is derived reporting and is not part of the seam-D contract.

## Replay and Cost Shape

Replay remains observation-shaped. A batch call returns `N` canonical
`Observation` rows and the replay artifact stores those rows in the existing
`ReplayArtifact.observations` tuple. Replay readers and `score_replay(...)`
continue to consume observations by field id; they do not need to understand a
new batch transcript object for phase 1.

The operational cost unit changes from "one selector usage event per field" to
"one selector usage event per batch call." Consumers that allocate cost per
field must treat per-field allocation as derived reporting over a batch-level
usage event, not as canonical usage truth.

If a batch provider returns malformed structured output, the strategy raises a
selector contract/infrastructure failure for the batch call. Phase 1 does not
synthesize one failed observation per field because that would make selector
implementation failure look like data-level abstention. A later repair strategy
may translate batch failure into per-field retry work, but that is outside this
ADR.

## Rejected Alternatives

### One-shot `{value, evidence_text}` extraction

Rejected for this thread. It makes the model author values and evidence text,
then asks deterministic code to recover spans after the fact. That is an
alternate grounded proposal generator seam, not an observation-compatible
selector. It may be benchmarked later, but it must not replace the selector
contract.

### Automatic fallback to per-field selectors

Rejected for batch strategy. A run declared as batch should either perform
batch selection or fail loudly. Silent fallback would make cost and replay
shape depend on hidden capability checks.

### A batch selector that returns typed values

Rejected. Values are still owned by seam F validation over selected
candidate evidence. Batch selection only chooses candidate ids.

## Consequences

- Consumers can reduce LLM round trips without weakening grounding.
- Replay remains observation-compatible.
- Benchmark attribution continues to work without a separate scoring path.
- Schemas must opt into batch-capable selectors for fields that need soft
  selection.
- The initial motivation came from a consumer bulk extraction benchmark,
  where per-field selector round trips dominated wall time. Those benchmarks
  are consumer evidence, not part of extractx's contract.
