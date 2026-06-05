# Task: LLM extractor phase 1: grounded classifier with observation output

*This supersedes [`llm-selector-and-instance-proposer-phase-1.md`](llm-selector-and-instance-proposer-phase-1.md). ADR-0008 is the authority for the vocabulary and initial cardinality decisions. ADR-0010 later extends the `Cardinality.MANY` binding shape by adding an explicit instance candidate strategy seam.*

## Read first

- [`README.md`](../../README.md)
- [`docs/architecture.md`](../architecture.md)
- [`docs/adr/0002-pydantic-ai-default-selector-and-interview.md`](../adr/0002-pydantic-ai-default-selector-and-interview.md)
- [`docs/adr/0004-narrow-interview-scope-to-field-seams.md`](../adr/0004-narrow-interview-scope-to-field-seams.md)
- [`docs/adr/0007-storage-shape-authority-and-minimum-skeleton.md`](../adr/0007-storage-shape-authority-and-minimum-skeleton.md)
- [`docs/adr/0008-observation-shaped-llm-extraction.md`](../adr/0008-observation-shaped-llm-extraction.md)
- [`docs/tasks/eval-cli-phase-1.md`](eval-cli-phase-1.md)
- `src/extractx/core/outcomes.py`
- `src/extractx/schema/spec.py`
- `src/extractx/observation/observer.py`
- `src/extractx/observation/algorithmic/singleton.py`
- `src/extractx/execution/strategies/independent.py`
- `src/extractx/execution/executor/serial.py`
- `src/extractx/replay/`
- `packages/extractx_eval/`

## Goal

Land the production extraction shape decided in ADR-0008.

Done means extractx has:

1. public vocabulary migrated to `Extraction`, `Instance`, `Evidence`, and `Observation`,
2. LLM observation producing observation-shaped decisions over bounded IDs,
3. cardinality-driven instance behavior with no `SingletonInstanceProposer`,
4. deterministic reconstruction from `Observation` to sealed `Evidence` and typed schema materialization,
5. replay, eval, docs, and public exports updated coherently.

## Production Doctrine

This is not exploratory. Prior empirical systems established the split:

- deterministic candidate grounding is useful,
- LLM-classifier-over-grounded-candidates is the production extraction pattern,
- deterministic instance assignment is testing-only for single-instance documents,
- production multi-instance extraction goes through `LLMInstanceProposer` in phase 2.

Phase 1 ships the production decision shape. Phase 2 adds LLM-backed instance proposal behind the cardinality/proposer seam.

## Scope

### 1. Vocabulary migration

Rename the public lifecycle objects:

- historical `ResolvedFieldProposal` -> `Evidence`
- historical `InstanceResult` -> `Instance`
- historical `ExtractionResult` -> `Extraction`
- historical `InstanceKey` -> fold into `Instance.instance_id`
- historical `Selection` typed return -> replaced by `Observation` tuples
- `Candidate` stays `Candidate`

Keep lifecycle responsibility explicit:

- `Candidate` is pre-observation evidence option.
- `Observation` is model decision over bounded IDs.
- `Evidence` is post-validation sealed fact.

Update:

- core types,
- imports and public exports,
- replay serializers/deserializers,
- eval scoring,
- tests,
- docs/architecture.md,
- README and task references.

### 2. Cardinality-driven instance behavior

Use the existing `Cardinality` enum.

Add:

```python
class ExtractionSpec(BaseModel):
    instance_cardinality: Cardinality = Cardinality.ONE
    instance_candidate_strategy_binding: InstanceCandidateStrategyBinding | None = None  # ADR-0010
    instance_proposer_binding: ProposerBinding | None = None
```

Rules:

- `Cardinality.ONE`: create one synthetic instance with `instance_id="inst_0"`; no proposer object.
- `Cardinality.MANY`: require `instance_proposer_binding`; ADR-0010 extends this to require `instance_candidate_strategy_binding` too.
- phase 1 raises `SpecError` for `MANY` without required bindings.
- no `SingletonInstanceProposer` class.
- `InstanceProposer` protocol exists only for `MANY` implementations.

### 3. Observation-shaped observer output

Add the observer decision shape:

```python
class Observation(BaseModel):
    instance_id: str
    field_id: str
    evidence_id: str | None
    abstain: bool = False
    reason: str | None = None
```

During phase 1, `evidence_id` references a selected candidate before deterministic sealing promotes that candidate to `Evidence`. The external contract remains that the LLM chooses IDs, not values.

Hard requirements:

- provider schema constrains `instance_id`, `field_id`, and `evidence_id` to bounded enums where supported,
- post-validation enforces all IDs are in the bounded input sets,
- `abstain=True` requires `evidence_id is None`,
- reason is diagnostic only,
- no response field may carry raw value, normalized value, source span, evidence span, or domain identity.

### 4. LLM observer

Implement an LLM-backed observer in `src/extractx/extras/pydantic_ai/observer.py`.

Contract:

- field/spec binding opt-in, not a global mode,
- default path remains algorithmic when no LLM binding is present,
- no `extract(...)` signature widening,
- no cross-field context in phase 1,
- system prompt contains instructions,
- user prompt contains field description and bounded candidate summaries only,
- document text appears only through bounded candidate text/context,
- structured output via provider/tool schema, not prose parsing,
- temperature defaults to `0`,
- capture concrete model id/snapshot when available,
- capture seed when supplied,
- capture rendered prompt hash and retention-governed prompt body,
- fake-provider tests run in CI,
- real-provider eval is opt-in only.

Failure mapping:

- malformed structured output -> observer output failure,
- fabricated ids -> `ObserverContractError`,
- missing LLM capability -> `InfrastructureError("observer.missing_llm: ...")`,
- auth failure -> `InfrastructureError("observer.auth_failed: ...")`,
- timeout/rate-limit -> `InfrastructureError("observer.provider_unavailable: ...")`.

Do not turn provider failures into empty output.

### 5. Replay and storage

Bump replay artifact format for the vocabulary and observation-shaped observer decision.

Replay rules:

- replay default uses the captured observer response and does not re-call the provider,
- live provider re-execution is opt-in for comparison/forensics only,
- cache is not replay and must use a separate namespace if added later,
- captured artifacts include observer decision, producer metadata, rendered prompt hash/body per policy, model snapshot, and seed when present.

### 6. Deterministic reconstruction

Reconstruction is deterministic:

```text
Candidate -> Observation -> Evidence -> Instance -> Extraction
```

The LLM chooses a candidate/evidence ID. Normalization, validation, and sealing produce `Evidence.normalized_value`. Typed pydantic objects are derived projections over `Instance.evidence`.

## Guardrails

- No LLM-authored values.
- No LLM-authored normalized values.
- No LLM-authored evidence spans.
- No domain identity in extractx.
- No cross-field context in phase 1.
- No `SingletonInstanceProposer`.
- No global LLM mode knob.
- No replay/cache namespace smear.
- No live-provider tests in default CI.
- No hidden compatibility shims for the old public type names unless explicitly documented as temporary migration aliases.

## Tests

Required proof:

1. vocabulary exports expose `Extraction`, `Instance`, `Evidence`, and `Observation`.
2. old names are either removed or explicitly temporary aliases with deprecation tests.
3. `Cardinality.ONE` creates exactly one `inst_0` instance without invoking an `InstanceProposer`.
4. `Cardinality.MANY` without `instance_proposer_binding` raises `SpecError`.
5. fake LLM observer emits a valid `Observation` and deterministic sealing produces `Evidence`.
6. fake LLM observer abstains with `evidence_id=None`.
7. fabricated `instance_id`, `field_id`, or `evidence_id` raises `ObserverContractError`.
8. LLM response schema has no value, normalized value, source span, evidence span, or domain identity fields.
9. observer `reason` never appears in `Evidence.raw_value`, `Evidence.normalized_value`, source spans, or downstream materialized objects.
10. replay round-trip and replay re-execution pass under the new artifact format.
11. `extractx_eval` scorer and CLI consume `Extraction.instances`.
12. existing deterministic extraction tests remain green through the renamed canonical path.

Proof commands:

- `uv run pytest`
- `uv run ruff check`
- `uv run pyright`
- package-level `extractx_eval` tests and CLI smoke.

## Phase 2

Phase 2 lands `LLMInstanceProposer` for `Cardinality.MANY`.

That work is scheduled and empirically justified. It is not "wait for pressure"; the pressure has already been observed in prior systems.

## Success Criteria

1. ADR-0008 vocabulary is true in code, docs, replay, eval, and public exports.
2. LLM observation is classifier-only over bounded IDs.
3. `Cardinality.ONE` preserves today's single-instance behavior without a singleton proposer class.
4. `Cardinality.MANY` fails loudly until required many-cardinality bindings exist.
5. Consumers can consume `Extraction` as instances plus evidence plus observations without domain identity leaking into extractx.
