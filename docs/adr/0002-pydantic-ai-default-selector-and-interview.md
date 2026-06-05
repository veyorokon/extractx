# ADR-0002: Adopt pydantic-ai as default llm-backed Selector; enable interview capability

**Status:** Accepted
**Date:** 2026-04-20
**Amended by:** [ADR-0004](0004-narrow-interview-scope-to-field-seams.md) — capture scope narrowed to seams D and C.alt only; G.planner and G.resolver capture removed.

## Context

During architecture review, a gap surfaced between the current `ReplayArtifact` design and a real debugging need: **interviewing an agent about why it did or did not pick a field**. Our replay artifact proves decision reproducibility bytewise under pinned producers, but deliberately excludes prompt text and response content for privacy and size reasons. That means you can verify "the same selection reproduces," but you cannot ask the agent "why did you pick X over Y?"

A prior implementation already ships this capability. Its transcript interview
command loads a persisted `AgentTranscriptArtifact`, deserializes the full
message history, rebuilds the exact agent with the same model and deps, and
calls `agent.run_sync(question, message_history=history)`. The agent responds
in the context of its own prior conversation — tool calls, structured outputs,
and reasoning preserved.

We initially considered inventing our own `InterviewTranscript` format on top of `instructor` (our current provisional default llm-backed Selector). Investigation showed that **pydantic-ai already provides the entire mechanism natively**:

- `result.all_messages()` returns the full message history including tool calls and tool returns
- `ModelMessagesTypeAdapter` serializes / validates the history to/from JSON with typed round-trip
- `Agent.run(question, message_history=history)` resumes a conversation; pydantic-ai automatically skips regenerating the system prompt when history is provided
- tool call / tool return pairing is typed and enforced
- the ecosystem has converged: the community read in 2026 is "instructor for simple extraction, pydantic-ai for agents with state." our Selector seam needs state (transcripts, retry with validator feedback, replay).

Continuing to build a custom transcript format on top of instructor would
reinvent what pydantic-ai already does, and would diverge from the proven
transcript-replay pattern. Per first principles item 18 ("reuse the ecosystem
where it has already converged"), we should use the library.

## Decision

**Adopt pydantic-ai as the default llm-backed Selector backend. Use its message history serialization as the `InterviewTranscript` shape. Expose interview capability as an opt-in capture policy and a `.interview()` method on `ExtractionResult`.**

Specifically:

1. **Default llm-backed Selector:** `PydanticAISelector` in `extras/pydantic_ai/`. Replaces `InstructorSelector` as the v1 default. Instructor remains a legitimate alternative users can bring for lighter "extraction as SDK call" use cases — not shipped as a core default.
2. **Add canonical object `InterviewTranscript`** to `docs/architecture.md` §9. Thin wrapper over pydantic-ai's `ModelMessagesTypeAdapter` serialization plus extractx metadata (field_id, instance_key, producer_version, attempt_index, timestamp_ns, message_history_json).
3. **Add `ExecutorPolicy.capture_interview_transcripts: bool = False`.** When true, each soft-compute call at seams D, G.planner, G.resolver, and C.alt (when using pydantic-ai-backed impls) emits an `InterviewTranscript` to a sibling artifact — not embedded in `ReplayArtifact`. Default false for privacy and size.
4. **Add `ExtractionResult.interview(*, field_id, instance_key=None, attempt_index=None, question: str) -> str`** as an end-user public derived method. When transcripts were captured, it rebuilds the agent at the pinned `producer_version`, loads the message history via `ModelMessagesTypeAdapter`, appends the question, and returns the agent's answer. When transcripts were not captured, it raises `InterviewError` with a clear message.
5. **Id-only contract enforcement stays at extractx.** We wrap pydantic-ai's structured output (`output_type=SelectionOutput`) with a contract check that `selected_candidate_ids ⊆ input_candidate_ids`. Pydantic-ai does not know our semantic rule; we do.
6. **Producer version pinning stays at extractx.** Our `producer_version = "{model_id}|{prompt_template_hash}|{code_hash}"` composition is independent of pydantic-ai. An `InterviewError` is raised at rehydration if the current runtime's `producer_version` does not match the captured transcript's — interview is valid only for pinned producers.
7. **`InterviewTranscript` is a sibling artifact, not embedded in `ReplayArtifact`.** Storage, retention, and transport are separate. Per anti-pattern "Transcripts-In-Default-Replay-Artifact," transcripts must never land inside `ReplayArtifact` — they have different privacy posture and size profile.

## Consequences

- **Upside:** the interview capability is inherited from a proven library rather than hand-built. The transcript-replay pattern is already proven for forensic debugging of extraction runs; extractx can model the ergonomics on that shape.
- **Upside:** tool calls, tool returns, and structured outputs are preserved automatically with typed validation. `ModelMessagesTypeAdapter` enforces round-trip correctness at the type level.
- **Upside:** applies first principles item 18 ("reuse the ecosystem") consistently. Rejecting the pattern would duplicate library internals.
- **Upside:** the Selector seam D contract is unchanged. Pydantic-ai sits inside the selector impl; the id-only contract is enforced on top.
- **Upside:** instructor users are not abandoned — they can still bring their own Selector that uses instructor, and the architecture supports it. We just don't ship it as the default.
- **Tradeoff:** pydantic-ai is a heavier dep than instructor. It's a full agent framework rather than an SDK patch. For users who only want "structured output from one shot," the surface is larger than they need.
- **Tradeoff:** we own the id-only contract enforcement on top of pydantic-ai. A future pydantic-ai change that affects `output_type` semantics could require adapter adjustments.
- **Tradeoff:** we own producer_version pinning. Pydantic-ai does not protect us from provider-side model updates that change behavior silently. Our `producer_version` mechanism handles this, but it's extractx's responsibility, not the library's.
- **Tradeoff:** the interview capability is opt-in via a policy flag. Users who enable it inherit storage and privacy obligations for the transcript sibling artifact. We do not embed it in the default replay path to keep those concerns separate.

## Alternatives considered

- **Build a custom `InterviewTranscript` format on top of instructor.** Rejected. This reinvents `ModelMessagesTypeAdapter` with lower fidelity — we would need to hand-roll tool call preservation, message pairing, and structured output round-trips. Pydantic-ai does all of this with typed validation. The maintenance cost would compound as provider SDKs evolve.
- **Ship no interview capability; require users to instrument their own selector for transcripts.** Rejected. The interview pattern is the primary debugging surface for extraction runs at scale — "why did the model pick X" is the question that drives most investigations. Leaving it unsupported pushes every serious user to reimplement transcript replay themselves.
- **Embed transcripts inside `ReplayArtifact` under a policy flag.** Rejected. Transcripts contain prompt content, which means they may contain sensitive document excerpts. `ReplayArtifact` is designed to be portable across systems (CI, regression tests, shared debugging). Transcripts have different privacy posture and size profile. Keeping them as a sibling artifact with independent lifecycle is cleaner. A new anti-pattern "Transcripts-In-Default-Replay-Artifact" codifies this.
- **Use pydantic-ai throughout (also for DocumentAdapter, Executor, GroundedProposalGenerator default).** Rejected. Scope creep. Pydantic-ai is purpose-built for agent runs; it does not belong at the document adapter or executor seams. We adopt it where it fits (the llm-backed Selector at seam D, and the default llm-backed GroundedProposalGenerator at C.alt when that ships), not everywhere.

## Related

- First principles item 18 (ecosystem leverage) in `docs/architecture.md` §2
- [Pydantic AI Message History docs](https://ai.pydantic.dev/message-history/)
- Prior reference implementation of transcript replay and agent interview
- ADR-0001 "pass through operational metadata; no pricing in core" — still applies. `UsageEvent.raw_usage` passthrough is independent of the transcript-capture decision. Pydantic-ai's `result` surface exposes usage which we project into `UsageEvent` with `raw_usage` carrying the provider's native usage object unchanged.
- New anti-pattern `Transcripts-In-Default-Replay-Artifact` (forbids embedding) in `docs/architecture.md` §15
- New canonical object `InterviewTranscript` in `docs/architecture.md` §9
- New end-user public method `ExtractionResult.interview()` in `docs/architecture.md` §13
- New exception `InterviewError` in `docs/architecture.md` §13
- Build-plan thread T11 (LLM-backed selector) now includes interview capture as part of its scope, not as a separate sidecar
