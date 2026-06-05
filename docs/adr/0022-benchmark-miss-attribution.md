# ADR-0022: Attribute Benchmark Misses At Extraction Seams

**Status:** Accepted
**Date:** 2026-05-04

## Context

ADR-0020 gives extractx deterministic candidate and replay scoring primitives.
Those scores can say that recall or selection failed, but consumers still need
to know which seam produced the miss before deciding whether to change schema
bindings, filters, normalizers, fixtures, or runtime behavior. Without
structured attribution, benchmark reports force manual replay/source inspection
and make every recall miss look like the same kind of failure.

In this ADR, "gold" means a fixture expectation supplied by a benchmark author.
It is not extractx asserting objective real-world truth; it is the comparison
target that makes a deterministic scorer possible.

## Decision

Benchmark reports will carry structured miss attribution for candidate and
replay scoring. extractx owns generic, domain-agnostic attribution categories
that correspond to its seams; consumers own domain-specific interpretation and
schema fixes.

Miss attribution is diagnostic only. It does not auto-expand candidate
strategies, derive filters from validator prose, mutate schemas, or decide
whether a miss is acceptable for a consumer's product.

## Surface Contract

Each benchmark field row may carry zero or more `MissAttribution` entries:

```python
class MissAttribution(BaseModel):
    stage: Literal[
        "candidates",
        "filtered_candidates",
        "selection",
        "normalization",
        "validation",
        "object_validation",
        "materialization",
        "comparability",
    ]
    kind: Literal[
        "not_generated",
        "generated_then_filtered",
        "wrong_candidate_selected",
        "selection_abstained",
        "span_near_miss",
        "normalization_mismatch",
        "validation_rejected",
        "object_issue",
        "materialization_missing",
        "fixture_schema_mismatch",
        "fixture_missing_evidence",
        "fixture_grounding_dispute",
        "setup_failure",
    ]
    field_id: str
    gold_index: int | None = None
    candidate_id: str | None = None
    strategy_id: str | None = None
    filter_node: str | None = None
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
```

The exact Python model name is implementation-owned, but the report contract
must preserve the same information: stage, kind, field, implicated gold or
candidate ids when known, producer/filter attribution when known, a stable
reason string, and a JSON-serializable details envelope.

### Candidate Scoring Attribution

`score_candidates(...)` attributes misses across the deterministic grounding
path:

- `not_generated` — expected evidence does not match any generated candidate;
- `span_near_miss` — candidate text or span overlaps the expected evidence but
  misses under the configured `SpanMatchConfig`;
- `generated_then_filtered` — expected evidence matched a generated candidate
  but did not survive field filtering;
- `fixture_schema_mismatch` — fixture field id is not present in the schema;
- `fixture_missing_evidence` — fixture lacks evidence and is not marked
  `expected_absent=True`;
- `fixture_grounding_dispute` — fixture expected value and expected evidence
  disagree in a way the scorer cannot honestly classify as an extractx miss;
- `setup_failure` — strategy/filter execution failed before a fair score could
  be produced.

When multiple `strategy_bindings` are present, attribution records the producing
`strategy_id` for matched and near-miss candidates. When a filter drops a
matched or near-miss candidate, attribution records the filter expression node
and the structured rejection reason when available.

Candidate scoring must not infer a domain fix. For example, repeated
`not_generated` rows near a document dateline may make a regex strategy
obvious to a consumer, but extractx reports the miss shape and context rather
than authoring that schema change.

### Replay Scoring Attribution

`score_replay(...)` attributes misses across recorded replay stages:

- `wrong_candidate_selected` — expected evidence was present in the replay
  candidate set but a different candidate was selected;
- `selection_abstained` — expected evidence was present but selection abstained;
- `normalization_mismatch` — selected evidence was grounded, but normalized
  value differed from the fixture expectation;
- `validation_rejected` — selected evidence failed field validation;
- `object_issue` — validated fields failed object validation;
- `materialization_missing` — evidence existed before final materialization but
  did not appear in final instances;
- `fixture_schema_mismatch`, `fixture_missing_evidence`,
  `fixture_grounding_dispute`, and `setup_failure` retain the same meaning as
  candidate scoring.

Replay scoring must distinguish "not present in replay candidate set" from
"present but not selected." If raw pre-filter candidates are not available in a
replay artifact, replay scoring reports only the earliest stage it can
observe honestly and leaves pre-filter diagnosis to `score_candidates(...)`.

### Context Snippets

Attribution may include bounded source context for human or agent diagnosis:

```python
details={
    "gold_text": "...",
    "candidate_text": "...",
    "source_context": "...",
}
```

Context is a derived diagnostic projection. It is not canonical evidence and
must not replace fixture evidence, candidate ids, source spans, or replay
records as the authority.

### Details Vocabulary

`details` is a JSON-serializable escape valve for diagnostic context, but common
keys must stay stable so consumers can group and review attribution rows without
parsing `reason` strings. Implementations may omit unavailable keys, but when
they carry the following concepts they must use these names:

| key | meaning |
|---|---|
| `gold_text` | fixture evidence text when available |
| `gold_span` | fixture source span when available |
| `fixture_value` | expected normalized value from the fixture |
| `candidate_text` | implicated candidate text |
| `candidate_span` | implicated candidate source span |
| `candidate_id` | implicated candidate id; mirrors the top-level field when present |
| `selected_candidate_id` | selected candidate id when different from the implicated candidate |
| `selected_candidate_text` | selected candidate text |
| `normalized_value` | extractx normalized value |
| `validation_code` | stable validation failure code when available |
| `object_issue_code` | stable object-validation issue code when available |
| `filter_node` | filter expression node summary; mirrors the top-level field when present |
| `filter_reason` | structured filter rejection reason |
| `strategy_id` | producer strategy id; mirrors the top-level field when present |
| `source_context` | bounded source context snippet |
| `match_config` | span/text matching configuration used for the comparison |

Kind-specific minimum details:

- `not_generated`: `gold_text` or `gold_span`, plus `source_context` when
  available.
- `span_near_miss`: `gold_text` or `gold_span`, `candidate_text` or
  `candidate_span`, and `match_config`.
- `generated_then_filtered`: `candidate_id`, `candidate_text`, `filter_node`,
  and `filter_reason` when available.
- `wrong_candidate_selected`: `gold_text`, `candidate_id`,
  `selected_candidate_id`, and `selected_candidate_text` when available.
- `normalization_mismatch`: `candidate_text`, `normalized_value`, and
  `fixture_value`.
- `validation_rejected`: `candidate_text`, `normalized_value`, and
  `validation_code` when available.
- `object_issue`: `object_issue_code` when available and implicated fields or
  values when the replay exposes them.
- `fixture_grounding_dispute`: `gold_text`, `fixture_value`, and any
  scorer-observed normalized value that explains the dispute.

## Consequences

Consumers can rank benchmark failures by actionable seam instead of treating
all misses as model or prompt defects. A downstream system can cluster
"field X not generated by any strategy" separately from "field X was generated
but filtered out" and decide whether the fix belongs in schema bindings,
filters, normalization, fixture labels, or extractx itself.

The report gets more verbose, but the verbosity is structured and scoped to
diagnosis. This is preferable to consumers parsing free-form messages or
replaying runs manually to recover information extractx already observed.

The attribution contract also keeps the eval package honest: benchmark reports
remain derived read models over fixtures, candidate sets, and replay artifacts.
They do not become an optimizer, a schema author, or benchmark authority beyond
the observed seams.

## Implementation Phases

- **Phase 1 — candidate miss attribution:** add structured attribution entries
  to `score_candidates(...)` for `not_generated`, `span_near_miss`,
  `generated_then_filtered`, fixture comparability failures, setup failures,
  strategy attribution, and filter-node attribution. Complete when tests prove
  each kind with generic fixtures and the report remains JSON-serializable.
- **Phase 2 — replay miss attribution:** add structured attribution entries to
  `score_replay(...)` for selection, normalization, validation, object
  validation, and materialization misses. Complete when tests prove the scorer
  identifies the earliest honestly observable failed replay stage without live
  compute.
- **Phase 3 — clustering helpers:** optionally add pure report projections that
  group attributions by field, stage, kind, strategy, and filter node. Complete
  only if consumers repeatedly implement the same grouping locally. These
  helpers must not suggest or apply schema changes.

## Alternatives considered

- **Leave attribution to consumers:** rejected. Consumers would have to
  reconstruct extractx seam state from reports and replays, leading to
  duplicate miss taxonomies and inconsistent diagnosis.
- **Free-form miss messages only:** rejected. Human-readable messages are useful
  presentation, but downstream agents and projections need stable `stage` and
  `kind` values.
- **Auto-fix schemas from misses:** rejected. Miss attribution can show that a
  field was not generated or was filtered out, but deciding the right strategy,
  pattern, context, or threshold is domain-specific schema work.
- **Treat every recall miss as a selector/model failure:** rejected. If expected
  evidence never entered the candidate set, no selector can recover it.
- **Add a generic optimizer loop:** rejected. Optimization objectives,
  precision/recall tradeoffs, and acceptable false positives are consumer
  policy, not extractx library policy.

## Related

- [ADR-0018: Candidate Filter Seam](0018-candidate-filter-seam.md)
- [ADR-0019: Rule Surfaces And Repair Ownership](0019-rule-surfaces-and-repair-ownership.md)
- [ADR-0020: Ship Benchmark Primitives, Not A Benchmark Product](0020-benchmark-primitives-over-benchmark-product.md)
- [ADR-0021: Make Candidate Strategy Composition First-Class](0021-plural-candidate-strategy-bindings.md)
