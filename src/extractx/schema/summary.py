"""`SpecSummary` — phase-1 round-trip-safe persisted form of `ExtractionSpec`.

per docs/tasks/m9-phase-1-replay-storage-skeleton.md §2 and ADR-0007 §4.

`ExtractionSpec` carries live `python_type` / binding `cls` / callable
references that do not survive json (or any portable serialization)
without a registry. `SpecSummary` is the persisted spec object — every
non-round-trip-safe field becomes a deterministic qualname-string
surrogate. `SpecSummary` is **not** rehydratable back to a runnable
`ExtractionSpec` in phase 1; that requires a class registry and is a
future thread (see drift §3 of the M9 phase-1 brief).

`summarize_spec(spec)` raises `InfrastructureError` (with prefix
`"spec_summary.unsafe_params: ..."`) if any binding's `params` mapping
contains values that are not JSON-safe. that is a defect surfacing,
not a typed negative.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from extractx.core.cardinality import Cardinality
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import (
    BudgetSpec,
    ExtractionSpec,
    GroupingPolicy,
    PromptPolicy,
    SchemaRef,
    ValidationPolicy,
)
from extractx.core.value_kinds import ValueKind

if TYPE_CHECKING:
    from extractx.core.objects import FieldSpec

__all__ = [
    "BindingSummary",
    "FieldSummary",
    "GroupingBindingSummary",
    "PromptBindingSummary",
    "SpecSummary",
    "ValidationBindingSummary",
    "summarize_spec",
]


def _qualname(obj: Any) -> str:
    """compose `f"{module}.{qualname}"` for a class or callable.

    raises `InfrastructureError` with `"spec_summary.unsafe_params: ..."`
    prefix on inputs that lack `__module__` / `__qualname__`. used as the
    deterministic surrogate for live class / callable references in
    `SpecSummary`.
    """

    module = getattr(obj, "__module__", None)
    qualname = getattr(obj, "__qualname__", None)
    if module is None or qualname is None:
        raise InfrastructureError(
            "spec_summary.unsafe_params: cannot derive qualname for "
            f"{obj!r}: missing __module__ / __qualname__",
        )
    return f"{module}.{qualname}"


def _is_json_safe(value: Any) -> bool:
    """match the seam-F layer-1 `_is_json_safe` rule (see
    `extractx.proposals.validation._is_json_safe`).

    json-safe means: `None`, `bool`, `int`, `float`, `str`, or a
    `Mapping[str, Any]` / `Sequence` of the same (recursively). `bytes`,
    pydantic models, custom class instances, sets, tuples-of-non-safe
    are not json-safe.
    """

    if value is None or isinstance(value, bool | int | float | str):
        return True
    if isinstance(value, Mapping):
        mapping_value = cast("Mapping[Any, Any]", value)
        for key, v in mapping_value.items():
            if not isinstance(key, str):
                return False
            if not _is_json_safe(v):
                return False
        return True
    if isinstance(value, bytes | bytearray):
        return False
    if isinstance(value, Sequence):
        seq_value = cast("Sequence[Any]", value)
        return all(_is_json_safe(v) for v in seq_value)
    return False


def _ensure_json_safe_params(
    *,
    field_id: str,
    binding_label: str,
    params: Mapping[str, Any],
) -> Mapping[str, Any]:
    """validate `params` is json-safe and return it as a plain dict.

    raises `InfrastructureError` with `"spec_summary.unsafe_params: ..."`
    on the first non-safe entry — defect surfacing per the brief.
    """

    if not _is_json_safe(params):
        raise InfrastructureError(
            "spec_summary.unsafe_params: field "
            f"{field_id!r} {binding_label} params are not JSON-safe; "
            "phase-1 binding params must be primitives, mappings, or "
            "sequences of the same",
        )
    return dict(params)


# ---------------------------------------------------------------------------
# binding summaries
# ---------------------------------------------------------------------------


class BindingSummary(BaseModel):
    """generic binding summary used for `StrategyBinding` and `SorterBinding`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cls_qualname: str
    params: Mapping[str, Any] = Field(default_factory=dict)
    kind: str | None = None
    """`StrategyBinding.kind` literal; `None` for `SorterBinding` (no kind field)."""


class ValidationBindingSummary(BaseModel):
    """summary of a `ValidationBinding`.

    `normalizer` and `field_validators` are reduced to qualname strings.
    they are **not** re-importable in phase 1.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    normalizer_qualname: str | None = None
    field_validator_qualnames: tuple[str, ...] = ()


class GroupingBindingSummary(BaseModel):
    """summary of a `GroupingBinding`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["boundary_defining", "boundary_consuming", "neutral"]
    distance_metric_name: str
    distance_metric_params: Mapping[str, Any] = Field(default_factory=dict)


class PromptBindingSummary(BaseModel):
    """summary of a `PromptBinding`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str
    params: Mapping[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# field summaries and spec summary
# ---------------------------------------------------------------------------


class FieldSummary(BaseModel):
    """phase-1 round-trip-safe summary of a `FieldSpec`.

    `python_type_qualname` is `f"{cls.__module__}.{cls.__qualname__}"`
    for the field's `python_type`. opaque string; **not** re-imported.

    `value_kind` is carried as the `ValueKind` instance's `name` string.
    `ValueKind` is a registry-backed type (not a pydantic-friendly
    `Enum`), so we persist the registry name and rebuild via
    `ValueKind.register(...)` on read. that mirrors how `FieldSpec`
    already treats `ValueKind` in core / schema.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str
    description: str
    value_kind_name: str
    cardinality: Cardinality
    priority: int
    depends_on: tuple[str, ...]
    python_type_qualname: str

    strategy_binding_summaries: tuple[BindingSummary, ...] = ()
    validation_binding_summary: ValidationBindingSummary | None = None
    grouping_binding_summary: GroupingBindingSummary | None = None
    prompt_binding_summary: PromptBindingSummary | None = None
    filter_binding_summary: Mapping[str, Any] | None = None
    selector_binding_summary: BindingSummary | None = None
    sorter_binding_summary: BindingSummary | None = None

    @property
    def value_kind(self) -> ValueKind:
        """rehydrate the `ValueKind` instance from its registry name."""

        return ValueKind.register(self.value_kind_name)


class SpecSummary(BaseModel):
    """phase-1 round-trip-safe summary of an `ExtractionSpec`.

    persisted at `objects/spec/<spec-version>.json` per ADR-0007 §4. the
    live `ExtractionSpec` is not stored: live `python_type` / binding
    `cls` / callable references survive only as deterministic qualname
    strings here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary_version: Literal["v1"] = "v1"
    spec_version: str
    source_schema_ref: SchemaRef | None = None
    instance_type: str = "ExtractionInstance"
    instance_cardinality: Cardinality = Cardinality.ONE
    instance_proposer_binding_summary: BindingSummary | None = None
    prompt_policy: PromptPolicy
    validation_policy: ValidationPolicy
    grouping_policy: GroupingPolicy
    budget: BudgetSpec
    field_summaries: tuple[FieldSummary, ...]


# ---------------------------------------------------------------------------
# summarize_spec
# ---------------------------------------------------------------------------


def _summarize_field(field_spec: FieldSpec) -> FieldSummary:
    """reduce one `FieldSpec` to a deterministic `FieldSummary`."""

    strategy_summaries: list[BindingSummary] = []
    for index, binding in enumerate(field_spec.strategy_bindings):
        params = _ensure_json_safe_params(
            field_id=field_spec.field_id,
            binding_label=f"strategy_bindings[{index}]",
            params=binding.params,
        )
        strategy_summaries.append(
            BindingSummary(
                cls_qualname=_qualname(binding.cls),
                params=params,
                kind=binding.kind,
            ),
        )

    validation_summary: ValidationBindingSummary | None = None
    if field_spec.validation_binding is not None:
        normalizer = field_spec.validation_binding.normalizer
        validators = field_spec.validation_binding.field_validators
        validation_summary = ValidationBindingSummary(
            normalizer_qualname=_qualname(normalizer) if normalizer is not None else None,
            field_validator_qualnames=tuple(_qualname(v) for v in validators),
        )

    grouping_summary: GroupingBindingSummary | None = None
    if field_spec.grouping_binding is not None:
        gb = field_spec.grouping_binding
        params = _ensure_json_safe_params(
            field_id=field_spec.field_id,
            binding_label="grouping_binding.distance_metric",
            params=gb.distance_metric.params,
        )
        grouping_summary = GroupingBindingSummary(
            role=gb.role,
            distance_metric_name=gb.distance_metric.name,
            distance_metric_params=params,
        )

    prompt_summary: PromptBindingSummary | None = None
    if field_spec.prompt_binding is not None:
        params = _ensure_json_safe_params(
            field_id=field_spec.field_id,
            binding_label="prompt_binding",
            params=field_spec.prompt_binding.params,
        )
        prompt_summary = PromptBindingSummary(
            template_id=field_spec.prompt_binding.template_id,
            params=params,
        )

    selector_summary: BindingSummary | None = None
    selector_binding = getattr(field_spec, "selector_binding", None)
    if selector_binding is not None:
        selector_cls = getattr(selector_binding, "cls", None)
        selector_params = getattr(selector_binding, "params", {})
        if selector_cls is not None:
            params = _ensure_json_safe_params(
                field_id=field_spec.field_id,
                binding_label="selector_binding",
                params=selector_params,
            )
            selector_summary = BindingSummary(
                cls_qualname=_qualname(selector_cls),
                params=params,
                kind=None,
            )

    sorter_summary: BindingSummary | None = None
    if field_spec.sorter_binding is not None:
        params = _ensure_json_safe_params(
            field_id=field_spec.field_id,
            binding_label="sorter_binding",
            params=field_spec.sorter_binding.params,
        )
        sorter_summary = BindingSummary(
            cls_qualname=_qualname(field_spec.sorter_binding.cls),
            params=params,
            kind=None,
        )

    return FieldSummary(
        field_id=field_spec.field_id,
        description=field_spec.description,
        value_kind_name=field_spec.value_kind.name,
        cardinality=field_spec.cardinality,
        priority=field_spec.priority,
        depends_on=tuple(field_spec.depends_on),
        python_type_qualname=_qualname(field_spec.python_type),
        strategy_binding_summaries=tuple(strategy_summaries),
        validation_binding_summary=validation_summary,
        grouping_binding_summary=grouping_summary,
        prompt_binding_summary=prompt_summary,
        filter_binding_summary=(
            None
            if field_spec.filter_binding is None
            else field_spec.filter_binding.model_dump(mode="json")
        ),
        selector_binding_summary=selector_summary,
        sorter_binding_summary=sorter_summary,
    )


def summarize_spec(spec: ExtractionSpec) -> SpecSummary:
    """produce the phase-1 round-trip-safe `SpecSummary` for `spec`.

    the helper raises `InfrastructureError` with prefix
    `"spec_summary.unsafe_params: ..."` if any binding's `params` mapping
    contains values that are not JSON-safe — a defect surfacing, not a
    typed negative.
    """

    field_summaries = tuple(_summarize_field(f) for f in spec.fields)
    instance_proposer_summary: BindingSummary | None = None
    if spec.instance_proposer_binding is not None:
        params = _ensure_json_safe_params(
            field_id="__spec__",
            binding_label="instance_proposer_binding",
            params=spec.instance_proposer_binding.params,
        )
        instance_proposer_summary = BindingSummary(
            cls_qualname=_qualname(spec.instance_proposer_binding.cls),
            params=params,
            kind=None,
        )
    return SpecSummary(
        summary_version="v1",
        spec_version=spec.version,
        source_schema_ref=spec.source_schema_ref,
        instance_type=spec.instance_type,
        instance_cardinality=spec.instance_cardinality,
        instance_proposer_binding_summary=instance_proposer_summary,
        prompt_policy=spec.prompt_policy,
        validation_policy=spec.validation_policy,
        grouping_policy=spec.grouping_policy,
        budget=spec.budget,
        field_summaries=field_summaries,
    )
