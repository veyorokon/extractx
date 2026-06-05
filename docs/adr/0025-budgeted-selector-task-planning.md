# ADR-0025: Budgeted Selector Task Planning

**Status:** Accepted
**Date:** 2026-05-06

## Context

ADR-0023 added batch selector observations so one soft-compute call can choose
bounded candidate ids for many fields. ADR-0024 made that prompt readable,
prompt-local, and inspectable. Consumer benchmarks now show a separate failure
mode: very large documents can produce batch selector prompts large enough that
the provider times out or returns infrastructure errors after many minutes.

This is not a candidate-generation failure and not a reason to weaken the
bounded-candidate contract. It is a planning failure before seam D: extractx
currently treats batch selection as all-or-nothing instead of budgeting the
selector work into prompt-sized calls.

LangExtract handles long documents by chunking source text before extraction and
aligning model-authored text back to the source. extractx should not copy that
shape directly because the selector seam intentionally does not let the model
author values or evidence text. The extractx-native analog is to chunk selector
work over already-grounded candidate sets.

## Decision

extractx will add a selector task planner that budgets and packs selector work
before calling soft compute.

The planner operates after candidate generation and filtering, and before seam D
selection. It builds selection tasks from `(FieldSpec, CandidateSet)`, estimates
each task's rendered prompt size through an injected prompt estimator, packs
tasks into one or more batch selector calls under a configured prompt budget,
shards oversized single-field candidate sets when needed, and leaves selector
execution plus canonical `Observation` merging to the strategy.

## Contract

The task planner is presentation and orchestration policy only. It must not
change canonical candidate generation, filtering, validation, evidence
materialization, replay, or scoring semantics.

Required invariants:

- `CandidateSet` remains canonical and unchanged;
- prompt compaction and task packing are derived read models over candidate
  sets;
- prompt estimates must be exact rendered prompt character counts, or a proven
  upper bound over the renderer. An under-estimating prompt estimator violates
  the planner contract because it can admit provider calls that later exceed the
  configured budget;
- packing is order-preserving. Smarter bin-packing, sorting by prompt size, or
  semantic grouping are separate decisions because they change reproducibility
  and diagnostic interpretation;
- every selected id returned by any selector call must still belong to the
  selected field's canonical candidate set;
- multiple selector calls for one extraction produce the same canonical
  observation tuple shape consumed by downstream seams;
- usage events are per selector call, while any per-field allocation remains
  derived reporting;
- prompt-budget failures happen before provider calls and identify the first
  oversized task or individual candidate.
- planner outputs carry the diagnostic data used for logs; execution code must
  not rederive planner facts such as estimated prompt size, shard count, or
  original oversized-task size.

A prompt-budget failure should be diagnostic, not a provider timeout. The error
shape should include at least:

```text
selector_prompt_budget_exceeded:
  field_id: invoice_date
  candidate_count: 742
  estimated_prompt_chars: 510000
  max_prompt_chars: 120000
```

The exact exception/status type is implementation-owned, but the failure must
name the selector-planning seam and carry enough structured data to decide
whether to shrink contexts, add filtering, or introduce candidate sharding.

## Planner Object

The planner is a named internal seam, not a selector wrapper. It should be
factored as a pure planning component once the in-strategy implementation is
stable.

The planner owns packing, sharding, and prompt-budget diagnostics. It does not
own selector execution, usage-event recording, validation, replay, or
observation adaptation.

Sketch:

```python
@dataclass(frozen=True, slots=True)
class SelectorTask:
    field_spec: FieldSpec
    candidate_set: CandidateSet


@dataclass(frozen=True, slots=True)
class BatchSelectorCallPlan:
    tasks: tuple[SelectorTask, ...]
    estimated_prompt_chars: int


@dataclass(frozen=True, slots=True)
class ShardedSelectorTaskPlan:
    task: SelectorTask
    shards: tuple[BatchSelectorCallPlan, ...]
    original_estimated_prompt_chars: int


SelectorPlan = BatchSelectorCallPlan | ShardedSelectorTaskPlan

PromptEstimator = Callable[[tuple[SelectorTask, ...]], int]


class BudgetedBatchSelectorPlanner:
    def __init__(self, *, max_prompt_chars: int) -> None:
        self.max_prompt_chars = max_prompt_chars

    def plan(
        self,
        *,
        tasks: tuple[SelectorTask, ...],
        estimate_prompt_chars: PromptEstimator,
    ) -> tuple[SelectorPlan, ...]:
        ...
```

The planner takes a prompt estimator instead of a `BatchSelector`. The estimator
is the only selector-specific fact needed for planning. `IndependentStrategy`
may build that estimator as a closure over `spec`, `document_view`, and the
configured selector's `render_prompt(...)`, but those details should stay
outside the planner.

`SelectorTask` intentionally does not carry `instance_id`. Instance assignment
and `inst_0` defaults belong at the strategy/observation contract boundary, not
inside prompt planning.

`estimated_prompt_chars` is an `int`, not `int | None`. Unbounded mode should
either skip this planner and use one normal batch call, or be represented by a
separate unbounded plan type. Optional estimates would smear "not measured" and
"unbounded" into one field.

`ShardedObservationReducer` should not be extracted yet. The current reducer
logic has one consumer and is still part of selector execution. It should become
a separate contract only when confidence-weighted shard winners, semantic dedupe
for `MANY`, or another independently testable reducer policy appears.

## Diagnostics and Logging

Planner outputs are the source of truth for selector-planning diagnostics.
Execution code logs those facts; it must not recompute or infer them from local
loop state.

Required log-facing fields for a normal batch plan:

- `field_count`
- `candidate_count`
- `estimated_prompt_chars`
- `max_prompt_chars`

Required log-facing fields for a sharded field plan:

- `field_id`
- `original_estimated_prompt_chars`
- `shard_index`
- `shard_count`
- `candidate_count`
- `estimated_prompt_chars`
- `max_prompt_chars`

Prompt-budget exceptions should use the same diagnostic vocabulary as logs so a
failed run can be understood without replaying planner internals.

A sharded field produces one usage event per shard selector call, plus one usage
event per reducer call or reducer shard. Per-field cost is derived reporting:
consumers may sum the usage events associated with that field, but the canonical
usage unit remains the selector call.

## Planning Algorithm

Phase 1 planning is intentionally mechanical:

1. Generate and filter canonical candidate sets as today.
2. Build one `SelectionTask` per soft-selected field/instance candidate set.
3. Estimate rendered prompt size for each task using the same prompt renderer,
   or a conservative estimator with contract coverage proving it upper-bounds
   the renderer.
4. Pack tasks into selector batches under the budget, preserving input order.
5. If one field task exceeds the configured budget, shard that field's
   candidate set into budget-fitting derived candidate-set views.
6. If one candidate alone exceeds the budget, fail before the provider call with
   `selector_prompt_candidate_budget_exceeded`.
7. Execute one batch selector call per packed batch or shard.
8. Merge observations in original task order.
9. Enforce the existing batch observation contract.

The planner packs by prompt budget, not by semantic field group. Semantic groups
such as "date fields" or "money fields" may be added later as schema hints, but
they are not the default unit of planning.

## Out of Scope

This ADR explicitly does not implement:

- field-specific ranking heuristics such as special cases for `invoice_date` or
  `subtotal_amount`;
- embedding or BM25 relevance ranking;
- automatic context shrinkage beyond existing deterministic prompt compaction;
- silent fallback from batch to iterative selectors.

These are adjacent strategies, not hidden behavior inside the first planner.

## Phase 2: Candidate Sharding

If Phase 1 shows that individual field tasks remain too large, extractx shards
the oversized field's candidate set before calling the provider.

Sharding is still presentation/orchestration policy over a canonical
`CandidateSet`; it does not drop candidates from the canonical seam-C output.
Each shard is a derived `CandidateSet` view with the same `field_id`,
`document_id`, `strategy_id`, and `instance_hint`, but only a contiguous slice of
the canonical candidates. Shards together cover the canonical candidate set
exactly once: no dropped candidates, no duplicated canonical ids.

Contiguous sharding is chosen because canonical candidate order is source order,
which is meaningful for selector diagnostics and reproducibility. Interleaved,
random, ranked, or field-specific ordering could be useful later, but would be a
separate ranking policy rather than default planning behavior.

Phase 2 reducer semantics:

- `ONE` / `OPTIONAL`: run selection on each shard. If every shard abstains,
  return one abstained observation. If exactly one shard selects candidates,
  return that observation. If multiple shards select candidates, run one final
  reducer selector call over only the selected shard winners and return that
  observation. If the reducer winner set still exceeds the prompt budget, shard
  the winners recursively; if that cannot reduce because every winner is already
  a single-candidate shard, fail with `selector_prompt_reducer_budget_exceeded`.
  Each reducer round must strictly decrease the winner count. If a round does
  not shrink the candidate set, fail with `selector_prompt_reducer_no_progress`
  instead of looping or silently retrying.
- `MANY`: run selection on each shard and return a deterministic union of all
  selected canonical candidate ids. Semantic dedupe is explicitly out of scope;
  duplicate-in-meaning candidates remain separate evidence ids until a later
  reducer contract exists.

If an individual candidate cannot fit in a single-candidate shard, extractx
fails before the provider call with `selector_prompt_candidate_budget_exceeded`.

The planner should log shard diagnostics (`field_id`, `shard_index`,
`shard_count`, `candidate_count`, `estimated_prompt_chars`,
`max_prompt_chars`) at the same seam as batch selector diagnostics.

## Phase 3: Bounded Parallel Shard Execution

Phase 2 solves the oversize availability cliff by splitting large selector
prompts into budget-fitting shard calls. That can increase wall time because
large sharded fields may require multiple independent provider calls plus a
reducer call.

Parallel shard execution is an execution-scheduling concern, not planner policy.
The planner remains pure, order-preserving, and unaware of concurrency. The
strategy may execute shard selector calls concurrently when all of the following
hold:

- concurrency is bounded by explicit configuration, not unbounded `gather`;
- every shard call is still a normal bounded selector call over a planned
  `BatchSelectorCallPlan`;
- each shard observation is contract-enforced independently;
- reducer execution starts only after every shard call has completed
  successfully;
- observations are restored to deterministic shard order before reducer
  semantics are applied;
- usage events are recorded for every shard and reducer call;
- one shard failure fails the sharded field/extraction loudly with shard
  diagnostics, rather than returning partial success;
- provider rate-limit or infrastructure failures are not hidden as abstentions
  or missing observations.

The first implementation should use a conservative per-extraction shard
concurrency limit, such as `selector_shard_concurrency=3`. A global provider
limiter may be added separately if runtime-level rate limiting becomes the
dominant operational concern.

The proof target for Phase 3 is:

- a synthetic slow-selector test showing bounded shard calls complete faster
  than serial execution;
- a max-concurrency test proving the configured bound is respected;
- a deterministic-order test proving reducer input order matches shard order;
- a usage-accounting test proving every shard call is recorded;
- a failure test proving one failed shard fails loudly with shard metadata;
- benchmark confirmation on the 314K and 506K fixtures showing lower wall time
  with the same final observation shape.

## Repair Policy Interaction

Batch selection and bounded repair are orthogonal executor policy concerns.
A consumer production extraction path needs both: the initial pass should use
batch planning/sharding for large-document survivability, and object-validator
repair should remain available so cross-field invariant failures can reselect
implicated fields with `retry_feedback`.

The contract shape is:

```python
ExecutorPolicy(strategy="batch", repair=True)
```

`strategy` chooses the initial selection mode. `repair` chooses whether the
executor composes the existing bounded repair passes around that initial mode.
For backward compatibility, `repair=None` preserves historical behavior:
`strategy="iterative"` repairs, while `strategy="independent"` and
`strategy="batch"` do not.

When repair is enabled after a batch initial pass, repair should use the batch
selector surface for schemas bound to `PydanticAIBatchSelector`. The executor
reruns selection for only implicated fields as a single-field batch call with
the validator reason in `ContextPack.retry_feedback`. This keeps schemas on one
selector mechanism and avoids requiring paired per-field and batch selector
bindings.

## Consequences

- Large-document failures become fast, diagnosable planning failures instead of
  slow provider timeouts.
- Normal large prompts can be split into multiple batch selector calls without
  changing the observation contract.
- Batch selection becomes a planned multi-call strategy when needed, but not the
  same thing as current `strategy="iterative"`.
- The selector layer gains a new internal planning responsibility that must be
  tested as a contract surface.
- Consumers can run fast prompt-iteration benchmarks on normal-sized documents
  while tracking oversize failures as a separate architecture thread.

## Alternatives considered

- **Force current iterative mode for large documents:** rejected as the primary
  fix. It splits by field, not by prompt budget, and does not help when one
  field alone has an oversized candidate set.
- **Raise concurrency:** rejected. It may reduce wall time slightly but leaves
  the same oversized prompts and provider failures.
- **Chunk raw document text like LangExtract:** rejected for the selector seam.
  It would push extractx toward model-authored values/evidence plus post-hoc
  alignment, which is a different producer contract.
- **Field-specific ranking first:** rejected. Hardcoded field-name heuristics
  turn schemas into hidden programs. If ranking is needed later, it should be a
  generic strategy such as lexical or embedding relevance against field
  descriptions.
- **Silent candidate truncation:** rejected. Any prompt-size reduction that drops
  candidates must be explicit, recorded, and contract-tested.
- **Size-sorted packing:** rejected for the initial planner. It could improve
  batch utilization, but it changes task order and makes diagnostics less
  reproducible. The default planner preserves input order.

## Related

- [ADR-0005: Candidate Overflow Policy](0005-candidate-overflow-policy.md)
- [ADR-0008: Observation-Shaped LLM Extraction](0008-observation-shaped-llm-extraction.md)
- [ADR-0023: Batch Selector Observations](0023-batch-selector-observations.md)
- [ADR-0024: Readable Bounded-ID Selector Prompts](0024-readable-bounded-id-selector-prompts.md)
