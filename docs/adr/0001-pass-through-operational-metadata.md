# ADR-0001: Pass through operational metadata; no pricing in core

**Status:** Accepted
**Date:** 2026-04-20

## Context

During the architecture pass, we had to decide what extractx owns regarding **operational metadata** emitted by the subsystems it integrates with: LLM provider usage objects, parser native metadata (unstructured, docling, pymupdf), finish reasons, response envelopes, token counts, cost signals.

Two options:

1. **Own it.** Invent extractx-native abstractions over each: a `Cost` type that translates provider pricing, a `PageStructure` type that normalizes parser metadata across backends, a `FinishReason` enum that unifies `stop` / `length` / `content_filter` / etc.
2. **Pass it through.** Emit a typed envelope (minimal canonical projection for the fields extractx actually uses internally) plus a `raw_*` passthrough field carrying the subsystem's native object unchanged. Consumers who want derived facts (cost in dollars, layout statistics, etc.) compute them from the raw shape using their own sources.

The concrete case that forced the choice: **Budget / cost tracking**. We initially planned to ship a default `Budget` impl wrapping `litellm` or `tokencost` for dollar-denominated budgets. Both libraries have known concerns (`litellm` is a heavyweight gateway with a history of advisories; `tokencost` is lighter but still requires keeping pricing tables current across many providers). Wrapping either commits extractx to carrying stale pricing tables and inherits their security and maintenance surface.

Zooming out, the same question applies to every boundary where a subsystem emits operational metadata: parser metadata from document adapters, provider usage objects from LLM calls, finish reasons, response envelopes.

## Decision

**Pass through operational metadata. Do not reshape it. Do not ship pricing in core.**

Specifically:

1. Add a `UsageEvent` canonical object with a minimal typed projection (`producer_version`, `model_id`, `input_tokens`, `output_tokens`, `finish_reason`, `timestamp_ns`) plus a `raw_usage: Mapping[str, Any] | None` passthrough field carrying the provider's native usage object unchanged.
2. `Budget` protocol receives `UsageEvent`s via `record(event)` and decides allow/deny via `check()`. It does not price.
3. Ship a default `TokenCountBudget` in core that tracks input/output tokens against user-provided limits. No pricing. No provider-specific dependencies.
4. **Do not depend on `litellm` or `tokencost` in core or extras.** Consumers who want dollar-denominated budgets subclass or wrap `Budget`, read `UsageEvent.raw_usage`, and apply their own pricing source (provider invoice, internal table, `tokencost` or `litellm` installed in their own environment).
5. Apply the same passthrough pattern elsewhere: `DocumentView.metadata["parser"]` carries the parser's native metadata unchanged; `ReplayArtifact` captures `UsageEvent`s with `raw_usage` preserved (payload content stripped per existing rule, but usage metadata preserved).
6. Formalize this as principle 21 in `docs/architecture.md` §2: "pass through operational metadata; do not reshape it."

The rule applies only to **operational metadata**. Semantic public types (`FieldProposal`, `ResolvedFieldProposal`, `InstanceKey`, etc.) remain fully typed and provider-agnostic — provider quirks stay behind their seam (principle 15 is unchanged).

## Consequences

- **Upside:** no stale pricing tables to maintain; no inherited security surface from pricing libraries; no "which provider did we miss" bug class; consumers get ground-truth cost from their provider bill, not our abstraction of it.
- **Upside:** users get the full fidelity of subsystem outputs (usage objects, parser metadata, finish reasons). Debugging and audit improve because we don't lose information in translation.
- **Upside:** the integration surface with each subsystem shrinks — we need the envelope, not a pricing table or a metadata translator.
- **Upside:** the rule generalizes — applying it to parser metadata, LLM response envelopes, and future subsystem integrations (speech-to-text usage, image-parsing metadata) reduces total code and decision surface.
- **Tradeoff:** users who want cost-in-dollars do slightly more work (add a pricing source, wrap `Budget`). In exchange they trust their own numbers rather than ours.
- **Tradeoff:** `UsageEvent.raw_usage` is `Mapping[str, Any]` — not fully typed. That's the point. Consumers inspect it knowing its shape comes from the provider, not from us.
- **Tradeoff:** we give up the ability to enforce "budget in dollars" at the executor level. Only token/call budgets are enforced out of the box. Users who need dollar enforcement wrap `Budget` themselves and implement their own pricing table.

## Alternatives considered

- **Ship `litellm` as a default Budget impl.** Rejected due to its gateway/proxy surface, past advisory history, heavyweight dep tree, and the fact that we'd inherit its pricing tables going stale. Users can still use `litellm` in their own environment and wrap `Budget` around it.
- **Ship `tokencost` as a default Budget impl.** Lighter than `litellm` but still couples extractx to a third-party pricing source that must be kept current across providers. Same structural problem as `litellm`: we own pricing we shouldn't own.
- **Reshape provider usage into a unified `Usage` type without a `raw_usage` passthrough.** Rejected — loses fidelity. Every provider has fields the others don't (cached tokens, tool-use tokens, tier info). If we reshape, consumers who need those fields have no path.
- **Own parser metadata translation across unstructured / docling / pymupdf / marker.** Rejected for the same reason: each parser has unique structural information (element trees, layout analyses, reading order). Consumers who need it read the native object. extractx only uses what it needs internally (anchor spans, page refs).

## Related

- Principle 21 in `docs/architecture.md` §2
- Anti-patterns "Reshape-Operational-Metadata" and "Core-Owns-Pricing" in `docs/architecture.md` §15
- `UsageEvent` canonical object in `docs/architecture.md` §9
- `Budget` protocol contract in `docs/architecture.md` §7 seam J
- `docs/tasks/bootstrap-project-skeleton.md` — dependency list reflects this decision (no `litellm`, no `tokencost`)
- `docs/tasks/select-default-document-adapter.md` — the parser-metadata passthrough rule shapes the evaluation criteria for default adapter choice
