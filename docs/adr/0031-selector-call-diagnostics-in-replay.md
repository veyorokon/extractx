# ADR-0031: Persist Selector Call Diagnostics In Replay

**Status:** Accepted
**Date:** 2026-05-14

## Context

Replay artifacts already preserve documents, candidate sets, observations,
validated fields, final instances, and usage events. That is enough to
reconstruct extraction output, but it is not enough to diagnose selector
precision failures when a selector call is budgeted, sharded, batched, or uses
prompt-local bounded ids.

The missing information exists only at extractx-owned seams:

- `IndependentStrategy` knows the execution shape: auto-selection,
  no-candidate decisions, batch indexes, shard indexes, shard counts, reducer
  rounds, prompt budget estimates, and final observations.
- `PydanticAISelector` / `PydanticAIBatchSelector` know the rendered prompt
  identity, prompt-local to canonical candidate-id maps, allowed bounded ids,
  selector response hashes before and after canonical-id translation, usage,
  and model metadata.
- `LocalPromptRecorder` records rendered prompts, but it does not know whether
  the prompt was a shard, reducer pass, batch call, auto-selection, or
  no-candidate decision.

Consumers can project replay into their own diagnostic stores, but they cannot
correctly reconstruct these selector-call facts after the run. Re-running
candidate generation or re-rendering prompts is not equivalent to observing the
selector seam that actually executed.

## Decision

extractx will make selector-call diagnostics canonical replay data.

`ReplayArtifact` schema version `"v3"` adds:

```python
selector_call_diagnostics: tuple[SelectorCallDiagnostic, ...]
```

`SelectorCallDiagnostic` is a structural replay record for one selector seam
decision. It does not embed raw prompt or response bodies. It carries stable
refs / hashes when available and references canonical candidate ids already
present in `ReplayArtifact.candidate_sets`.

The diagnostic contract preserves:

- diagnostic schema version
- selector seam: single selector, batch selector, or deterministic selection
  gate
- decision kind: LLM, auto-selected, no-candidates, or shard reducer
- document id, spec version, field ids, and instance ids
- batch index/count, shard index/count, and reducer round
- candidate count by field
- candidate ids actually presented to the call by field
- allowed evidence ids by field
- prompt-local to canonical candidate-id maps by field
- prompt-local to canonical field-id maps for batch prompts
- rendered prompt hash/ref
- estimated and max prompt chars
- selector response hash/ref before canonical-id translation
- selector response hash/ref after canonical-id translation
- final canonical observations
- usage event and model metadata

Older replay artifacts remain readable. v1 artifacts still translate legacy
`selections` to `observations`; v2 artifacts deserialize with
`selector_call_diagnostics=()`. Current writes use v3.

## Contract Rules

- Replay is the source of truth for selector-call diagnostics. Downstream
  systems may project diagnostics into their own stores, but those projections
  are derived.
- Diagnostics reference candidate ids from `ReplayArtifact.candidate_sets`; they
  do not duplicate full candidate blobs.
- Diagnostics must state the subset of candidate ids actually presented to the
  selector call. This is required for prompt-budgeted batch calls, shards, and
  reducer passes.
- Auto-selection and no-candidate decisions are selector diagnostics even when
  no LLM prompt is rendered.
- Prompt and response bodies are not required in the diagnostic record. If a
  prompt recorder or response store exists, the diagnostic should carry refs and
  hashes so replay tooling can dereference the bodies.
- Prompt-local ids are implementation detail inside selector prompts; replay
  diagnostics must carry the mapping needed to explain how they became
  canonical candidate ids.
- Usage events remain ordered at the extraction level and may also be attached
  to the selector diagnostic that produced them.
- Diagnostics are structural. They are not labels, scores, miss attributions,
  or consumer audit facts.

## Ownership

extractx owns the diagnostic capture because it owns the selector seam and the
replay artifact format. Consumers own projection, classification, UI rendering,
domain miss taxonomy, and gold labels.

This keeps the seam opaque: consumers receive the facts necessary to diagnose a
selector decision without depending on private selector implementation state or
reconstructing the execution plan from prompt text.

## Consequences

Selector replay becomes sufficient for failure forensics. A consumer can answer
"what did the selector see?" for normal calls, auto-selection, no-candidate
decisions, batch calls, shards, and reducer passes from one canonical artifact.

Replay artifacts grow in schema surface but not by duplicating large prompt or
candidate bodies. The heavy objects remain source bytes, candidate sets, and
optional prompt/response blobs behind refs.

The v3 replay schema is a durable contract. Future selector implementations
must either populate the same structural fields or fail loudly if they cannot
provide them.
