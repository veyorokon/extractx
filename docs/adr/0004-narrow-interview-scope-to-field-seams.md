# ADR-0004: Narrow InterviewTranscript capture scope to field-scoped seams

**Status:** Accepted
**Date:** 2026-04-20

## Context

ADR-0002 adopted pydantic-ai as the default llm-backed Selector and introduced `InterviewTranscript` as a sibling artifact enabling `ExtractionResult.interview()`. That ADR stated capture at four seams: D (Selector), C.alt (GroundedProposalGenerator), G.planner (InstancePlanner), G.resolver (InstanceResolver).

During T0 doc-integrity review, a structural inconsistency surfaced between the claimed capture surface and the object/API shape:

- `InterviewTranscript.field_id: str` is **non-optional**.
- `ExtractionResult.interview(*, field_id, instance_key=None, attempt_index=None, question) -> str` requires `field_id`.
- The prose refers to "the given field's selection."

Seams D and C.alt are field-scoped — both operate on one field per invocation, and a `field_id` is always available. Seams G.planner and G.resolver are not field-scoped — they operate on document-level (planner: produces tentative InstanceKeys from the whole document) or all-field (resolver: consumes every ValidatedField and CandidateSet) inputs. Neither has a natural `field_id` to attach.

Implementing the broader claim would require either:
- a sentinel `field_id` for non-field captures (Silent-None / Raw-Payload-Escape-Hatch family), or
- generalizing `InterviewTranscript` with a `seam` discriminator and making `field_id: str | None` (Silent-None shape; adds seam-dispatch burden on every consumer), or
- introducing separate transcript types per capture surface (three canonical types; three API methods).

None of those match the field-scoped transcript-replay pattern cited in
ADR-0002. The type and API that ADR-0002 landed are correct; the capture scope
prose drifted broader than the capability the type supports.

## Decision

**Narrow `InterviewTranscript` capture to field-scoped seams: D and C.alt only. G.planner and G.resolver do not capture interview transcripts in v1.**

Specifically:

1. `ExecutorPolicy.capture_interview_transcripts=True` causes transcript capture at seams D and C.alt only.
2. Capture does not apply at G.planner or G.resolver, regardless of whether those seams are pydantic-ai-backed.
3. `InterviewTranscript` canonical object shape is unchanged. `field_id: str` remains non-optional and now honestly reflects the capture scope.
4. `ExtractionResult.interview(*, field_id, instance_key=None, attempt_index=None, question) -> str` signature is unchanged.
5. `UsageEvent` emission at G.planner and G.resolver (for soft planners/resolvers) is unchanged and independent of interview capture. Budget tracking is preserved at all soft-compute seams.
6. ADR-0002 is amended, not superseded. Its core decision (pydantic-ai as default Selector; `InterviewTranscript` sibling artifact; `.interview()` public API; no embedding in `ReplayArtifact`) stands. Only the capture-scope clause narrows.

## Consequences

- **Upside:** `InterviewTranscript` type and `.interview()` API are internally consistent with the capture surface. No sentinel `field_id`, no Silent-None discriminator, no seam-dispatch burden on consumers.
- **Upside:** primary debugging question ("why did the selector pick X over Y for field Z in instance W?") remains fully supported — that field-scoped debugging use case is the motivation for the capability.
- **Upside:** plugin-public surface stays minimal. No additional transcript types, no new API methods, no new protocol.
- **Tradeoff:** soft `NeuralInstancePlanner` or `NeuralInstanceResolver` implementations lose the interview surface in v1. Debugging planner/resolver decisions is available via `ReplayArtifact` + `ExecutionTrace` (forensic structure preserved; just no LLM conversation replay for these seams).
- **Tradeoff:** a future decision that validator-guided or agent-guided planning/resolution warrants interview capability requires either (a) separate transcript types per capture surface, or (b) generalized `InterviewTranscript` with a typed discriminator — both of which were deferred pending real evidence of need.

## Alternatives considered

- **Generalize `InterviewTranscript` with a seam discriminator.** Rejected. `field_id: str | None` + `seam: Literal["D", "C.alt", "G.planner", "G.resolver"]` creates Silent-None shape; forces every consumer to dispatch on `seam` before using the transcript. Public API would widen (`.interview(seam=..., ...)`) for most callers who only care about field-scoped selection.
- **Separate transcript types per capture surface.** Rejected for v1 on surface-cost grounds. Three new canonical types and three API methods with no evidence that planner/resolver interview capability is load-bearing. Held in reserve for a future ADR if real debugging scenarios demonstrate the need.
- **Leave capture scope as ADR-0002 stated; implement it with a sentinel.** Rejected. Silent-None / Raw-Payload-Escape-Hatch pattern; the architecture explicitly prohibits this in §15.

## Related

- [ADR-0002](0002-pydantic-ai-default-selector-and-interview.md) — amended by this ADR on the capture-scope clause
- `docs/architecture.md` §9 `InterviewTranscript` (shape unchanged)
- `docs/architecture.md` §13 `.interview()` semantics (prose tightened)
- `docs/architecture.md` §18 summary (capture list narrowed)
- `docs/architecture.md` §15 anti-patterns (`Silent-None`, `Raw-Payload-Escape-Hatch`)
- T0 review queue (T0c thread)
