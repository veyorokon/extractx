# ADR-0003: Single canonical F.layer3; G.resolver does not invoke validators

**Status:** Accepted
**Date:** 2026-04-20

## Context

During T0 doc-integrity review, a structural contradiction surfaced between seam F and seam G:

- `docs/architecture.md` seam F layer 3 invariant: "layer 3 runs only after G.resolver has assigned final InstanceKeys."
- `docs/architecture.md` seam G.resolver precedence rule authority #4: "cross-field instance-layer validation consistency — the grouping that makes InstanceValidators pass (the resolver may backtrack once if a tentative grouping produces layer-3 failures)."

For authority #4 to operate, G.resolver must invoke layer-3 validators *during* its own decision process — before final InstanceKey assignment. This contradicts the seam F invariant directly. In addition, G.resolver's input contract does not list `InstanceValidator`s as inputs, so the implicit dependency was unnamed.

The combination produced: (a) two seams leaking behavior into each other, (b) G.resolver's input list not sufficient for its stated behavior, (c) F.layer3's single-run invariant contradicted by resolver's potential internal invocation, (d) hidden policy — implementers would invent their own interpretation of the loop.

## Decision

**G.resolver does not invoke instance-layer validators. F.layer3 is the sole canonical instance-layer validation phase, running exactly once per `InstanceResult` that reaches layer 3, post-resolution.**

Specifically:

1. **Remove precedence authority #4** from G.resolver's precedence rule. Authorities become: explicit `GroupingBinding` → source-anchor continuity → candidate co-occurrence → `InstancePlan` tentative scaffolds (renumbered 1–4).
2. **G.resolver does not invoke `InstanceValidator`s or pydantic `model_validator`s.** Instance-layer validation is canonical under seam F layer 3, post-resolution. Resolver does not retry or backtrack based on validator outcomes.
3. **Canonical layer 3 runs exactly once per `InstanceResult` that reaches layer 3**, after G.resolver assigns final `InstanceKey`s. No other seam invokes layer 3 or its constituent validators.
4. **Layer 3 failures route through `ExecutorPolicy`** as `ValidationFailure(layer="instance", ...)`. They do not trigger G.resolver reassignment.
5. **Ambiguous grouping after authorities 1–4 emits `NegativeOutcome("resolution", "ambiguous_grouping", field_id=<affected>, instance_key=<tentative>)`** per affected proposal, attached to the tentative instance with the strongest partial signal from authorities 1–4 (deterministic tie-break via `tentative_key` ordering). Lands in that `InstanceResult.negative_outcomes`. The affected proposal does not become a `ResolvedFieldProposal`. New code under the existing `"resolution"` category — no category-literal change. Document-scope free-floating resolution negatives are not emitted.

## Consequences

- **Upside:** seams F and G become opaque to each other. G.resolver's input list becomes necessary and sufficient for its behavior. F.layer3's single-run invariant holds unambiguously.
- **Upside:** eliminates the hidden double-run / probe-vs-canonical ambiguity. Validators fire exactly once per instance.
- **Upside:** determinism and replay are cleaner — no conditional second validator invocation under backtrack.
- **Upside:** `NegativeOutcome("resolution", "ambiguous_grouping")` is an honest failure mode. Users who hit it can declare a more discriminating `GroupingBinding`, add structural anchors, or provide a custom `InstancePlanner` — each of which is a typed, declared fix rather than an implicit rescue via validator output.
- **Tradeoff:** a class of edge cases where validator-consistency would have been the only viable tie-breaker now surface as `ambiguous_grouping` rather than silent resolution. This is deliberate — the repo's bias is that validators validate, resolvers resolve. Conflating the two trapped policy inside the resolver's implementation.
- **Tradeoff:** users migrating from a prior pattern that relied on validator-guided grouping (e.g., external systems that produced validators specifically to force grouping outcomes) will need to move that intent into `GroupingBinding` or `InstancePlanner` instead.

## Alternatives considered

- **Option B: retain rule #4; name the phase explicitly as "advisory layer-3 probe."** Rejected on opacity grounds. Probe + canonical running the same validator twice preserves capability at the cost of double-run determinism hazards (non-deterministic validators produce different answers; pydantic `model_validator` side effects double) and makes the F/G seam boundary blurry.
- **Option C: introduce a new `GroupingConsistencyValidator` protocol as a narrow subset of layer 3, invoked by resolver advisorily.** Technically honest, but adds a new plugin-public protocol without evidence that validator-guided grouping is a load-bearing capability. Held in reserve for a future thread if real use cases demonstrate the need.
- **Option D: invert — layer 3 runs on tentative groupings, resolver consumes its output.** Rejected. Validating against non-canonical `InstanceKey`s is semantically incoherent; `ValidationFailure(layer="instance")` fires against instances that may not canonically exist.

## Related

- `docs/architecture.md` §7 seam G.resolver (precedence rule, invariants)
- `docs/architecture.md` §7 seam F layer 3 (invariant; failure routing)
- `docs/architecture.md` §15 anti-patterns (`Policy Trapped In Consumer`, `Resolver-As-Truth-Owner`)
- `CODEX.md` seam summary for G.resolver
- T0 review queue (T0a thread)
