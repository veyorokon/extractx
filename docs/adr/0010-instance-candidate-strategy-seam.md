# ADR-0010: Add an instance candidate strategy seam before instance proposal

**Status:** Accepted
**Date:** 2026-04-30

## Context

ADR-0009 made `LLMInstanceProposer` the production path for `Cardinality.MANY`.
That proposer must choose from bounded document-local instance ids; it must not
author instances freely.

The current implementation builds `InstanceCandidateSet` deterministically inside
the proposer helper path. That is acceptable as a first source of bounded ids,
but it hides a separate responsibility: finding candidate instance anchors. Prior
prior work showed that deterministic instance assignment fails on real
multi-instance documents, but deterministic anchor finding is still useful as the
menu that a classifier selects from.

This is the same lifecycle split used for fields:

- field `CandidateStrategy` finds bounded evidence candidates.
- field observer chooses among bounded candidate ids.
- instance candidate strategy finds bounded instance candidates.
- instance proposer chooses among bounded instance ids.

## Decision

Introduce a named `InstanceCandidateStrategy` seam.

```python
class InstanceCandidateStrategy(Protocol):
    def generate(
        self,
        *,
        spec: ExtractionSpec,
        document_view: DocumentView,
        candidate_sets: tuple[CandidateSet, ...],
    ) -> InstanceCandidateSet: ...
```

`ExtractionSpec` gains an `instance_candidate_strategy_binding` used only when
`instance_cardinality == Cardinality.MANY`.

Cardinality behavior:

- `Cardinality.ONE`: create synthetic `inst_0`; no instance candidate strategy
  and no instance proposer.
- `Cardinality.MANY`: require both `instance_candidate_strategy_binding` and
  `instance_proposer_binding`; fail loudly before extraction if either is absent.

The first implementation should keep the current line-grouping behavior as a
baseline strategy and add a regex/defined-term strategy that reuses the same
mechanics as field regex candidates: explicit params, byte-span anchoring,
context-window construction, deterministic ids, stable strategy hashes, and
source-span validation. It must not reuse the field `RegexCandidateStrategy`
class directly because the contract and return type are different.

## Contract

`InstanceCandidateStrategy` output is bounded input to `InstanceProposer`.

`InstanceCandidate` remains an extraction-level object:

- `instance_id` is document-local.
- `instance_type` is copied from `ExtractionSpec.instance_type`.
- `anchor_candidate_ids` may reference field candidates that helped form the
  anchor menu.
- `anchor_spans` and `context` are forensic/supporting evidence for proposal.

The strategy may use deterministic regexes, headings, table rows, clause blocks,
or field candidates to form options. It must not decide which options are real.
The proposer owns selection. Domain identity still belongs to consumers.

Failure behavior:

- malformed strategy params -> `SpecError`
- no instance candidates under `Cardinality.MANY` -> insufficient instance
  candidate generation
- duplicate instance ids -> contract error
- invalid source spans -> contract error

## Consequences

The current helper `build_instance_candidate_set(...)` becomes an implementation
of the baseline strategy rather than hidden orchestration logic.

The `LLMInstanceProposer` prompt and replay artifact continue to consume
`InstanceCandidateSet`; they do not need to know which strategy produced it.

This creates an inspectable pre-LLM surface: users can dry-run the deterministic
candidate menu before spending model calls.

## Alternatives considered

- **Let `LLMInstanceProposer` generate instances directly.** Rejected. This
  violates the bounded-id doctrine and weakens replay.
- **Reuse field `RegexCandidateStrategy` directly.** Rejected. Field candidates
  and instance candidates are different lifecycle objects with different return
  contracts. Shared helper code is fine; shared public class is not.
- **Keep candidate generation hidden inside proposer helpers.** Rejected. It
  hides a load-bearing seam and makes candidate menus harder to inspect,
  configure, and test.

## Related

- [`0008-observation-shaped-llm-extraction.md`](0008-observation-shaped-llm-extraction.md)
- [`0009-llm-instance-proposer-for-many-cardinality.md`](0009-llm-instance-proposer-for-many-cardinality.md)
- [`../tasks/instance-candidate-strategy-phase-1.md`](../tasks/instance-candidate-strategy-phase-1.md)
