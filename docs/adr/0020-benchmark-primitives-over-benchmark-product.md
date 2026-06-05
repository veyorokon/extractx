# ADR-0020: Ship Benchmark Primitives, Not A Benchmark Product

**Status:** Accepted
**Date:** 2026-05-04

## Context

extractx now has replay artifacts, usage events, candidate filters, pydantic
field validation, object validation, and bounded repair. Consumers need a
repeatable way to tell whether changes improved extraction quality or merely
moved failures between candidate generation, selection, and validation. The
benchmark surface must preserve replay discipline and avoid turning live LLM
calls into the default evaluation path.

## Decision

The extractx project will ship domain-agnostic benchmark primitives, not a
benchmark product. The public eval surface is staged:

1. deterministic candidate scoring over frozen fixtures;
2. replay-based selection / validation scoring over recorded artifacts;
3. optional live selector benchmarking as an explicitly soft-compute,
   model-pinned, usage-reporting harness.

The primitive surface belongs outside the core runtime path. A sibling package
such as `extractx_eval` may expose fixture models, scorers, reports, and replay
diff helpers, while `src/extractx` must not import the eval package.

## Surface Contracts

### Fixtures

Benchmark fixtures are frozen, portable records of source text and expected
field evidence. The standard fixture-pack shape is:

- JSONL metadata for cases, expected fields, expected normalized values, and
  expected source spans or evidence text;
- a raw document directory referenced by stable case IDs;
- no domain-specific threshold policy.

Expected evidence may be expressed either as text or as spans. Evidence-text
fixtures are first-class because most hand-labeled corpora start with copied
snippets rather than byte offsets. Expected spans, when supplied, use extractx's
public `SourceSpan` contract: UTF-8 byte offsets into the raw document text.
Consumers that author fixtures from Python string indices must translate before
writing fixture JSONL. Span expectations are stricter than text expectations
because they test exact grounding, not just value presence, but they are not
required for a valid fixture.

The fixture contract describes what the consumer expects extractx to surface.
It does not decide whether a score is good enough for a consumer's product.

### Candidate Scoring

Candidate scoring is deterministic:

```python
score_candidates(schema, fixtures, *, runtime=None) -> BenchmarkReport
```

It runs candidate generation and field candidate filtering through the real
extractx machinery and reports per-field recall, precision, missing gold
evidence, and extra candidate volume. It must not call an LLM.

Candidate scoring answers whether the right evidence entered the candidate set.
It does not judge selector quality.

Candidate scoring compares by stable case ID and schema field ID. It must report
unmatched fixture fields, fields that are present in the schema but absent from
the fixture, and setup failures separately from ordinary recall / precision
misses.

Candidate scoring returns the shared `BenchmarkReport` shape.

Candidate scoring must emit named metrics:

- `recall_at_candidates` — whether expected evidence appears in the generated
  candidate set before field filters;
- `recall_at_filtered` — whether expected evidence survives field filters;
- `precision_at_candidates` — how much generated candidate volume is explained
  by fixture gold evidence;
- `precision_at_filtered` — how much filtered candidate volume is explained by
  fixture gold evidence;
- `true_positive_at_*`, `false_negative_at_*`, and `false_positive_at_*`
  aliases for the same candidate and filtered-candidate counts, for consumers
  that prefer confusion-matrix vocabulary.

`GoldField.expected_absent=True` is the explicit negative fixture shape. It is
used to count false positives when a field should have no surviving evidence.
Empty evidence without `expected_absent=True` is unlabeled for candidate
scoring and is reported as a comparability failure, not silently treated as a
negative. True negatives are not a primary aggregate because there is no useful
span-level negative universe in arbitrary documents.

Each candidate-stage field row carries producer attribution, including
`strategy_id` when available. When expected evidence appears before filtering
but not after filtering, the report should carry filter-drop attribution:
the rejected candidate id, the filter node that rejected it, and a structured
reason such as a missing `ContextContains.all_of` needle.

Span matching is an explicit scorer configuration, not hidden policy:

```python
class SpanMatchConfig(BaseModel):
    mode: Literal["exact", "overlap", "contains", "contained_by", "iou"] = "overlap"
    min_iou: float = 0.5
```

The report records the match config used. Text-only `GoldEvidence` matches
case-insensitively against `candidate.text` first. Context matching is an
explicit fallback and any match through context must be marked as
`matched_via="context"` so recall is not confused with exact candidate text
coverage.

Cross-variant comparison is not part of candidate scoring. Users compare
variants by running `score_candidates(...)` separately and passing the resulting
reports to a later diff surface.

### Replay Scoring

Replay scoring is deterministic:

```python
score_replay(replay, fixtures) -> BenchmarkReport
```

It reads recorded replay artifacts and compares candidate sets, selected
evidence, normalized values, validation outcomes, and final materialized
evidence against fixture expectations.

Replay artifacts currently carry the candidate sets consumed by selection,
which are post-filter candidate sets. Raw pre-filter candidate misses remain
the responsibility of `score_candidates(...)`.

Replay scoring compares by fixture case ID and field ID, not by schema class
identity. When a replay cannot be honestly matched to a fixture, the scorer
emits a setup / comparability failure instead of inventing a benchmark miss.

Replay scoring emits deterministic stage rows for:

- `filtered_candidates` — expected evidence survived into the replay candidate
  set;
- `selection` — the selected candidate IDs include the expected evidence;
- `validation` — selected expected evidence became a validated field, with
  validation negatives surfaced when it did not;
- `materialization` — validated evidence appeared in final instances, with
  optional expected-value accuracy.

Replay scoring returns the shared `BenchmarkReport` shape.

### Replay Diff

Replay diffing compares two recorded artifacts for the same input:

```python
diff_replays(replay_a, replay_b) -> ReplayDiff
```

It reports changes by stage: candidates, filtered candidates, selected IDs,
normalized values, validation failures, object issues, repair attempts, usage,
and terminal outcome. It is a forensic projection over canonical replay data,
not an authority over extraction truth.

## Report Shape

All scorers return a serializable `BenchmarkReport` with the same top-level
shape so consumers can persist, compare, and project reports without knowing
which scorer produced them. The report contains:

- scorer metadata: scorer name, scorer version, generated timestamp, and input
  refs;
- per-case rows keyed by stable case ID;
- per-field rows keyed by case ID and field ID;
- aggregate counts and rates for recall, precision, exact-value matches,
  setup / comparability failures, and ordinary misses;
- stage labels so a row can identify whether the gap is in candidates, filtered
  candidates, selection, normalization, validation, object validation, repair,
  or replay comparability;
- producer versions and replay refs when available.

The report is a derived projection. Canonical truth remains the fixture pack,
runtime extraction output, and replay artifacts.

### Live Smoke Runs

Live smoke runs are the only eval-adjacent surface that calls the production
soft-compute path directly:

```python
smoke_run(schema, fixture, runtime) -> SmokeResult
```

Smoke is not a benchmark. It answers whether the production path completed and
produced a replay artifact for a fixture with the current runtime, provider,
prompt, and model. It must require replay storage and fail loudly when no replay
artifact is produced.

`SmokeResult` uses two orthogonal status axes:

```python
type SmokeRunStatus = Literal[
    "completed",
    "completed_with_outcome",
    "errored",
]

type ValueCheckStatus = Literal[
    "matched",
    "mismatched",
    "not_checked",
]
```

The live run result carries the typed extraction `Outcome | None`, a
`replay_artifact_ref`, usage events, and typed `ErrorInfo | None`. It does not
carry a materialized extraction object; consumers that need typed values should
materialize from replay through an explicit helper or use deterministic replay
scorers.

`ErrorInfo` must include at least a stable `kind` and human-readable `message`
so operators do not parse error strings to distinguish setup, schema, runtime,
provider, and timeout failures.

Value checking is a separate deterministic projection:

```python
smoke_check_values(smoke_result, fixture) -> ValueCheckResult
```

Replay history is consumer-owned. `SmokeResult` does not carry a previous replay
reference; consumers that want change detection call `diff_replays(prev, curr)`
with refs they manage.

### Live Selector Benchmarking

Live selector benchmarking is optional and explicitly soft-compute:

```python
run_live_selection_benchmark(schema, fixtures, runtime) -> BenchmarkReport
```

When added, it must pin model/provider identity, produce replay artifacts,
surface usage/cost, and report termination reasons. It must not be the default
selection scorer because live model calls measure today's producer behavior,
not only the schema's contract.

Live selector benchmarking is not part of the first implementation slice, and
it is not the same as smoke. Smoke proves the production path can run and
produce replay. Live selector benchmarking measures pinned model/provider
selection behavior against fixtures and emits a `BenchmarkReport`. The first
slice should land fixture models, deterministic candidate scoring, and package
isolation tests; replay scoring follows once the fixture and report contracts
are proven.

## Implementation Phases

Progress is tracked in task docs or issues. These phases define the durable
completion conditions so accepted benchmark work does not stay half-landed.

- **Phase 1 — fixture and report contract.** Land the `extractx_eval` sibling
  package, fixture models, evidence text/span expectation models, JSONL +
  raw-document loader, serializable `BenchmarkReport`, setup/comparability
  failure rows, and package isolation tests. Complete when package-local tests
  prove fixture loading/report serialization and root tests prove `src/extractx`
  does not import `extractx_eval`.
- **Phase 2 — deterministic candidate scoring.** Land `score_candidates(...)`
  over real schema/spec candidate generation and candidate filtering, with no
  selector and no LLM calls. Complete when tests prove candidate recall /
  precision for both evidence-text and span-based fixtures, including filtered
  candidate sets and setup failures.
- **Phase 3 — replay scoring and replay diff.** Land `score_replay(...)` and
  `diff_replays(...)` over recorded replay artifacts. Complete when tests prove
  selected evidence, normalized values, validation outcomes, object issues,
  repair attempts, usage, and cross-schema/prompt replay comparability are
  reported without live compute.
- **Phase 4 — live smoke surface.** Reframe the existing live end-to-end runner as a
  smoke surface that runs production extraction, requires replay, emits
  `SmokeResult`, and keeps value checking in a separate deterministic
  `smoke_check_values(...)` projection. Complete when the old harness is no
  longer documented as a benchmark and tests prove smoke run status and value
  check status remain separate.
- **Phase 5 — optional live soft-compute benchmark.** Land live selector
  benchmarking only after phases 1-4 are stable. Complete when the harness pins
  model/provider identity, produces replay artifacts, surfaces usage/cost, and
  keeps live runs separate from deterministic scoring and smoke.

## Consequences

Consumers get a stable way to compare schema, strategy, filter, prompt, model,
and validation changes without inventing incompatible local score formats. A
failure can be located at the correct stage: candidate generation, filtering,
selection, normalization, object validation, repair, or resolution.

The core runtime stays boring. Benchmark code may depend on extractx, but
extractx does not depend on benchmark code. The eval package can grow fixture
loaders, reports, and diff projections without widening `extract(...)`,
`run_extraction(...)`, or the tier-1 `extractx` import surface.

Live LLM benchmarks remain possible, but they are not smuggled into the
deterministic scoring contract. Any live benchmark must be replay-producing and
usage-reporting so soft-compute runs stay auditable.

The existing live end-to-end runner is preserved as smoke, not benchmark authority.
Its live side effect produces a replay artifact; deterministic value checks,
replay scoring, and replay diffing consume that artifact afterward.

## Non-Goals

extractx will not own:

- domain thresholds such as "good enough";
- schema-search or prompt-search optimizers;
- dashboards or web UX;
- CI policy for pass/fail decisions;
- consumer-specific projections;
- domain-specific fixture packs.

Consumers may build those on top of the primitive reports.

## Alternatives considered

- **No upstream benchmark surface.** Rejected. Every consumer would recreate
  fixture models, recall math, replay comparison, and miss taxonomy. That would
  fragment the contract around extractx's own seams.
- **Live LLM benchmark as the primary API.** Rejected. It conflates schema
  quality, prompt quality, provider drift, model drift, and spend. Live
  benchmarking is useful only when explicitly pinned and replayed.
- **Core-runtime benchmark APIs in `src/extractx`.** Rejected. Benchmarking is
  a consumer of extraction artifacts, not part of extraction execution. Keeping
  the eval surface in a sibling package avoids runtime dependency pressure and
  preserves one canonical execution path.
- **CLI / web benchmark product.** Rejected. Presentation, sweeps, threshold
  policy, and workflow integration are consumer concerns. The extractx project
  should expose typed primitives that CLIs, pytest suites, notebooks, and
  product dashboards can compose.
- **Optimizer that proposes schema or prompt variants.** Rejected. The
  objective function is domain-specific. extractx can report stage-level
  quality; consumers decide what tradeoffs matter.
- **Score materialized pydantic objects only.** Rejected. Materialization is a
  derived projection. Scoring must remain possible when materialization fails,
  and it must identify whether the miss happened before or after selection.

## Related

- [ADR-0012: Provenance Grouping And Replay Ref Contracts](0012-provenance-grouping-and-replay-ref-contracts.md)
- [ADR-0015: Minimal Soft Compute Usage Events](0015-minimal-soft-compute-usage-events.md)
- [ADR-0018: Candidate Filter Seam](0018-candidate-filter-seam.md)
- [ADR-0019: Separate Rule Surfaces And Repair Ownership](0019-rule-surfaces-and-repair-ownership.md)
- [Task: Benchmark Primitives Phase 1](../tasks/benchmark-eval-harness-phase-1.md)
