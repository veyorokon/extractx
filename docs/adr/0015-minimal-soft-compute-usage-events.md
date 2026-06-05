# ADR-0015: Minimal Soft-Compute Usage Events

## Status

Accepted.

## Context

LLM-backed seams need token visibility for operations, replay forensics, and
budget enforcement. This must not turn extractx into a pricing gateway or add a
provider-normalization dependency.

Existing architecture already defines `UsageEvent` and `TokenCountBudget`, but
the LLM selector/proposer path did not wire provider usage into extraction
results or replay.

## Decision

Use a minimal provider-result envelope:

```python
ProviderResult[T](output=T, usage_event=UsageEvent | None)
```

Provider adapters may return either a bare structured output or this envelope.
Selectors and proposers validate only `output`; `usage_event` is operational
metadata and never evidence.

`UsageEvent` remains an ordered per-call event, not an aggregate:

- `operation`: e.g. `"selector"` or `"instance_proposer"`
- `field_id` / `instance_id` when available
- `model_id`
- `input_tokens`, `output_tokens`, `total_tokens`
- `finish_reason`, `response_id`, `soft_call_identity` when available
- `raw_usage` and `raw_response_metadata` as provider/framework passthrough

The executor records every emitted event into `Runtime.budget`, stores the same
ordered tuple on `Extraction.usage_events`, and persists it into
`ReplayArtifact.usage_events`. `Extraction.usage()` returns that tuple.

Cost in dollars stays outside extractx. Consumers derive cost from
`UsageEvent.raw_usage`, `model_id`, and their own pricing source.

## Consequences

Usage attribution survives mixed-model runs. A consumer can group by operation,
model, field, or instance without losing the original per-call records.

Provider-specific parsing is isolated to provider adapters. Core only knows the
`UsageEvent` contract.

No LiteLLM, Langfuse, OpenLIT, tokenizer, or pricing package becomes a core
dependency.

## Alternatives Rejected

- **Aggregate-only usage totals.** Rejected because it loses attribution when
  selectors and proposers use different models or call counts.
- **Core pricing tables.** Rejected per ADR-0001; pricing changes outside
  extractx and would create maintenance/security surface.
- **Provider gateway dependency.** Rejected. A gateway can be a consumer-owned
  provider adapter, not extractx core.
