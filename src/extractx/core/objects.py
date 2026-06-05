"""canonical data objects per docs/architecture.md §9.

houses `DocumentView`, `ExtractionSpec`, `FieldSpec` (+ bindings),
`Candidate`, `CandidateSet`, `Observation`, `ContextPack`, `RenderedPrompt`,
internal grouping keys, `InstanceHint`, `InstanceState`, `InstancePlan`,
`GroupingEvidence`, `GroupingPolicy`, `UsageEvent`, `PromptPolicy`,
`SorterBinding`, `CandidateOverflowMetadata`, `ContextBudget`,
`InterviewTranscript`.

ADR coverage:
- ADR-0001: `UsageEvent.raw_usage` is an unshaped `Mapping[str, Any] | None`
  passthrough.
- ADR-0002 / ADR-0004: `InterviewTranscript.field_id: str` is non-optional;
  capture remains field-scoped.
- ADR-0005: `PromptPolicy.candidate_overflow_policy` /
  `candidate_count_bound`; `FieldSpec.sorter_binding`;
  `CandidateOverflowMetadata`; `ContextBudget` minimal shape;
  `ContextPack.candidate_overflow`.
- ADR-0006: `SourceSpan` is imported from `anchors.py` and required at
  construction.
- ADR-0013: structured candidates carry pydantic contract status; text
  candidates do not.

placeholder types owned by later tasks:
- `FieldId` — narrow alias for `str` until `schema/` defines a richer id.
- `SchemaRef` — placeholder pydantic model holding a ref string; shaped by
  the schema-surface task.
- `ValidationReason` — opaque str alias carried in retry feedback until the
  validator seam shapes it.
- `DistanceMetric` — placeholder pydantic model until seam G defines
  concrete metrics.
- `BudgetSpec` — placeholder pydantic model until the execution task lands.
- `ValidationPolicy` — placeholder pydantic model until seam F / policy
  lands.
- `Message` — placeholder pydantic model for `RenderedPrompt.messages`
  until the selector seam defines the real shape.

`ContextPack.prior_proposals` and `InstanceState.accepted_proposals` /
`InstanceState.negatives_so_far` reference `ValidatedField` and
`NegativeOutcome` defined in `outcomes.py`. to avoid an import cycle, the
models carry these fields typed as forward string references and
`outcomes.py` calls `model_rebuild()` on both once the lifecycle types
exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .anchors import AnchorMap, SourceRef, SourceSpan
from .cardinality import Cardinality
from .filters import FilterExpr
from .value_kinds import ValueKind

if TYPE_CHECKING:
    # only imported for static type checkers; resolved at runtime via
    # `model_rebuild` in `outcomes.py`.
    from .outcomes import NegativeOutcome, ValidatedField

__all__ = [
    "BudgetSpec",
    "Candidate",
    "ClassificationContextBinding",
    "ClassificationContextOverflowMetadata",
    "ClassificationContextSet",
    "ClassificationContextWindow",
    "CandidateOverflowMetadata",
    "CandidateSet",
    "ConstraintValue",
    "ContextBudget",
    "ContextPack",
    "DistanceMetric",
    "DocumentView",
    "ExpectedConstraint",
    "ExtractionSpec",
    "FilterBinding",
    "FieldId",
    "FieldSpec",
    "GroupingBinding",
    "GroupingDiscriminator",
    "GroupingEvidence",
    "GroupingPolicy",
    "InstanceCandidate",
    "InstanceCandidateSet",
    "InstanceHint",
    "InstanceGroupingKey",
    "InstancePlan",
    "InstanceProposerBinding",
    "InstanceProposerResponse",
    "InstanceState",
    "InterviewTranscript",
    "Message",
    "Observation",
    "PredicateConstraint",
    "PromptBinding",
    "PromptPolicy",
    "ProviderResult",
    "RangeConstraint",
    "RenderedPrompt",
    "SchemaRef",
    "SelectorBinding",
    "SetConstraint",
    "SorterBinding",
    "StructuralFailure",
    "StructuralStatus",
    "StrategyBinding",
    "UsageEvent",
    "ValidationBinding",
    "ValidationPolicy",
    "ValidationReason",
]

T = TypeVar("T")


# ---------------------------------------------------------------------------
# placeholder / opaque types owned by later seam tasks
# ---------------------------------------------------------------------------


type FieldId = str
"""alias for `FieldSpec.field_id` until `schema/` defines a richer type."""


type ValidationReason = str
"""opaque string carried in `ContextPack.retry_feedback`.

shaped by the validator seam in a later task."""


type ConstraintValue = str | int | float | bool
"""JSON-safe scalar used in structured candidate contract diagnostics."""


class SchemaRef(BaseModel):
    """reference to the source schema class for `ExtractionSpec`.

    narrow placeholder until the schema-surface task defines a richer
    reference shape (content hash, module path, class name, …).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str


class SetConstraint(BaseModel):
    """expected set-membership constraint for a structured candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["set"] = "set"
    allowed: tuple[ConstraintValue, ...]


class RangeConstraint(BaseModel):
    """expected inclusive range constraint for a structured candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["range"] = "range"
    lo: ConstraintValue | None = None
    hi: ConstraintValue | None = None
    lo_inclusive: bool = True
    hi_inclusive: bool = True


class PredicateConstraint(BaseModel):
    """expected named predicate constraint for a structured candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["predicate"] = "predicate"
    name: str


type ExpectedConstraint = Annotated[
    SetConstraint | RangeConstraint | PredicateConstraint,
    Field(discriminator="kind"),
]
"""typed expected-constraint kernel for structured candidate failures."""


class StructuralFailure(BaseModel):
    """one pydantic contract failure for a structured candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    actual: ConstraintValue
    expected: ExpectedConstraint


class StructuralStatus(BaseModel):
    """pydantic contract result attached to structured-source candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    contract_id: str
    failures: tuple[StructuralFailure, ...] = ()

    @model_validator(mode="after")
    def _enforce_failure_shape(self) -> StructuralStatus:
        if self.passed and self.failures:
            raise ValueError("StructuralStatus.passed=True requires failures == ()")
        if not self.passed and not self.failures:
            raise ValueError("StructuralStatus.passed=False requires at least one failure")
        return self


class DistanceMetric(BaseModel):
    """placeholder for concrete distance metrics used by `GroupingBinding`.

    shaped by seam G (`instances/`). carried through core today as a
    typed container so `GroupingBinding.distance_metric` is strongly typed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    params: Mapping[str, Any] = Field(default_factory=dict)


class BudgetSpec(BaseModel):
    """placeholder for the budget policy carried inside `ExtractionSpec`.

    shaped by the execution / budget task. kept minimal and frozen so
    current core can quote it from `ExtractionSpec.budget` without
    inventing fields that belong to later surfaces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_calls: int | None = None


class ValidationPolicy(BaseModel):
    """placeholder for spec-wide validator policy carried in `ExtractionSpec`.

    shaped by seam F / executor policy. kept minimal so current core can
    quote it without pre-deciding the richer knobs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    on_validation_failure: str | None = None


class Message(BaseModel):
    """placeholder message type for `RenderedPrompt.messages`.

    shaped by the selector seam (pydantic-ai message shape). carried here
    as a typed container so `RenderedPrompt` is strongly typed today.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str
    content: str


# ---------------------------------------------------------------------------
# source view
# ---------------------------------------------------------------------------


class DocumentView(BaseModel):
    """see docs/architecture.md §9 and seam A contract.

    `anchor_map` is carried as a minimal typed container today; the richer
    lookup / inversion api lands with seam A.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    normalized_text: str
    anchor_map: AnchorMap
    source_ref: SourceRef
    metadata: Mapping[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# bindings and policies
# ---------------------------------------------------------------------------


class StrategyBinding(BaseModel):
    """see docs/architecture.md §9."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    cls: type
    params: Mapping[str, Any] = Field(default_factory=dict)
    kind: Literal["candidate", "grounded_proposal"]


class ValidationBinding(BaseModel):
    """see docs/architecture.md §9.

    `normalizer` and `field_validators` are carried as opaque callables;
    their protocol definitions live in `contracts.py` and are not load-
    bearing at the object-layer shape level (they do not constrain the
    container type).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    normalizer: Any | None = None
    field_validators: tuple[Any, ...] = ()


class GroupingBinding(BaseModel):
    """see docs/architecture.md §9."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["boundary_defining", "boundary_consuming", "neutral"]
    distance_metric: DistanceMetric


class PromptBinding(BaseModel):
    """see docs/architecture.md §9."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str
    params: Mapping[str, Any] = Field(default_factory=dict)


class FilterBinding(BaseModel):
    """field-level candidate filter expression.

    Filters refine a generated `CandidateSet` before selection. The expression
    is a composed typed AST rather than a callable so specs, summaries, and replay
    artifacts can carry the exact policy shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    expr: FilterExpr


class SelectorBinding(BaseModel):
    """per-field selector binding for seam D.

    This is distinct from `PromptBinding`: prompt choice describes how a
    selector is asked; selector binding describes which producer owns the
    decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    cls: type
    params: Mapping[str, Any] = Field(default_factory=dict)


class InstanceProposerBinding(BaseModel):
    """spec-level binding for multi-instance proposal.

    `Cardinality.ONE` does not use a proposer. `Cardinality.MANY`
    requires this binding before extraction begins.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    cls: type
    params: Mapping[str, Any] = Field(default_factory=dict)


class SorterBinding(BaseModel):
    """see docs/architecture.md §9 (ADR-0005).

    `cls` is carried as an opaque `type` today; the `CandidateSorter`
    protocol lives in `contracts.py`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    cls: type
    params: Mapping[str, Any] = Field(default_factory=dict)


class ClassificationContextBinding(BaseModel):
    """selector-input context strategy binding for category fields.

    Classification context is non-selectable evidence rendered into selector
    prompts. This binding is intentionally separate from `StrategyBinding`,
    which produces selectable candidates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    cls: type
    params: Mapping[str, Any] = Field(default_factory=dict)


class PromptPolicy(BaseModel):
    """spec-wide prompt policy.

    see docs/architecture.md §9 (ADR-0005, ADR-0025).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_overflow_policy: Literal["fail", "truncate_sorted"] = "fail"
    candidate_count_bound: int | None = None
    selector_prompt_max_chars: int | None = Field(default=None, gt=0)


class CandidateOverflowMetadata(BaseModel):
    """signal attached to `ContextPack.candidate_overflow` when strategy has
    bounded the selector input. see ADR-0005 §3."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_candidate_count: int
    presented_candidate_count: int
    sorter_id: str
    overflow_policy: Literal["truncate_sorted"]


class ContextBudget(BaseModel):
    """runtime/prompt-size bound carried in `ContextPack.bounds`.

    orthogonal to `PromptPolicy.candidate_count_bound` per ADR-0005.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_prompt_chars: int | None = None
    max_tokens: int | None = None


class GroupingPolicy(BaseModel):
    """spec-wide grouping policy. see docs/architecture.md §9."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default_distance_metric: DistanceMetric
    allow_parallel_instances: bool = False
    max_instances: int | None = None
    merge_threshold: float | None = None


# ---------------------------------------------------------------------------
# FieldSpec and ExtractionSpec
# ---------------------------------------------------------------------------


class FieldSpec(BaseModel):
    """see docs/architecture.md §9 (core + composable bindings).

    immutable; downstream seams import and quote `FieldSpec` without
    mutating it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    field_id: FieldId
    description: str
    value_kind: ValueKind
    cardinality: Cardinality
    priority: int = 0
    depends_on: tuple[FieldId, ...] = ()
    python_type: type
    literal_values: tuple[str, ...] = ()

    strategy_bindings: tuple[StrategyBinding, ...] = ()
    validation_binding: ValidationBinding | None = None
    grouping_binding: GroupingBinding | None = None
    prompt_binding: PromptBinding | None = None
    filter_binding: FilterBinding | None = None
    selector_binding: SelectorBinding | None = None
    sorter_binding: SorterBinding | None = None


class InstanceCandidate(BaseModel):
    """bounded candidate instance proposed before LLM instance selection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str
    instance_type: str
    label: str | None = None
    anchor_candidate_ids: tuple[str, ...] = ()
    anchor_spans: tuple[SourceSpan, ...] = ()
    context: str = ""


class InstanceCandidateSet(BaseModel):
    """bounded set of extraction instances available to an instance proposer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    instance_type: str
    candidates: tuple[InstanceCandidate, ...]


class InstanceProposerResponse(BaseModel):
    """narrow id-only output from an instance proposer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected_instance_ids: tuple[str, ...]
    reason: str | None = None


class ExtractionSpec(BaseModel):
    """see docs/architecture.md §9 and seam B contract.

    immutable at runtime. `version` is expected to be a stable content hash
    produced by the spec-loader task (`schema/from_pydantic.py`); this
    module does not recompute the hash itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: tuple[FieldSpec, ...]
    instance_type: str = "ExtractionInstance"
    instance_cardinality: Cardinality = Cardinality.ONE
    instance_proposer_binding: InstanceProposerBinding | None = None
    prompt_policy: PromptPolicy
    validation_policy: ValidationPolicy
    grouping_policy: GroupingPolicy
    budget: BudgetSpec
    version: str
    source_schema_ref: SchemaRef | None = None

    @model_validator(mode="after")
    def _validate_instance_proposer_contract(self) -> ExtractionSpec:
        if self.instance_cardinality is Cardinality.MANY and self.instance_proposer_binding is None:
            raise ValueError(
                "ExtractionSpec.instance_proposer_binding is required when "
                "instance_cardinality=Cardinality.MANY",
            )
        if (
            self.instance_cardinality is Cardinality.ONE
            and self.instance_proposer_binding is not None
        ):
            raise ValueError(
                "ExtractionSpec.instance_proposer_binding is not used when "
                "instance_cardinality=Cardinality.ONE",
            )
        return self

    @classmethod
    def from_pydantic(
        cls,
        schema_cls: Any,
        *,
        instance_type: str | None = None,
        instance_cardinality: Cardinality = Cardinality.ONE,
        instance_proposer_binding: InstanceProposerBinding | None = None,
        prompt_policy: PromptPolicy | None = None,
        validation_policy: ValidationPolicy | None = None,
        grouping_policy: GroupingPolicy | None = None,
        budget: BudgetSpec | None = None,
    ) -> ExtractionSpec:
        """build an `ExtractionSpec` from a pydantic `BaseModel` subclass.

        `schema_cls` is typed as `Any` at the public boundary so that
        non-BaseModel inputs surface a typed `SpecError` rather than an
        unchecked attribute error. thin classmethod surface over
        `extractx.schema.from_pydantic`; the import is lazy to keep the
        core → schema dependency direction intact. see
        docs/architecture.md §12 and seam B contract.
        """

        # lazy import avoids a core → schema cycle at module-import time.
        from ..schema.from_pydantic import from_pydantic as _from_pydantic

        del cls  # classmethod binding is not used; the target class is fixed.
        return _from_pydantic(
            schema_cls,
            instance_type=instance_type,
            instance_cardinality=instance_cardinality,
            instance_proposer_binding=instance_proposer_binding,
            prompt_policy=prompt_policy,
            validation_policy=validation_policy,
            grouping_policy=grouping_policy,
            budget=budget,
        )


# ---------------------------------------------------------------------------
# candidates / selection / context
# ---------------------------------------------------------------------------


class Candidate(BaseModel):
    """see docs/architecture.md §9 seam C."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    text: str
    source_kind: Literal["structured", "text"] = "text"
    source_id: str = "text"
    source_span: SourceSpan
    evidence_spans: tuple[SourceSpan, ...] = ()
    context: str = ""
    context_span: SourceSpan | None = None
    normalized_span: SourceSpan | None = None
    entity_type: str | None = None
    normalized_hint: Any | None = None
    structured_payload: Mapping[str, Any] | None = None
    structural_status: StructuralStatus | None = None

    @model_validator(mode="after")
    def _enforce_source_kind_contract(self) -> Candidate:
        if self.source_kind == "text" and self.structural_status is not None:
            raise ValueError("text candidates must not carry structural_status")
        if self.source_kind == "structured" and self.structural_status is None:
            raise ValueError("structured candidates must carry structural_status")
        return self


class InstanceGroupingKey(BaseModel):
    """internal resolver/planner grouping key.

    ADR-0008 folds the public instance handle into `Instance.instance_id`.
    The planner and resolver still need a structured grouping shape while
    phase-1 deterministic grouping exists, so that shape stays internal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: str
    ordinal: int
    group_anchors: tuple[SourceSpan, ...]


type InstanceHint = InstanceGroupingKey


class CandidateSet(BaseModel):
    """see docs/architecture.md §9 seam C.

    canonical output of seam C; `G.resolver` and `ReplayArtifact` consume
    the full set unchanged per ADR-0005.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: FieldId
    document_id: str
    instance_hint: InstanceHint | None = None
    candidates: tuple[Candidate, ...]
    strategy_id: str


class ClassificationContextOverflowMetadata(BaseModel):
    """budget signal for classification context retrieval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_window_count: int
    presented_window_count: int
    max_windows: int | None = None
    max_total_chars: int | None = None
    overflow_policy: Literal["truncate_ranked"]


class ClassificationContextWindow(BaseModel):
    """non-selectable grounded context shown to a category selector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    window_id: str
    field_id: FieldId
    text: str
    source_kind: Literal["text"] = "text"
    source_id: str = "text"
    source_span: SourceSpan
    matched_terms: tuple[str, ...] = ()
    strategy_id: str
    rank: int
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class ClassificationContextSet(BaseModel):
    """bounded non-selectable evidence packet for category classification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: FieldId
    document_id: str
    strategy_id: str
    windows: tuple[ClassificationContextWindow, ...]
    overflow: ClassificationContextOverflowMetadata | None = None

    @model_validator(mode="after")
    def _check_context_set_shape(self) -> ClassificationContextSet:
        for window in self.windows:
            if window.field_id != self.field_id:
                raise ValueError(
                    "ClassificationContextSet.windows[].field_id must match field_id",
                )
            if window.strategy_id != self.strategy_id:
                raise ValueError(
                    "ClassificationContextSet.windows[].strategy_id must match strategy_id",
                )
        return self


class ContextPack(BaseModel):
    """see docs/architecture.md §9.

    `candidate_overflow` is `None` when the selector is seeing the full
    `CandidateSet` from seam C; non-`None` when the strategy bounded the
    view (ADR-0005).

    `prior_proposals` is typed as a forward string reference to avoid an
    import cycle with `outcomes.py`; `outcomes.py` resolves it via
    `ContextPack.model_rebuild()` once the `ValidatedField` type exists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_description: str
    document_summary: str
    field_context: Mapping[FieldId, str] = Field(default_factory=dict)
    prior_proposals: tuple[ValidatedField, ...] = ()
    retry_feedback: tuple[ValidationReason, ...] = ()
    bounds: ContextBudget = Field(default_factory=ContextBudget)
    candidate_overflow: CandidateOverflowMetadata | None = None
    classification_context_by_field: Mapping[FieldId, ClassificationContextSet] = Field(
        default_factory=dict,
    )


class Observation(BaseModel):
    """seam-D grounded decision tuple.

    Canonical ADR-0008 fields are `instance_id`, `field_id`, `evidence_id`,
    `abstain`, `reason`, and `producer_version`. `selected_candidate_ids`
    remains the selector-to-cardinality handoff for multi-candidate field
    cardinality; `evidence_id` mirrors the sole selected id when exactly one
    candidate is selected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str | None = None
    field_id: FieldId | None = None
    evidence_id: str | None = None
    abstain: bool = False
    outcome: Literal["SELECTED", "AMBIGUOUS", "ABSTAINED", "NO_CANDIDATES"]
    selected_candidate_ids: tuple[str, ...]
    reason: str | None = None
    producer_version: str

    @model_validator(mode="after")
    def _check_observation_shape(self) -> Observation:
        if self.abstain and self.selected_candidate_ids:
            raise ValueError(
                "Observation: abstain=True requires empty selected_candidate_ids",
            )
        if self.evidence_id is not None and self.evidence_id not in self.selected_candidate_ids:
            raise ValueError(
                "Observation: evidence_id must be one of selected_candidate_ids",
            )
        if self.evidence_id is None and len(self.selected_candidate_ids) == 1:
            object.__setattr__(self, "evidence_id", self.selected_candidate_ids[0])
        return self


class RenderedPrompt(BaseModel):
    """see docs/architecture.md §9.

    `messages` is carried as `tuple[Message, ...]` where `Message` is a
    placeholder until the selector seam locks the pydantic-ai-shaped
    message container.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: tuple[Message, ...]
    structured_output_schema: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# usage / interview
# ---------------------------------------------------------------------------


class UsageEvent(BaseModel):
    """see docs/architecture.md §9 and ADR-0001.

    `raw_usage` is pass-through: the provider's native usage object,
    unshaped. extractx never reshapes it (principle 21 / anti-pattern
    `Reshape-Operational-Metadata`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    producer_version: str
    operation: str | None = None
    field_id: FieldId | None = None
    instance_id: str | None = None
    model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None
    response_id: str | None = None
    soft_call_identity: str | None = None
    timestamp_ns: int
    raw_usage: Mapping[str, Any] | None = None
    raw_response_metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderResult[T]:
    """provider output plus optional operational metadata.

    Soft-compute adapters may return this envelope instead of returning a
    bare structured output. Core selector/proposer contracts still validate
    `output`; usage remains operational metadata and is never evidence.
    """

    output: T
    usage_event: UsageEvent | None = None


class InterviewTranscript(BaseModel):
    """see docs/architecture.md §9 and ADR-0002 / ADR-0004.

    field-scoped by design (ADR-0004): `field_id` is non-optional and the
    capture surface narrows to seams D and C.alt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: FieldId
    instance_key: InstanceGroupingKey | None = None
    attempt_index: int
    producer_version: str
    message_history_json: str
    timestamp_ns: int


# ---------------------------------------------------------------------------
# instance lifecycle (core data shapes only; lifecycle flow lives in later seams)
# ---------------------------------------------------------------------------


class GroupingDiscriminator(BaseModel):
    """typed diagnostic for why an instance was separated from siblings.

    This is not domain identity. Consumers derive business ids from sealed
    `Evidence`; discriminators only expose resolver/planner rationale in a
    stable shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: FieldId
    candidate_id_refs: tuple[str, ...] = ()
    authority: Literal[
        "boundary_defining",
        "source_anchor_continuity",
        "candidate_cooccurrence",
        "instance_plan_prior",
    ]


class GroupingEvidence(BaseModel):
    """see docs/architecture.md §9.

    stage-tagged and unified across planner and resolver.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: Literal["planned", "resolved"]
    anchor_spans: tuple[SourceSpan, ...]
    discriminators: tuple[GroupingDiscriminator, ...] = ()
    clustering_signals: Mapping[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    producer_version: str


class InstanceState(BaseModel):
    """see docs/architecture.md §9.

    versioned, immutable per version. each `with_*` transition lives in
    the iterative-strategy task and returns a new `InstanceState` rather
    than mutating this one.

    `accepted_proposals` and `negatives_so_far` are typed as forward
    string references to avoid an import cycle with `outcomes.py`;
    `outcomes.py` resolves them via `InstanceState.model_rebuild()`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_key: InstanceGroupingKey
    version: int
    accepted_proposals: tuple[ValidatedField, ...] = ()
    negatives_so_far: tuple[NegativeOutcome, ...] = ()
    unresolved_fields: tuple[FieldId, ...] = ()
    grouping_anchors: tuple[SourceSpan, ...] = ()

    @model_validator(mode="after")
    def _check_version(self) -> InstanceState:
        if self.version < 0:
            raise ValueError(
                f"InstanceState: version must be >= 0, got {self.version}",
            )
        return self


class InstancePlan(BaseModel):
    """see docs/architecture.md §9 seam G.planner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tentative_keys: tuple[InstanceGroupingKey, ...]
    grouping_evidence: GroupingEvidence
    producer_version: str | None = None
