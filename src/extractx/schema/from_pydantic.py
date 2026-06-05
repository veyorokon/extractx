"""`ExtractionSpec.from_pydantic` reader per docs/architecture.md §12 and seam B.

construction surface:

- `from_pydantic(Cls)` reads a pydantic `BaseModel` subclass and produces
  an immutable `ExtractionSpec`.
- the function is pure: same class → same `ExtractionSpec` → same
  `spec.version`. no env, filesystem, or network access.

what seam B owns (and what it does *not*):

- infers `Cardinality` and `ValueKind` from type annotations per §12.
- reads typed `ExtractxFieldMetadata` attached by `extract_field(...)`.
- builds `FieldSpec` with default strategy / validation bindings for
  pydantic-backed fields; users can override bindings via `extract_field`.
- validates the dependency graph (via `core.dependencies`) — raises
  `SpecError` on cycles or unknown references.
- applies the ADR-0005 spec-load rule: if
  `PromptPolicy.candidate_overflow_policy == "truncate_sorted"` and any
  `FieldSpec.sorter_binding is None`, raise `SpecError`.
- detects pydantic-as-extractor validators and raises `SpecError` on the
  narrow detectable pattern (see `validators.py`).
- computes `ExtractionSpec.version` as a stable content hash.

what seam B does *not* own (deferred to later-seam tasks):

- strategy selection at seam C — `StrategyBinding.cls` on a from_pydantic-
  derived field is a sentinel (`PydanticDefaultStrategy`); the executor /
  strategy task swaps it for a concrete class later.
- normalization at seam F layer 2 — `ValidationBinding()` carries no
  explicit `Normalizer`; pydantic's own coercion plays that role per §7
  seam F, and the pydantic class is the fallback referred to in the
  "manual `FieldSpec` with `validation_binding=None` and no pydantic
  class" SpecError trigger.
- materialization (`to_pydantic`) — lives in `schema/to_pydantic.py`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..core.cardinality import Cardinality
from ..core.dependencies import validate_dependency_graph
from ..core.exceptions import SpecError
from ..core.objects import (
    BudgetSpec,
    DistanceMetric,
    ExtractionSpec,
    FieldSpec,
    GroupingPolicy,
    InstanceProposerBinding,
    PromptPolicy,
    SchemaRef,
    StrategyBinding,
    ValidationPolicy,
)
from ..core.versions import stable_hash
from ._schema_cls_registry import register_class_by_qualname, register_schema_cls
from .inference import analyze_field_annotation
from .metadata import (
    ExtractxFieldMetadata,
)
from .validators import detect_pydantic_as_extractor

__all__ = ["from_pydantic"]


def from_pydantic(
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

    the `schema_cls` parameter is typed as `Any` intentionally: users may
    reach this entry point with arbitrary inputs (a non-pydantic class,
    `None`, a module, etc.) and the runtime guard below produces a typed
    `SpecError` with a diagnostic message. a stricter annotation would
    shift the rejection from `SpecError` to an unchecked attribute error
    at the first `model_fields` access.

    overrides for the four policy containers are accepted; when omitted,
    the defaults carry zero-cost values (`PromptPolicy()` default is
    `candidate_overflow_policy="fail"` with no bound). users who want
    runtime-bound policies pass them explicitly.

    raises `SpecError` on:

    - cyclic or dangling `depends_on`
    - zero or multiple `ValueKind` markers on an annotation
    - a cardinality / annotation combination outside the §12 table (no
      explicit override provided)
    - a detectable pydantic-as-extractor `field_validator`
    - `PromptPolicy.candidate_overflow_policy == "truncate_sorted"` with
      any field missing a `sorter_binding`
    - `instance_cardinality == Cardinality.MANY` without an
      `instance_proposer_binding`
    - `schema_cls` is not a `BaseModel` subclass
    """

    if not (isinstance(schema_cls, type) and issubclass(schema_cls, BaseModel)):
        raise SpecError(
            f"from_pydantic: expected a pydantic BaseModel subclass; got {schema_cls!r}.",
        )

    # detect pydantic-as-extractor before we build any FieldSpec. failing
    # early keeps the spec-load error message close to the bad pattern.
    detect_pydantic_as_extractor(schema_cls)

    # the pydantic class is the source of field declarations; iterate the
    # declared fields in declaration order (pydantic v2 preserves it in
    # `model_fields`).
    model_fields = schema_cls.model_fields

    field_specs: list[FieldSpec] = []
    for field_id, field_info in model_fields.items():
        metadata = _read_metadata(schema_cls, field_id, field_info)
        # pydantic flattens the outermost `Annotated[...]` into
        # `FieldInfo.metadata`; `rebuild_annotation()` is the public method
        # that reconstructs the full original annotation (Annotated, Optional,
        # list shapes preserved). see pydantic.fields.FieldInfo.
        annotation = field_info.rebuild_annotation()
        type_info = analyze_field_annotation(field_id, annotation)

        # explicit `cardinality=` on extract_field overrides inference.
        cardinality: Cardinality = (
            metadata.cardinality
            if metadata.cardinality is not None
            else type_info.inferred_cardinality
        )

        field_kwargs: dict[str, Any] = {
            "field_id": field_id,
            "description": metadata.description,
            "value_kind": type_info.value_kind,
            "cardinality": cardinality,
            "priority": metadata.priority,
            "depends_on": metadata.depends_on,
            "python_type": type_info.python_type,
            "literal_values": type_info.literal_values,
            "strategy_bindings": _default_strategy_bindings(
                value_kind=type_info.value_kind,
                literal_values=type_info.literal_values,
                explicit_bindings=metadata.strategy_bindings,
            ),
            "validation_binding": metadata.validation_binding,
            "grouping_binding": metadata.grouping_binding,
            "prompt_binding": metadata.prompt_binding,
            "filter_binding": metadata.filter_binding,
            "selector_binding": metadata.selector_binding,
            "sorter_binding": metadata.sorter_binding,
        }

        field_specs.append(
            FieldSpec(
                **field_kwargs,
            ),
        )

    # dependency graph: validate over the closed set of declared field ids.
    edges = {f.field_id: f.depends_on for f in field_specs}
    validate_dependency_graph(edges)

    # policy defaults. `GroupingPolicy` requires a `DistanceMetric`; we
    # supply a sentinel `"default"` metric name so the core model is
    # happy. seam G owns the real metric catalog.
    effective_prompt_policy = prompt_policy if prompt_policy is not None else PromptPolicy()
    effective_validation_policy = (
        validation_policy if validation_policy is not None else ValidationPolicy()
    )
    effective_grouping_policy = (
        grouping_policy
        if grouping_policy is not None
        else GroupingPolicy(default_distance_metric=DistanceMetric(name="default"))
    )
    effective_budget = budget if budget is not None else BudgetSpec()
    effective_instance_type = instance_type if instance_type is not None else schema_cls.__name__

    if instance_cardinality is Cardinality.MANY and instance_proposer_binding is None:
        raise SpecError(
            "ExtractionSpec.instance_proposer_binding is required when "
            "instance_cardinality=Cardinality.MANY",
        )
    if instance_cardinality is Cardinality.ONE and instance_proposer_binding is not None:
        raise SpecError(
            "ExtractionSpec.instance_proposer_binding is not used when "
            "instance_cardinality=Cardinality.ONE",
        )

    # ADR-0005 spec-load rule: truncate_sorted requires a sorter on every field.
    if effective_prompt_policy.candidate_overflow_policy == "truncate_sorted":
        missing = [f.field_id for f in field_specs if f.sorter_binding is None]
        if missing:
            raise SpecError(
                "PromptPolicy.candidate_overflow_policy='truncate_sorted' requires "
                "sorter_binding on every FieldSpec; missing on: "
                f"{sorted(missing)} (see ADR-0005).",
            )

    _validate_category_selector_bindings(field_specs)

    version = _compose_spec_version(
        schema_cls=schema_cls,
        fields=field_specs,
        instance_type=effective_instance_type,
        instance_cardinality=instance_cardinality,
        instance_proposer_binding=instance_proposer_binding,
        prompt_policy=effective_prompt_policy,
        validation_policy=effective_validation_policy,
        grouping_policy=effective_grouping_policy,
        budget=effective_budget,
    )

    spec = ExtractionSpec(
        fields=tuple(field_specs),
        instance_type=effective_instance_type,
        instance_cardinality=instance_cardinality,
        instance_proposer_binding=instance_proposer_binding,
        prompt_policy=effective_prompt_policy,
        validation_policy=effective_validation_policy,
        grouping_policy=effective_grouping_policy,
        budget=effective_budget,
        version=version,
        source_schema_ref=SchemaRef(ref=_schema_class_ref(schema_cls)),
    )
    # M8 phase-1 schema_cls handoff: register the live class under
    # `spec.version` so the executor can resolve it once per run and
    # pass it into seam F's `LayeredProposalValidator.validate(...,
    # schema_cls=...)`. this is internal execution / schema plumbing;
    # `ExtractionSpec.source_schema_ref` remains a stable reference
    # string, not a live class import path. see
    # `docs/tasks/m8-phase-1-serial-independent-vertical-slice.md` §3.
    register_schema_cls(spec.version, schema_cls)
    # M9 phase-2 (replay rehydration): register the live class under
    # its `module.qualname` qualname in the sibling registry so a
    # future manual-spec replay thread can resolve binding `cls`
    # references without a second migration. defensively also register
    # every binding `cls` encountered during the spec build —
    # `StrategyBinding.cls` and `SorterBinding.cls`. the per-thread
    # constraint is "no callable registry" (normalizers /
    # field_validators are not registered here) — manual replay is a
    # follow-on thread.
    register_class_by_qualname(schema_cls)
    for f in field_specs:
        for binding in f.strategy_bindings:
            register_class_by_qualname(binding.cls)
        if f.sorter_binding is not None:
            register_class_by_qualname(f.sorter_binding.cls)
        selector_binding = getattr(f, "selector_binding", None)
        selector_cls = getattr(selector_binding, "cls", None)
        if selector_cls is not None:
            register_class_by_qualname(selector_cls)
    if instance_proposer_binding is not None:
        register_class_by_qualname(instance_proposer_binding.cls)
    return spec


def _default_strategy_bindings(
    *,
    value_kind: object,
    literal_values: tuple[str, ...],
    explicit_bindings: tuple[StrategyBinding, ...],
) -> tuple[StrategyBinding, ...]:
    if explicit_bindings:
        return explicit_bindings
    if getattr(value_kind, "name", None) != "CATEGORY" or not literal_values:
        return ()

    from extractx.candidates.generators.literal_set import LiteralSetCandidateStrategy

    return (
        StrategyBinding(
            cls=LiteralSetCandidateStrategy,
            kind="candidate",
        ),
    )


def _validate_category_selector_bindings(field_specs: list[FieldSpec]) -> None:
    missing = [
        f
        for f in field_specs
        if f.value_kind.name == "CATEGORY"
        and len(f.literal_values) > 1
        and f.selector_binding is None
    ]
    if missing:
        details = ", ".join(
            f"{f.field_id!r} ({len(f.literal_values)} literal arms)" for f in missing
        )
        raise SpecError(
            "category.selector_binding_required: CATEGORY fields with more than "
            "one Literal arm require selector_binding; missing on "
            f"{details}. Bind an LLM selector such as PydanticAISelector, "
            "or use a one-arm Literal when the field is a schema constant.",
        )


def _read_metadata(
    schema_cls: type[BaseModel],
    field_id: str,
    field_info: Any,
) -> ExtractxFieldMetadata:
    """read typed extractx metadata off the pydantic `FieldInfo`.

    the container is carried on `FieldInfo.metadata` (pydantic's per-field
    metadata list) — see `extract_field.py`. recovery is an isinstance
    filter over that list.

    raises `SpecError` when the field was declared without `extract_field`
    — the spec-load contract requires every field to carry typed metadata
    (description at minimum). users with fields they don't want extracted
    can exclude them from the schema; the ExtractionSpec surface is not
    the right place to silently skip.
    """

    raw_metadata_list = getattr(field_info, "metadata", [])
    matches = [m for m in raw_metadata_list if isinstance(m, ExtractxFieldMetadata)]
    if len(matches) == 0:
        raise SpecError(
            f"{schema_cls.__name__}.{field_id}: field must be declared with "
            f"`extract_field(description=...)`; plain `pydantic.Field(...)` is "
            f"not accepted by from_pydantic (see docs/architecture.md §12).",
        )
    if len(matches) > 1:
        raise SpecError(
            f"{schema_cls.__name__}.{field_id}: multiple ExtractxFieldMetadata "
            f"instances attached to the same field; `extract_field` must be called "
            f"exactly once per field.",
        )
    return matches[0]


def _schema_class_ref(cls: type[BaseModel]) -> str:
    """stable reference string for a pydantic class.

    `module.qualname` is stable across runs for the same source. it is
    not a content hash — that role is played by `ExtractionSpec.version`,
    which does hash the field shapes below.
    """

    return f"{cls.__module__}.{cls.__qualname__}"


def _compose_spec_version(
    *,
    schema_cls: type[BaseModel],
    fields: list[FieldSpec],
    instance_type: str,
    instance_cardinality: Cardinality,
    instance_proposer_binding: InstanceProposerBinding | None,
    prompt_policy: PromptPolicy,
    validation_policy: ValidationPolicy,
    grouping_policy: GroupingPolicy,
    budget: BudgetSpec,
) -> str:
    """compute a deterministic content-hash for `ExtractionSpec.version`.

    hashes:
    - each `FieldSpec`'s declaration-visible payload (id, description,
      value_kind name, cardinality, priority, depends_on, python_type
      name)
    - the spec-level policies
    - the pydantic class's json schema (pydantic guarantees stability
      across runs for a given class)
    - the class's module / qualname

    bindings (`strategy_bindings`, `validation_binding`, `grouping_binding`,
    `prompt_binding`, `filter_binding`, `sorter_binding`) are hashed by
    their durable binding payloads, so swapping a strategy impl or filter
    expression without touching the schema still changes `version`.
    """

    # NOTE: we intentionally do not call `schema_cls.model_json_schema()` here.
    # pydantic's json-schema generation walks `FieldInfo.metadata` and the
    # typed extractx metadata containers carry `type` fields (`StrategyBinding.cls`,
    # `SorterBinding.cls`) that are not json-schema-representable. the field
    # payload composed below already captures every declaration-visible shape
    # used by the extractor; the pydantic class's module / qualname pins class
    # identity, and `_field_hash_payload` captures the typed binding shape.
    payload: dict[str, Any] = {
        "module": schema_cls.__module__,
        "qualname": schema_cls.__qualname__,
        "instance_type": instance_type,
        "instance_cardinality": instance_cardinality.value,
        "instance_proposer_binding": (
            None
            if instance_proposer_binding is None
            else _binding_payload(
                cls=instance_proposer_binding.cls,
                params=dict(instance_proposer_binding.params),
                extra={},
            )
        ),
        "prompt_policy": prompt_policy.model_dump(mode="json"),
        "validation_policy": validation_policy.model_dump(mode="json"),
        "grouping_policy": grouping_policy.model_dump(mode="json"),
        "budget": budget.model_dump(mode="json"),
        "fields": [_field_hash_payload(f) for f in fields],
    }
    return stable_hash(payload)


def _field_hash_payload(f: FieldSpec) -> dict[str, Any]:
    """return the json-safe payload hashed into `ExtractionSpec.version` for a field."""

    return {
        "field_id": f.field_id,
        "description": f.description,
        "value_kind": f.value_kind.name,
        "cardinality": f.cardinality.value,
        "priority": f.priority,
        "depends_on": list(f.depends_on),
        "python_type": _type_ref(f.python_type),
        "strategy_bindings": [
            _binding_payload(
                cls=binding.cls,
                params=dict(binding.params),
                extra={"kind": binding.kind},
            )
            for binding in f.strategy_bindings
        ],
        "validation_binding": (
            None
            if f.validation_binding is None
            else {
                "normalizer": _callable_ref(f.validation_binding.normalizer),
                "field_validators": [
                    _callable_ref(v) for v in f.validation_binding.field_validators
                ],
            }
        ),
        "grouping_binding": (
            None
            if f.grouping_binding is None
            else {
                "role": f.grouping_binding.role,
                "distance_metric": f.grouping_binding.distance_metric.model_dump(mode="json"),
            }
        ),
        "prompt_binding": (
            None if f.prompt_binding is None else f.prompt_binding.model_dump(mode="json")
        ),
        "filter_binding": (
            None if f.filter_binding is None else f.filter_binding.model_dump(mode="json")
        ),
        "selector_binding": _selector_binding_hash_payload(f),
        "sorter_binding": (
            None
            if f.sorter_binding is None
            else _binding_payload(
                cls=f.sorter_binding.cls,
                params=dict(f.sorter_binding.params),
                extra={},
            )
        ),
    }


def _binding_payload(*, cls: type, params: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    return {"cls": _type_ref(cls), "params": params, **extra}


def _selector_binding_hash_payload(f: FieldSpec) -> Any:
    selector_binding = getattr(f, "selector_binding", None)
    if selector_binding is None:
        return None
    cls = getattr(selector_binding, "cls", None)
    params = getattr(selector_binding, "params", {})
    if cls is not None:
        return _binding_payload(cls=cls, params=dict(params), extra={})
    if hasattr(selector_binding, "model_dump"):
        return selector_binding.model_dump(mode="json")
    return repr(selector_binding)


def _type_ref(t: type) -> str:
    """stable reference string for a type.

    `module.qualname` is deterministic across runs for the same class.
    """

    return f"{t.__module__}.{t.__qualname__}"


def _callable_ref(c: Any) -> Any:
    """stable-ish reference string for a callable.

    when the callable is a class or a function with `__module__` and
    `__qualname__`, use those. otherwise fall back to repr, which is not
    strictly stable across runs but does reflect identity for pydantic-
    backed bindings where the callable is `None`. `from_pydantic`'s
    default `ValidationBinding()` has `normalizer=None` and empty
    `field_validators`, so this branch is exercised primarily by
    user-supplied custom bindings passed via `extract_field(...)`.
    """

    if c is None:
        return None
    if hasattr(c, "__module__") and hasattr(c, "__qualname__"):
        return f"{c.__module__}.{c.__qualname__}"
    return repr(c)
