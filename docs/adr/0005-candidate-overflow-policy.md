# ADR-0005: Candidate overflow policy; strategy-owned bounded selector view

**Status:** Accepted
**Date:** 2026-04-20

## Context

During T0 doc-integrity review, a latent hidden-policy gap surfaced between seams C and D:

- `docs/architecture.md` §7 seam C: `CandidateGenerator` emits the full `CandidateSet` — "no dedup by normalized value, ever."
- `docs/architecture.md` §15 anti-pattern `Pre-Selection Filter`: "no filter seam between C and D; `CandidateSorter` reorders without dropping."
- `docs/architecture.md` §7 seam D: selector consumes `CandidateSet` summaries.

The architecture forbids a lossy filter between C and D and blesses `CandidateSorter` as reorder-only. But it does not state what happens when a `CandidateSet` exceeds the selector's prompt capacity. `CandidateSorter` reorders but does not drop; if 10k candidates don't fit in a selector's context window, reordering alone is insufficient. The selector either silently truncates (Pre-Selection Filter at the selector boundary — the exact anti-pattern), fails without structure, or invents private handling.

Every selector implementation would invent its own overflow behavior; the rule against pre-selection filters becomes a doc-level promise that implementations routinely violate. Replay determinism degrades subtly — same C output could produce different selections across selectors with different internal truncation heuristics. Forensic analysis cannot distinguish "selector missed X" from "C didn't propose X."

## Decision

**Overflow policy is declared at the spec level (`ExtractionSpec.prompt_policy`) and executed by the strategy before invoking seam D. `CandidateSet` remains canonical and unchanged. Selector receives a bounded view signaled explicitly via `ContextPack.candidate_overflow: CandidateOverflowMetadata | None`.**

Specifically:

1. **`PromptPolicy` gains two fields** (and is minimally defined if currently implicit):
   - `candidate_overflow_policy: Literal["fail", "truncate_sorted"] = "fail"`
   - `candidate_count_bound: int | None = None`
2. **New canonical object `CandidateOverflowMetadata`** (plugin-public): carries `source_candidate_count`, `presented_candidate_count`, `sorter_id`, `overflow_policy`. `sorter_id` is a stable versioned identifier following the `code:{code_hash}` discipline used for algorithmic producers (§8).
3. **`ContextPack.candidate_overflow: CandidateOverflowMetadata | None`** — `None` when selector saw the full set; non-`None` when strategy bounded the view.
4. **New composable binding `SorterBinding`** on `FieldSpec.sorter_binding: SorterBinding | None = None`, mirroring the shape of other bindings (`StrategyBinding`, `ValidationBinding`, `GroupingBinding`, `PromptBinding`).
5. **`ContextBudget` minimal shape** (previously referenced but unspecified): `max_prompt_chars`, `max_tokens`. Orthogonal to candidate count — `PromptPolicy.candidate_count_bound` is spec-level policy; `ContextBudget` is runtime/prompt-size bound surface. The two serve different concerns and are intentionally not collapsed.
6. **`CandidateSet` unchanged.** Seam C emits the full canonical output. Only the strategy-constructed selector input may be bounded. `G.resolver` and `ReplayArtifact` continue to consume the full `CandidateSet`.
7. **Default policy `fail`** — matches the repo's fail-loudly discipline. Users explicitly opt in to truncation.
8. **Spec-load validation:** if `candidate_overflow_policy == "truncate_sorted"` and any field has `sorter_binding is None`, spec construction raises `SpecError`.
9. **Strategy pre-D check** applies under both `IndependentStrategy` and `IterativeStrategy`, per C invocation.
10. **Typed failure mode (strategy-emitted):** when policy is `fail` and bound exceeded, strategy emits `NegativeOutcome("selection", "candidate_overflow", field_id=<f>)`. This is a **strategy-emitted** typed failure associated with the selection step — not a seam-D-emitted selection outcome. Seam D is not invoked for that field. Uses existing `"selection"` category — no category-literal change.
11. **Selector soft-duty clause:** selector MAY inspect `ContextPack.candidate_overflow` and condition behavior; MUST NOT fabricate candidate ids outside the presented summaries.
12. **ReplayArtifact** stores `CandidateOverflowMetadata` alongside the Selection record. Replay reconstructs the bounded selector candidate set deterministically from the full `CandidateSet` + `CandidateOverflowMetadata` + sorter identity/version. (This is not a claim about bytewise reconstruction of the full selector prompt render — that depends on the selector's own rendering code; it is the claim about the bounded candidate view.)
13. **`CandidateSorter` contract unchanged** — remains reorder-only. Truncation is a strategy decision, not a sorter behavior.
14. **Interaction with pre-plan phase (ADR-T0d).** During the iterative strategy's pre-plan phase (advisory C→D only for boundary_defining fields), overflow observed emits trace events only and does not produce canonical `NegativeOutcome`s. Canonical overflow negatives arise from canonical per-instance selection flow under the rule above. The pre-plan mechanic owns this distinction; this ADR carries the note so the interaction is visible from both sides.

## Consequences

- **Upside:** hidden policy named and moved to the right ownership boundary. Spec declares intent; strategy enforces; selector sees a typed signal. No selector-local truncation.
- **Upside:** canonical/derived separation preserved. `CandidateSet` is never mutated. The bounded view is constructed fresh per D invocation from canonical inputs + typed metadata.
- **Upside:** default `fail` forces explicit opt-in to truncation, matching the "fail loudly" discipline. Users discover overflow by hitting it, not by silent degradation.
- **Upside:** replay is deterministic for the bounded candidate view — full `CandidateSet` + `sorter_id` + bound reproduces the same presented candidate set.
- **Tradeoff:** users who opt in to `truncate_sorted` must declare a `sorter_binding` on every field. Spec-load fails otherwise. This is deliberate — "truncate without a sort order" is meaningless.
- **Tradeoff:** `PromptPolicy.candidate_overflow_policy` is spec-level, not per-field. Mixed-policy specs are out of scope for v1. If evidence later demonstrates per-field overflow needs, that's a follow-on clarification.
- **Tradeoff:** `candidate_count_bound` is a coarse count-based bound. It does not account for per-candidate summary size variability (one candidate with 10KB of evidence text vs 100 candidates with 100B each). Prompt-char bounding lives in `ContextBudget` separately and is a runtime concern, not spec-level policy. This v1 surface handles the common case; fine-grained prompt-size handling is a future thread if needed.

## Alternatives considered

- **Mutate `CandidateSet` with `truncated_from_count: int | None`.** Rejected. Canonical/derived smear — same type would carry two roles (full output of C; possibly-bounded view fed to D). `G.resolver` and other consumers would inspect the field to know which they're seeing. Fails the opacity lens.
- **Introduce a wrapper selector-input type (`SelectorInputView`) around summaries + metadata.** Viable but adds more plugin-public surface. The `ContextPack.candidate_overflow` approach achieves the same canonical/derived separation with less surface — the summary projection is already implicit at seam D's input.
- **Hierarchical selection (cluster → pick representatives → select within).** Rejected. Cluster pruning is Pre-Selection Filter renamed: candidates in unchosen clusters are silently dropped from consideration. Level-1 summaries lose per-candidate evidence. Fails the same rule it claims to respect.
- **Selector-owned bounded rendering with contract obligation.** Rejected. Pushes the overflow logic one layer deeper into each selector impl. Opaque at seam D but complex inside every selector; tends to spawn its own hidden policy at a different location.
- **C-level capacity caps (candidate strategies cap emission).** Rejected. Contradicts C's "emit everything found" invariant. Different selectors have different capacity; C would have to know downstream capacity.

## Related

- `docs/architecture.md` §2 principle 16 (fail loudly when contracts are violated)
- `docs/architecture.md` §7 seam C (unchanged canonical output); §7 seam D (input contract extended); §7 seam B (spec-load validation)
- `docs/architecture.md` §9 canonical objects (new types and bindings)
- `docs/architecture.md` §14 extensibility map (`CandidateSorter` entry clarified)
- `docs/architecture.md` §15 anti-patterns (`Pre-Selection Filter` row updated)
- T0 review queue (T0b thread)
