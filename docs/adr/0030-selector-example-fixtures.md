# ADR-0030: Add Selector Example Fixtures

**Status:** Accepted
**Date:** 2026-05-09

## Context

ADR-0020 added deterministic benchmark primitives and ADR-0022 added miss
attribution, but selector precision work still lacks a portable unit of
evaluation. Consumers can inspect replay artifacts to see what happened in an
extraction run, and they can maintain domain audit labels, but neither object is
the right public contract for repeatedly testing seam D selector behavior.

The recurring failure shape is selection precision: the correct evidence is in
the `CandidateSet`, but the selector returns the wrong `Observation` or
abstains. Consumers need a stable way to freeze that one decision problem,
attach an expected answer, and run prompt, model, selector, or schema variants
against it without depending on consumer-specific run tables or replay internals.

## Decision

extractx will add a portable `SelectorExample` fixture contract for seam D:

```text
FieldSpec information + CandidateSet + document context -> Observation
```

A selector example is the selector input plus the expected selector output. It
is an eval fixture, not a replay artifact, optimizer integration, or domain
dataset. extractx owns the fixture shape, JSONL serialization, and generic
scoring semantics; consumers own labels, datasets, domain miss taxonomies, and
any optimization framework that consumes the fixtures.

extractx will also add a selector prompt asset surface so consumers can apply
worked examples and operational instructions without rewriting field
descriptions or embedding large demo payloads in schema code. Prompt assets are
referenced by runtime policy, resolved by a consumer-owned resolver, rendered by
selector implementations, and hashed into rendered prompt identity.

## Surface Contract

The exact Python module and names are implementation-owned, but the public
contract must preserve this information:

```python
class ExpectedObservation(BaseModel):
    selected_candidate_ids: tuple[str, ...]
    abstain: bool
    evidence_id: str | None


class SelectorExample(BaseModel):
    document_id: str
    field_id: str
    field_summary: FieldSummary
    candidate_set: CandidateSet
    document_context: str
    expected: ExpectedObservation
    original_observation: Observation | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelectorScore(BaseModel):
    correct: bool
    abstain_match: bool
    selected_candidate_ids_match: bool
    evidence_id_match: bool
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelectorDemo(BaseModel):
    field_id: str
    document_context: str
    candidate_set: CandidateSet
    expected: ExpectedObservation
    note: str | None = None


class SelectorDemoSet(BaseModel):
    demo_set_id: str
    version: str
    demos: tuple[SelectorDemo, ...]
    source: str
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelectorPromptPolicy(BaseModel):
    instruction_ref: str | None = None
    demo_refs: tuple[str, ...] = ()
```

Phase-1 operations:

```python
def score_selector_observation(
    expected: ExpectedObservation,
    actual: Observation,
) -> SelectorScore: ...


def load_selector_examples_jsonl(path: str | Path) -> tuple[SelectorExample, ...]: ...


def export_selector_examples_jsonl(
    examples: Iterable[SelectorExample],
    path: str | Path,
) -> None: ...
```

Runtime prompt-asset resolution:

```python
class SelectorPromptAssetResolver(Protocol):
    def resolve_demo_set(self, ref: str) -> SelectorDemoSet: ...
    def resolve_instruction(self, ref: str) -> str: ...
```

`Runtime` carries `selector_prompt_assets` and
`selector_prompt_policies: Mapping[str, SelectorPromptPolicy]`. The mapping is
keyed by `field_id`; selector implementations apply the policy for the field
being rendered.

`field_summary` is the round-trip-safe projection of `FieldSpec` already used by
the replay/spec-summary surface. The live `FieldSpec` carries Python type and
callable references, so JSONL fixtures persist field information through
`FieldSummary` and leave live-spec lookup to future runner helpers or consumer
adapters.

`run_selector_example(...)` may be added if it can call the existing selector
surface without inventing a parallel execution path. It is not required for the
fixture contract to be useful; consumers can use the fixture format directly
with their own selector runner or experiment harness.

## Contract Rules

- `expected` is curated comparison truth for this fixture. It is not derived
  from `original_observation`.
- Abstain is first-class. A correct abstain fixture is as valid as a fixture
  with selected candidate ids.
- `candidate_set` is required. A selector example without the candidate menu
  does not represent seam D.
- `document_context` is the bounded text context the selector should consider.
  It is a fixture input, not a canonical source document.
- `original_observation` is optional observed runtime output, useful for
  regression diagnosis and variant comparison. It must not be treated as truth.
- `metadata` is for consumer or experiment annotations such as dataset split,
  labeler, audit id, wrong-candidate class, or domain-specific notes. extractx
  must not require domain-specific metadata keys for generic scoring.
- JSONL serialization must use extractx canonical object serialization for
  `FieldSummary`, `CandidateSet`, and `Observation` so fixtures can be replayed
  across processes without private runtime state.
- `SelectorDemoSet` content is resolved through refs. Inline demos in
  `SelectorBinding.params` are not the long-term contract because they bloat
  schema code and make demo swaps look like schema edits.
- `SelectorPromptPolicy` is runtime prompt policy, not field definition. It
  changes selector behavior but does not redefine the semantic meaning of a
  field.
- Resolved demo and instruction content must contribute to
  `rendered_prompt_hash` and prompt soft-call identity. A run with the same
  `spec.version` and different resolved prompt assets is a different prompt
  instance.
- `spec.version` remains the semantic schema identity. Changing prompt assets
  does not by itself require changing `spec.version` unless a consumer chooses
  to encode refs in schema code.
- Demos must render in extractx's selector prompt format. They are not provider
  chat-history turns with their own structured-output schema, because demo
  candidate ids need not belong to the live target call's bounded enum.
- Consumers should use immutable refs for production runs. Floating aliases are
  acceptable for experiments only if the resolved content hash is recorded with
  the run.

## Relationship To Replay

Replay artifacts and selector examples intentionally remain separate.

Replay artifacts answer:

```text
What happened during this extraction run?
```

Selector examples answer:

```text
Given this frozen selector decision, what should a selector return?
```

A consumer may derive selector examples from replay artifacts plus labels:

```text
ReplayArtifact + consumer label -> SelectorExample
```

extractx may later provide helper functions for extracting unlabeled selector
decision slices from replay artifacts, but label attachment remains
consumer-owned because extractx does not know consumer gold truth.

## Consequences

Selector precision work gets a stable measuring stick that is smaller than a
full extraction run and more portable than replay internals. Manual prompt
iteration, model comparison, regression tests, and external optimizers can all
consume the same fixture format.

Demo and instruction iteration can now target a selector-owned prompt surface
instead of overloading `FieldSpec.description`. Consumers can compare "same
schema, same candidate menus, different demo set" as a well-formed eval
question.

extractx avoids choosing an optimization framework. DSPy, fine-tuning,
hand-written prompt variants, or future tools are consumers of
`SelectorExample`; none becomes the seam contract.

Consumers must still build adapters from their audit or gold-label systems into
`SelectorExample`. That is intentional: domain labels, wrong-candidate
taxonomies, promotion thresholds, and dataset splits are outside extractx's
ownership.

## Implementation phases

- **Phase 1 — Fixture and scorer:** add `SelectorExample`,
  `ExpectedObservation`, `SelectorScore`, JSONL load/export, and exact-match
  scoring. Completion condition: focused tests prove correct selected-id,
  evidence-id, abstain, mismatch scoring, and JSONL round-trip through
  `FieldSummary` + `CandidateSet`.
- **Phase 2 — Prompt asset refs:** add `SelectorDemo`, `SelectorDemoSet`,
  `SelectorPromptPolicy`, `SelectorPromptAssetResolver`, runtime policy wiring,
  and pydantic-ai selector rendering for resolved demos/instructions.
  Completion condition: single-field and batch selector prompt rendering include
  resolved assets and prompt metadata carries asset hashes.
- **Phase 3 — Runner helper, if earned:** add `run_selector_example(...)` only
  if it can delegate to the existing selector path without creating a duplicate
  selector execution path. Completion condition: a selector example can be run
  through an existing selector implementation and scored.
- **Phase 4 — Replay slicing, if earned:** add helper(s) that derive unlabeled
  selector decision slices from replay artifacts. Completion condition: callers
  can attach labels to those slices without depending on replay internals.

## Alternatives considered

- **Use replay artifacts directly as eval fixtures.** Rejected. Replay artifacts
  are runtime transcripts. They contain observed output and operational detail,
  but not curated expected truth, and their shape should be free to evolve with
  runtime forensics.
- **Put selector fixtures entirely in consumers.** Rejected. Consumers own the
  labels, but `FieldSpec`, `CandidateSet`, `Observation`, and seam D scoring are
  extractx contracts. If every consumer invents its own fixture shape, evals can
  drift away from the selector contract they claim to test.
- **Add a DSPy integration.** Rejected for this ADR. Optimizers are downstream
  consumers. The durable extractx contract is the labeled selector decision
  case, not any specific optimization framework.
- **Put demos in `FieldSpec.description` or `field.examples`.** Rejected.
  Field descriptions define what a field means. Selector demos define how a
  selector disambiguates bounded candidates. Collapsing them pollutes the
  semantic schema with operational prompt policy.
- **Put full demo payloads in `SelectorBinding.params`.** Rejected as the
  long-term shape. Binding params are schema-adjacent and become awkward for
  versioned assets. Refs plus a runtime resolver preserve reviewable,
  independently versioned prompt assets.
- **Rerun whole extractions for every selector experiment.** Rejected as the
  only eval surface. Full extraction runs remain necessary for end-to-end proof,
  but selector precision debugging needs a smaller unit that freezes candidate
  menus and expected observations.

## Related

- [ADR-0008: Observation-Shaped LLM Extraction](0008-observation-shaped-llm-extraction.md)
- [ADR-0020: Benchmark Primitives Over Benchmark Product](0020-benchmark-primitives-over-benchmark-product.md)
- [ADR-0022: Attribute Benchmark Misses At Extraction Seams](0022-benchmark-miss-attribution.md)
- [ADR-0024: Readable Bounded-ID Selector Prompts](0024-readable-bounded-id-selector-prompts.md)
