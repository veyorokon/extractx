# Task: LLM selector and instance proposer phase 1

> **Superseded:** do not dispatch this brief. It is superseded by
> [`llm-extractor-phase-1-grounded-classifier-with-observation-output.md`](llm-extractor-phase-1-grounded-classifier-with-observation-output.md)
> and ADR-0008. The superseding decision removes `SingletonInstanceProposer`,
> uses `Cardinality.ONE` for the single-instance no-op path, and lands
> observation-shaped selector output in phase 1.

*This starts the production extraction path: deterministic grounding first, LLM classification over bounded IDs second, deterministic normalization and sealing after. The LLM is a classifier, not a free-form extractor.*

## Read first

- [`README.md`](../../README.md) — product boundary and production extraction doctrine
- [`docs/architecture.md`](../architecture.md) — seam C, seam D, seam G, seam H, seam J, anti-patterns
- [`docs/adr/0002-pydantic-ai-default-selector-and-interview.md`](../adr/0002-pydantic-ai-default-selector-and-interview.md)
- [`docs/adr/0004-narrow-interview-scope-to-field-seams.md`](../adr/0004-narrow-interview-scope-to-field-seams.md)
- [`docs/tasks/eval-cli-phase-1.md`](eval-cli-phase-1.md)
- `src/extractx/selection/selector.py`
- `src/extractx/selection/algorithmic/singleton.py`
- `src/extractx/execution/strategies/independent.py`
- `src/extractx/execution/executor/serial.py`

## Goal

Land the first LLM-backed production-path seam without allowing the model to author values.

Done means extractx has:

1. a per-field LLM selector path that classifies grounded candidates by ID only,
2. an explicit `InstanceProposer` protocol seam with singleton-only phase-1 behavior, and
3. tests/docs that make deterministic instance assignment explicitly testing/single-instance only.

## Production Doctrine

This is not exploratory.

Prior empirical work established the split:

- deterministic field-level evidence finding is production-viable,
- deterministic instance assignment is testing-only for single-instance fixtures,
- production multi-instance extraction goes through LLM-backed instance proposal.

Phase 1 ships the `InstanceProposer` seam now so phase 2 can add `LLMInstanceProposer` without a pipeline refactor. `SingletonInstanceProposer` exists only to preserve today's one-instance behavior for fixtures, CI, and single-instance cases.

## Scope

### 1. InstanceProposer seam

Add protocol/data shapes for instance proposal.

Required shape:

```python
class InstanceCandidate(BaseModel):
    instance_id: str
    anchor_spans: tuple[SourceSpan, ...] = ()

class InstanceCandidateSet(BaseModel):
    candidates: tuple[InstanceCandidate, ...]
    producer_version: str

class InstanceProposer(Protocol):
    def propose(
        self,
        document_view: DocumentView,
        spec: ExtractionSpec,
    ) -> InstanceCandidateSet: ...
```

Add `SingletonInstanceProposer` with behavior identical to today's implicit assumption:

```python
InstanceCandidate(instance_id="inst_0", anchor_spans=())
```

Its class docstring must state:

```text
phase-1 default. covers single-instance only.

NOT a production path for multi-instance documents. Empirically,
deterministic instance assignment failed in extractx-old and
prior systems. Production multi-instance extraction goes through
LLMInstanceProposer (phase 2). This impl is for fixtures, CI baselines,
and single-instance test cases ONLY.
```

Do not wire `instance_id` into `Selection` in phase 1. The proposer seam exists so later observation-shaped selector output can consume bounded instance IDs.

### 2. LLM selector seam D

Implement an LLM-backed selector in `src/extractx/extras/pydantic_ai/selector.py`.

Hard contract:

- input is `FieldSpec + CandidateSet + ContextPack`,
- output is core `Selection`,
- output carries selected candidate IDs only,
- `selected_candidate_ids ⊆ CandidateSet.candidates[*].candidate_id`,
- no value fields in the LLM response model,
- no normalized values in the LLM response model,
- no evidence spans in the LLM response model,
- `reason` is diagnostic only.

Structured output shape:

```python
class SelectorResponse(BaseModel):
    selected_candidate_ids: tuple[str, ...] = ()
    abstain: bool = False
    reason: str | None = None
```

Mapping:

- `abstain=True` and no selected ids -> `Selection(outcome="ABSTAINED")`
- no candidates in input -> `Selection(outcome="NO_CANDIDATES")`
- one or more selected ids -> `Selection(outcome="SELECTED")`
- malformed provider output -> typed selector output failure
- fabricated ids -> `SelectorContractError`

Do not map abstention to `AMBIGUOUS`.

### 3. Prompt isolation

Prompt requirements:

- system message contains task instructions,
- user message contains only bounded candidate summaries and field description,
- document text appears only as bounded `Candidate.text` / `Candidate.context`,
- no raw full document text in the prompt,
- no cross-field context in phase 1,
- prompt asks the model to classify candidates, not extract values.

Prompt must include enough candidate data for classification:

- candidate id,
- candidate text,
- local context,
- entity type if present,
- structured payload keys if present,
- evidence-span count or source-span summary if useful.

Reason text is diagnostic. Add a contract test that `Selection.reason` never becomes `ResolvedFieldProposal.evidence_text`, `source_span`, `evidence_spans`, `raw_value`, or `normalized_value`.

### 4. Determinism and replay

Hard pins:

- default temperature is `0`,
- capture actual model id / snapshot when available,
- capture seed when provided,
- capture rendered prompt hash always,
- capture rendered prompt body according to an explicit local replay policy; if body capture is deferred, document that prompt body contains candidate evidence text and is retention-sensitive,
- replay default uses captured selector response, not a live provider re-call,
- live re-execution is an opt-in comparison/forensics mode, not default replay,
- cache is not replay. If cache lands in this thread, it must use a separate namespace from replay, e.g. `selector_cache`, and must be explicitly derived/optimization data.

Producer version for soft selector:

```text
model_id | prompt_template_hash | selector_code_hash
```

Do not use a vague model alias when the provider reports a concrete snapshot.

### 5. Binding surface

Selector opt-in is per-field, not global.

Use a field/spec binding shape. Do not add a global `mode` knob. Do not widen `extract(...)` in this thread.

Default behavior remains algorithmic singleton selection when no selector binding is present.

### 6. Failure mapping

Use typed failure surfaces:

- malformed structured output -> selector output failure, surfaced with `selector.output_malformed: ...`,
- out-of-set ids -> `SelectorContractError`,
- missing LLM capability on an LLM-bound field -> pre-run `InfrastructureError("selector.missing_llm: ...")`,
- provider auth failure -> pre-run or first-call `InfrastructureError("selector.auth_failed: ...")`,
- timeout/rate-limit -> `InfrastructureError("selector.provider_unavailable: ...")` with retry policy deferred unless already present.

Do not swallow provider failures into empty output.

## Guardrails

- No free-form extraction.
- No LLM-authored values.
- No LLM-authored normalized values.
- No LLM-authored evidence spans.
- No domain identity (`return_id`, account id, case id, etc.).
- No cross-field context.
- No `Selection(instance_id=...)` widening in phase 1.
- No `extract(...)` signature widening.
- No replay/cache namespace smear.
- No live-provider tests in default CI.

## Tests

Required tests:

1. `SingletonInstanceProposer` emits exactly one `inst_0` candidate and carries the testing-only docstring warning.
2. `PydanticAISelector` fake-provider returns a valid candidate id -> core `Selection(outcome="SELECTED")`.
3. fake provider returns `abstain=True` -> core `Selection(outcome="ABSTAINED")`.
4. fake provider returns fabricated id -> `SelectorContractError`.
5. LLM response model has no value / normalized value / evidence span fields.
6. selector reason is never propagated into any `ResolvedFieldProposal` field.
7. default no-selector path remains byte-identical on existing deterministic tests.
8. root and package proof commands pass.

Real-provider eval is opt-in only:

- marked integration or env-gated,
- uses the same selector code path as fake-provider tests,
- reports usage metadata and misses,
- never runs in default CI.

## Phase 2 Is Scheduled

Phase 2 lands `LLMInstanceProposer`.

This is scheduled work with empirical justification, not optional future pressure. Deterministic instance assignment has failed in prior empirical systems. Phase 1 lands the protocol seam now so phase 2 is additive.

Observation-shaped selector output remains a future thread:

```python
class SelectorObservation(BaseModel):
    instance_id: str
    field_id: str
    candidate_id: str | None
    abstain: bool = False
```

Do not introduce this shape in phase 1.

## Success Criteria

1. Existing deterministic path remains default and green.
2. Per-field LLM selector can classify grounded candidates by ID using a fake provider.
3. InstanceProposer seam exists with singleton-only behavior and explicit testing-only warnings.
4. Replay/version docs distinguish captured response authority from live re-execution.
5. README names the production doctrine: LLM-classifier over grounded candidates; deterministic instance assignment is single-instance/testing-only.

## Downstream Consequences

- Consumers can plan against extractx as its only runtime upstream.
- Consumers remain responsible for all domain correlation.
- Phase 2 adds `LLMInstanceProposer` behind an existing seam instead of refactoring the pipeline.
- C.alt `GroundedProposalGenerator` remains later and only lands if deterministic candidates are too brittle even after LLM selector + LLM instance proposal.
