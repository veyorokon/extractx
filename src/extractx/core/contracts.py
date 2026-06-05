"""protocol definitions per docs/architecture.md §4 and §7.

every §4 protocol noun has a named `Protocol` class here so downstream
seams import a stable type surface, even when the protocol's callable
signatures are owned by a later seam task. this avoids the "each new seam
invents its own protocol class" failure mode.

scope of this module:

- named `Protocol` classes for every §4 protocol noun.
- explicit method signatures only where the architecture has already
  defined the callable shape (today: `Prompt.render` / `Prompt.template_hash`
  in §9, and `Budget.record` / `Budget.check` in §7 seam J).
- the `BudgetDecision` allow / deny_with_reason shape referenced by
  `Budget.check` (§7 seam J).

for protocols whose callable surface is owned by a later seam task, the
class body is intentionally empty (`class X(Protocol): ...`). downstream
tasks extend the class with methods; they do not need to redefine the
class.

capability protocols (`LLM`, `NLP`, `Fetch`) are included as named
Protocols too so the execution / runtime task has a consistent import
surface for capability injection (§7 seam J).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from .anchors import SourceRef, SourceSpan
    from .objects import (
        Candidate,
        CandidateSet,
        ClassificationContextSet,
        ContextPack,
        DocumentView,
        ExtractionSpec,
        FieldSpec,
        InstanceCandidateSet,
        InstanceHint,
        InstancePlan,
        InstanceProposerResponse,
        InstanceState,
        Observation,
        RenderedPrompt,
        UsageEvent,
    )
    from .outcomes import (
        Instance,
        NegativeOutcome,
        ProposedField,
        ValidatedField,
        ValidationFailure,
    )

__all__ = [
    "AcceptanceLifecycle",
    "Budget",
    "BudgetDecision",
    "CandidateFilter",
    "CandidateSorter",
    "CandidateStrategy",
    "ClassificationContextStrategy",
    "DocumentAdapter",
    "Fetch",
    "FieldValidator",
    "GroundedProposalGenerator",
    "InstanceResolver",
    "InstancePlanner",
    "InstanceProposer",
    "InstanceValidator",
    "LLM",
    "NLP",
    "Normalizer",
    "Prompt",
    "PromptRecorder",
    "ProposalValidator",
    "Reporter",
    "SelectionAdapter",
    "Selector",
]


# ---------------------------------------------------------------------------
# Budget (seam J) — explicit signatures per §7 seam J
# ---------------------------------------------------------------------------


class BudgetDecision(BaseModel):
    """result of `Budget.check()`.

    minimal typed allow / deny_with_reason shape per docs/architecture.md
    §7 seam J. `allowed=False` carries a `reason`; `allowed=True` carries
    an optional `reason` for observability (e.g. "within 10% of limit").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reason: str | None = None


class Budget(Protocol):
    """see docs/architecture.md §7 seam J.

    receives `UsageEvent`s via `record(event)` and decides allow/deny via
    `check()`. extractx does not ship pricing (ADR-0001); consumers who
    want dollar-denominated budgets subclass or wrap `Budget`.
    """

    def record(self, event: UsageEvent) -> None: ...

    def check(self) -> BudgetDecision: ...


# ---------------------------------------------------------------------------
# Prompt (seam D) — explicit signatures per §9
# ---------------------------------------------------------------------------


class Prompt(Protocol):
    """see docs/architecture.md §9.

    llm-backed selectors render their prompt via a `Prompt` implementation;
    `template_hash` forms part of `producer_version`.
    """

    def render(
        self,
        field_spec: FieldSpec,
        candidate_summaries: tuple[Candidate, ...],
        context_pack: ContextPack,
        instance_state: InstanceState | None,
    ) -> RenderedPrompt: ...

    @property
    def template_hash(self) -> str: ...


class PromptRecorder(Protocol):
    """Optional soft-compute prompt capture surface.

    Prompt capture belongs at the producer boundary, after a selector renders a
    `RenderedPrompt` and before a provider sends it to the model. Recorders
    should be content-addressed where possible so repeated prompts dedupe.
    """

    def record(self, rendered: RenderedPrompt, *, seam: str) -> str: ...


# ---------------------------------------------------------------------------
# seam-owned protocols — bodies intentionally empty.
#
# downstream seam tasks extend each class with methods. keeping the import
# surface stable now avoids "each new seam invents its own protocol class."
# ---------------------------------------------------------------------------


class DocumentAdapter(Protocol):
    """see docs/architecture.md §7 seam A and ADR-0006.

    `adapt(raw_bytes, source_ref) -> DocumentView` is the single seam-A
    callable. adapters are synchronous; the executor bridges to a thread
    pool where asyncio is required. the two subcontracts (linearizable /
    paginated-visual) are declared implicitly via the `text_anchor_space`
    of the spans the adapter produces; an adapter must not mix subcontracts
    within a single `DocumentView` (ADR-0006).
    """

    def adapt(self, raw_bytes: bytes, source_ref: SourceRef) -> DocumentView: ...


class CandidateStrategy(Protocol):
    """see docs/architecture.md §7 seam C.

    deterministic candidate-enumeration seam. `generate(...)` is pure over
    `(field_spec, document_view, instance_hint)` and returns a canonical,
    full `CandidateSet`. strategies run only when `FieldSpec.strategy_bindings`
    names them; patterns / inputs come from `StrategyBinding.params`, not
    from `FieldSpec.description` or `ValueKind`. `instance_hint` flows into
    `CandidateSet.instance_hint` even when a strategy does not materially
    narrow generation by it.
    """

    def generate(
        self,
        field_spec: FieldSpec,
        document_view: DocumentView,
        instance_hint: InstanceHint | None = None,
    ) -> CandidateSet: ...


class ClassificationContextStrategy(Protocol):
    """non-selectable context retrieval seam for category selectors.

    Strategies produce grounded context windows that selectors may inspect
    while choosing among bounded label candidates. They do not produce
    selectable candidates.
    """

    def generate(
        self,
        field_spec: FieldSpec,
        document_view: DocumentView,
    ) -> ClassificationContextSet: ...


class CandidateFilter(Protocol):
    """see docs/architecture.md §7 seam C.filter."""

    def apply(self, candidate_set: CandidateSet) -> CandidateSet: ...


class CandidateSorter(Protocol):
    """see docs/architecture.md §14 / ADR-0005.

    reorder-only; truncation is a strategy decision, not a sorter behavior.
    callable surface lands with the candidate-sorter task.
    """


class GroundedProposalGenerator(Protocol):
    """see docs/architecture.md §7 seam C.alt.

    optional alternate to C + D; callable surface lands with the seam-C.alt
    task.
    """


class InstanceProposer(Protocol):
    """see docs/architecture.md §7 seam G.proposer.

    Multi-instance extraction only. Proposers receive a bounded
    `InstanceCandidateSet` and return selected instance ids. They do not
    assign fields, author values, or create domain identifiers.
    """

    def propose(
        self,
        document_view: DocumentView,
        spec: ExtractionSpec,
        candidate_set: InstanceCandidateSet,
    ) -> InstanceProposerResponse: ...


class Selector(Protocol):
    """see docs/architecture.md §7 seam D.

    primary soft-compute seam. `select(...)` is the sole callable surface
    in phase 1: sync, deterministic from the protocol's perspective. an
    impl may itself be algorithmic or llm-backed — the protocol does not
    distinguish. id-only enforcement (`selected_candidate_ids ⊆ input
    candidate_ids`) is applied by the extractx selector boundary on top of
    the impl's raw output, so llm-backed selectors reuse the same
    enforcement path.

    the parameter type for the candidate view is the full `CandidateSet`
    produced by seam C. any internal projection (e.g. the "candidate
    summaries" the architecture describes for prompt rendering) is derived
    by the selector impl and not part of the protocol surface in phase 1.
    """

    def select(
        self,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        context_pack: ContextPack,
        instance_state: InstanceState | None = None,
        *,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> Observation: ...


class BatchSelector(Protocol):
    """see ADR-0023.

    Batch selector is the multi-field sibling of `Selector`: one producer
    call emits canonical `Observation` objects for several bounded
    `CandidateSet`s. It remains an id-only seam; values and spans still flow
    from selected candidates through seam E/F.
    """

    def select_many(
        self,
        *,
        spec: ExtractionSpec,
        candidate_sets: tuple[CandidateSet, ...],
        context_pack: ContextPack,
        instance_state: InstanceState | None = None,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> tuple[Observation, ...]: ...


class SelectionAdapter(Protocol):
    """see docs/architecture.md §7 seam E.

    cardinality-aware adapter from `Observation + CandidateSet + FieldSpec`
    into `tuple[ProposedField, ...] | NegativeOutcome` per the seam-E
    cardinality table. sync, pure, and deterministic: the adapter does
    not normalize, validate, call an llm, or mutate lifecycle objects.

    the callable surface is a single `adapt(...)` method; alternate shapes
    (async, batched, `UsageEvent`-emitting) are out of scope in phase 1.
    """

    def adapt(
        self,
        observation: Observation,
        candidate_set: CandidateSet,
        field_spec: FieldSpec,
    ) -> tuple[ProposedField, ...] | NegativeOutcome: ...


class InstancePlanner(Protocol):
    """see docs/architecture.md §7 seam G.planner.

    phase-1 callable surface: `plan(document_view, spec,
    boundary_anchor_spans=()) -> InstancePlan | NegativeOutcome`.

    the planner consumes a real `DocumentView`, the `ExtractionSpec`, and
    a tuple of advisory `boundary_anchor_spans` gathered upstream by the
    iterative pre-plan C->D flow (architecture §7 seam G.planner and §11
    iterative pseudocode). it returns either:

    - an `InstancePlan` on success, with tentative `InstanceGroupingKey`s and
      `GroupingEvidence(stage="planned", ...)`, or
    - a typed `NegativeOutcome(category="planning", ...)` on canonical
      planner failure (e.g. `code="no_tentative_keys"` when no anchors
      can be formed, `code="max_exceeded"` when
      `GroupingPolicy.max_instances` would be violated).

    phase-1 discipline:

    - sync, pure, deterministic from the protocol's perspective
    - no `UsageEvent` emission — algorithmic planners do not emit
      provider usage (soft planners will in a later thread)
    - no `ContextPack` parameter — the architecture-level `bounded
      ContextPack` input is orchestrated by the iterative execution
      substrate in a later thread; phase-1 exposes only the concrete
      `boundary_anchor_spans` shape the pre-plan pseudocode in §11 uses
    """

    def plan(
        self,
        document_view: DocumentView,
        spec: ExtractionSpec,
        boundary_anchor_spans: tuple[SourceSpan, ...] = (),
    ) -> InstancePlan | NegativeOutcome: ...


class InstanceResolver(Protocol):
    """see docs/architecture.md §7 seam G.resolver.

    the single named owner of final instance truth. phase-1 callable
    surface: `resolve(validated_fields, candidate_sets, spec,
    instance_plan=None) -> tuple[Instance, ...]`.

    the resolver consumes all `ValidatedField`s for the run, all
    `CandidateSet`s for the run, the `ExtractionSpec` (for
    `GroupingBinding`, `Cardinality`, and field declaration order), and
    an optional `InstancePlan` from the planner. it returns a tuple of
    `Instance`s with final `InstanceGroupingKey`s assigned under the
    documented precedence rule and `ValidatedField`s promoted to
    `Evidence`s without mutation.

    phase-1 discipline (per ADR-0003):

    - sync, pure, deterministic
    - resolver does **not** invoke `InstanceValidator`s or pydantic
      `model_validator`s — instance-layer validation is canonical under
      seam F layer 3, post-resolution
    - precedence authorities in order: explicit `GroupingBinding` →
      source-anchor continuity → candidate co-occurrence →
      `InstancePlan` tentative priors
    - planner failure (`NegativeOutcome(category="planning", ...)`)
      stays upstream; `instance_plan` is `InstancePlan | None`, never a
      planner union type
    - no `UsageEvent` emission — algorithmic resolvers do not emit
      provider usage (soft resolvers will in a later thread)
    """

    def resolve(
        self,
        validated_fields: tuple[ValidatedField, ...],
        candidate_sets: tuple[CandidateSet, ...],
        spec: ExtractionSpec,
        instance_plan: InstancePlan | None = None,
    ) -> tuple[Instance, ...]: ...


class Normalizer(Protocol):
    """see docs/architecture.md §7 seam F layer 2.

    normalization happens exactly once, at seam F layer 2; callable
    surface lands with the seam-F task.
    """


class FieldValidator(Protocol):
    """see docs/architecture.md §7 seam F layer 2.

    callable surface lands with the seam-F task.
    """


class InstanceValidator(Protocol):
    """see docs/architecture.md §7 seam F layer 3.

    callable surface lands with the seam-F task. G.resolver does not
    invoke instance validators (ADR-0003).
    """


class ProposalValidator(Protocol):
    """see docs/architecture.md §7 seam F.

    internal machinery for the seam-F three-layer pipeline (plugin-public
    `FieldValidator` and `InstanceValidator` live alongside; this protocol
    is extractx-internal per §10). seam F is one internal protocol with
    two methods: `validate(...)` for layers 1+2 (per-`ProposedField`,
    pre-resolver) and `validate_instance(...)` for canonical layer 3
    (per-`Instance`, post-`G.resolver`, exactly once per ADR-0003).

    both methods are sync, pure, deterministic. `document_view` is
    required at `validate(...)` because layer 1 validates each
    `SourceSpan` against `DocumentView.normalized_text` /
    `DocumentView.anchor_map` per ADR-0006. `schema_cls` is caller-held
    runtime context across both methods:

    - at `validate(...)`: when provided, layer 2 runs pydantic coercion
      + `field_validator`s; when `None`, layer 2 runs
      `FieldSpec.validation_binding.normalizer` + `FieldValidator`s.
    - at `validate_instance(...)`: when provided, layer 3 runs pydantic
      `model_validator(mode="after")` on a materialized partial-instance
      view of the resolved `Instance`. when `None`, layer 3 is a
      byte-identical no-op pass-through (manual specs).

    layer 3 is per-instance cross-field, not per-field replay; the
    method consumes the whole `Instance`. layer 3 returns either
    the original `Instance` reference (success / pass-through) or
    a typed `ValidationFailure(layer="instance", ...)` on failure;
    escalation to `NegativeOutcome` is execution-owned and lives on the
    executor.
    """

    def validate(
        self,
        proposed: ProposedField,
        field_spec: FieldSpec,
        document_view: DocumentView,
        schema_cls: type[BaseModel] | None = None,
    ) -> ValidatedField | NegativeOutcome | ValidationFailure: ...

    def validate_instance(
        self,
        instance_result: Instance,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None = None,
    ) -> Instance | ValidationFailure: ...


class AcceptanceLifecycle(Protocol):
    """see docs/architecture.md §7 seam M (optional outer integration).

    extractx never constructs `AcceptanceState` itself; this protocol
    exists for the optional outer-integration seam. callable surface lands
    with the acceptance-lifecycle task (if/when it lands).
    """


class Reporter(Protocol):
    """see docs/architecture.md §7 seam K (OpenTelemetry-semantic).

    write-only from the step's perspective. callable surface mirrors
    `opentelemetry.trace.Tracer` and lands with the seam-K task.
    """


# ---------------------------------------------------------------------------
# capability protocols (§7 seam J)
# ---------------------------------------------------------------------------


class LLM(Protocol):
    """llm capability. callable surface lands with the runtime / execution
    task."""


class NLP(Protocol):
    """nlp capability. callable surface lands with the runtime / execution
    task."""


class Fetch(Protocol):
    """fetch capability. callable surface lands with the runtime /
    execution task."""
