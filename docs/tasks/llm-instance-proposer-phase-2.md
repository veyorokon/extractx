# Task: LLM instance proposer phase 2

*ADR-0009 is the authority for this thread. ADR-0008 remains the authority for observation-shaped extraction and vocabulary.*

## Read first

- [`README.md`](../../README.md)
- [`docs/architecture.md`](../architecture.md)
- [`docs/adr/0008-observation-shaped-llm-extraction.md`](../adr/0008-observation-shaped-llm-extraction.md)
- [`docs/adr/0009-llm-instance-proposer-for-many-cardinality.md`](../adr/0009-llm-instance-proposer-for-many-cardinality.md)
- [`docs/tasks/llm-extractor-phase-1-grounded-classifier-with-observation-output.md`](llm-extractor-phase-1-grounded-classifier-with-observation-output.md)
- `src/extractx/core/objects.py`
- `src/extractx/core/contracts.py`
- `src/extractx/schema/from_pydantic.py`
- `src/extractx/execution/executor/serial.py`
- `src/extractx/extras/pydantic_ai/`
- `packages/extractx_eval/`

## Goal

Land production multi-instance proposal for `Cardinality.MANY`.

Done means a spec with `instance_cardinality=Cardinality.MANY` and an LLM proposer binding can run against one already-scoped document, select extraction instance ids from a bounded `InstanceCandidateSet`, and continue through observation, deterministic sealing, replay capture, and eval.

## Scope

### 1. Instance candidate set construction

Build the deterministic bounded candidate set that feeds the LLM.

Rules:

- candidate ids are extractx-authored, deterministic, and document-local,
- `instance_type` comes from `ExtractionSpec.instance_type`,
- `from_pydantic(SchemaCls)` defaults `instance_type` to `SchemaCls.__name__`,
- callers may override `instance_type`,
- the LLM never authors `instance_id` or `instance_type`,
- candidates carry enough anchor/context text for selection but no domain identity.

### 2. LLMInstanceProposer

Implement an LLM-backed `InstanceProposer` for `Cardinality.MANY`.

Return type stays narrow:

```python
class InstanceProposerResponse(BaseModel):
    selected_instance_ids: tuple[str, ...]
    reason: str | None = None
```

The proposer selects instances only. It must not return `dict[instance_id, field_id]`, per-field assignments, normalized values, source spans, or business identifiers.

### 3. Prompt and structured output

Use structured output, not prose parsing.

Prompt requirements:

- system prompt owns instructions,
- user prompt contains `instance_type`, field/schema summary, and bounded instance candidates,
- candidate ids are constrained where provider support allows,
- temperature defaults to `0`,
- concrete model snapshot, seed, rendered prompt hash, and producer code hash are captured.

Do not use seeding conversations or demonstration turns in phase 2. Keep the prompt single-call and replayable.

### 4. Validation and failure mapping

At the proposer seam:

- selected id outside the candidate set -> conflicting proposer output,
- duplicate selected ids -> conflicting proposer output,
- empty selected set -> insufficient instance proposal,
- malformed structured output -> proposer output error,
- timeout/rate-limit/auth failure -> infrastructure error.

Do not turn provider failures into empty output.

### 5. Replay and soft-call identity

Every instance-proposer LLM call captures the universal soft-call identity:

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

Replay is authority. Cache, if added later, is an optimization in a separate namespace.

### 6. Executor integration

Remove the phase-1 fail-loudly gate for supported `Cardinality.MANY` specs with a proposer binding.

Keep loud failures for:

- `Cardinality.MANY` without a binding,
- missing runtime/provider capability,
- unsupported proposer binding kind,
- any deterministic singleton fixture helper selected in a production path.

## Guardrails

- No domain identity in extractx.
- No deterministic instance assignment as production path.
- No `SingletonInstanceProposer`.
- No LLM-authored ids, values, normalized values, or spans.
- No per-field mapping from the proposer.
- No cross-document correlation.
- No replay/cache namespace smear.
- No live-provider tests in default CI.

## Tests

Required proof:

1. `ExtractionSpec.from_pydantic(MySchema)` defaults `instance_type == "MySchema"`.
2. caller override of `instance_type` changes spec version.
3. `Cardinality.MANY` without binding raises `SpecError`.
4. `Cardinality.MANY` with `LLMInstanceProposer` runs through fake provider in CI.
5. proposer output with out-of-set id fails loudly.
6. duplicate selected ids fail loudly.
7. empty selected ids produces the documented insufficient outcome.
8. proposer response schema has no fields beyond `selected_instance_ids` and diagnostic `reason`.
9. replay captures the proposer decision and soft-call identity components.
10. a known multi-instance fixture exercises end-to-end extraction through observation and deterministic sealing.

Proof commands:

- `uv run pytest`
- `uv run ruff check`
- `uv run pyright`
- package-level `extractx_eval` tests and CLI smoke.

## Success criteria

1. `Cardinality.MANY` has a production LLM-backed proposer path.
2. The proposer seam is bounded-ID, replayable, and validation-owned.
3. Observation remains the only field/evidence assignment surface.
4. Consumers can consume multi-instance `Extraction` output without extractx owning domain identity.
