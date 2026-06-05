"""`ProposalValidator` phase-1 pipeline per docs/architecture.md §7 seam F.

this module lands the phase-1 `LayeredProposalValidator`:

- layer 1 — candidate shape + source-span validity (ADR-0006). dispatches
  on each `SourceSpan.text_anchor_space`. failures are typed
  `NegativeOutcome(category="validation", code="candidate.*")` — **non-
  retryable**. layer 1 emits no `ValidationFailure`.
- layer 2 — the **single** normalization site (§15 `Dual Normalization`).
  dispatched by caller-provided `schema_cls`: pydantic-backed path runs
  pydantic coercion + `field_validator`s; manual path runs
  `FieldSpec.validation_binding.normalizer` + `FieldValidator`s in
  declared order. success → `ValidatedField`. failure →
  `ValidationFailure(layer="field", ...)` — the typed output. retry /
  escalation routing through `ExecutorPolicy.on_validation_failure` is
  declared by §7 seam F but owned by the execution substrate (seam I/J)
  and lands in a later thread; this validator does not import
  `ExecutorPolicy` or invent a retry loop.

- layer 3 — canonical post-resolution instance-layer validation per
  ADR-0003. runs exactly once per `Instance` after `G.resolver`
  has assigned the final `InstanceGroupingKey`. dispatched by caller-provided
  `schema_cls`:
    - pydantic-backed (`schema_cls is not None`): pydantic
      `model_validator(mode="after")` decorators registered on
      `schema_cls` are explicitly invoked on a partial-instance view
      built via `schema_cls.model_construct(**mapping)` from the
      resolved `evidence`. `mode="before"` and `mode="wrap"`
      validators do **not** fire in phase 1 — only `mode="after"`. a
      raising `mode="after"` validator (`pydantic.ValidationError` or
      `ValueError`) is translated to
      `ValidationFailure(layer="instance", field_id="<instance>",
      instance_key=<resolved>, reason=<str>, producer_version=None)`.
      `AttributeError`, `TypeError`, and other unexpected exceptions
      propagate as implementation defects — phase 1 does not mask them.
    - manual (`schema_cls is None`): byte-identical no-op pass-through.
    - pydantic-backed with no registered `model_validator`s: also
      byte-identical pass-through.
    - on success the original `Instance` reference is returned
      unchanged (no defensive rebuild).
    - extractx `InstanceValidator` attachment is **deferred**: the
      landed `ExtractionSpec` / `FieldSpec` / `ValidationBinding`
      surface has no honest attachment point. seam F phase 1 layer 3
      is therefore pydantic-`mode="after"`-only; extractx
      `InstanceValidator` sourcing is owned by a separate
      coordinator-led spec-widening thread.
    - escalation of `ValidationFailure(layer="instance", ...)` to a
      typed `NegativeOutcome` lives on the executor (§7 seam F failure
      routing); the validator emits the typed failure but does not
      escalate it itself.

pydantic `Pydantic-as-Extractor` rejection happens at spec load (seam
B's `detect_pydantic_as_extractor`); this seam does not re-check that
rule at layer 2 (§15 `Duplicate Overlapping Path`).

runtime-reachable malformed `FieldSpec` (both `schema_cls is None` and
`validation_binding is None` at layer 2) raises a local
`ProposalValidatorContractError(ValueError)` — a seam-B defect surfaced
loudly rather than a typed negative, mirroring
`SelectionAdapterContractError`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, cast

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.fields import FieldInfo

from extractx.core.anchors import (
    SourceSpan,
    anchor_invert,
    check_normalized_text_span,
)
from extractx.core.cardinality import Cardinality
from extractx.core.outcomes import (
    Evidence,
    FieldRef,
    NegativeOutcome,
    ObjectIssue,
    ProposedField,
    ValidatedField,
    ValidationFailure,
)
from extractx.core.versions import algorithmic_producer_version, stable_hash
from extractx.schema.object_validators import ObjectValidatorMetadata, get_object_validator_metadata

if TYPE_CHECKING:
    from extractx.core.objects import DocumentView, ExtractionSpec, FieldSpec
    from extractx.core.outcomes import Instance

__all__ = [
    "LayeredProposalValidator",
    "ProposalValidatorContractError",
    "algorithmic_code_hash",
]


class ProposalValidatorContractError(ValueError):
    """raised when a seam-F input triple violates a structural invariant
    that should have been blocked at spec-load by seam B.

    this is an implementation-defect failure, not a typed
    `NegativeOutcome` or `ValidationFailure`. it fires when:

    - `schema_cls is None` **and** `field_spec.validation_binding is None`
      — neither the pydantic-backed path nor the manual path is
      configurable, which is a seam-B contract violation (seam B emits
      `SpecError` for manual `FieldSpec` with `validation_binding=None`
      and no pydantic class fallback per architecture §7 seam B).
    - `schema_cls is not None` but does not carry a field matching
      `field_spec.field_id` — the caller provided the wrong schema class
      for this field.

    mirrors the seam-E `SelectionAdapterContractError` shape: a local
    `ValueError` subtype, not a widened public exception surface.
    """


class LayeredProposalValidator:
    """phase-1 deterministic `ProposalValidator` per architecture §7 seam F.

    one sync, pure, per-`ProposedField` `validate(...)` call runs layer 1
    (candidate shape + source-span validity per ADR-0006) and layer 2
    (the single normalization site), emitting one of:

    - `ValidatedField`                     — layer 2 success
    - `NegativeOutcome(category="validation", code="candidate.*", ...)`
                                             — layer 1 failure (non-retryable)
    - `ValidationFailure(layer="field", ...)`
                                             — layer 2 failure (routed by a
                                               later seam I/J thread)

    the validator holds no configurable state; two instances produce
    byte-identical output for the same inputs.
    """

    def validate(
        self,
        proposed: ProposedField,
        field_spec: FieldSpec,
        document_view: DocumentView,
        schema_cls: type[BaseModel] | None = None,
    ) -> ValidatedField | NegativeOutcome | ValidationFailure:
        # layer 1: candidate-shape + source-span validity per ADR-0006.
        layer1_negative = _layer1_check(proposed, document_view)
        if layer1_negative is not None:
            return layer1_negative

        # layer 2: single normalization site.
        return _layer2_normalize(proposed, field_spec, schema_cls)

    def validate_instance(
        self,
        instance_result: Instance,
        spec: ExtractionSpec,
        schema_cls: type[BaseModel] | None = None,
    ) -> Instance | ValidationFailure:
        """run canonical layer 3 on a resolved `Instance`.

        per ADR-0003, this is the sole instance-layer validation phase
        and runs exactly once per `Instance` after `G.resolver`
        has assigned the final `InstanceGroupingKey`. callers (the executor)
        invoke this once per resolved instance; the validator is
        otherwise unaware of resolver internals.

        dispatch:

        - `schema_cls is None` → manual spec → byte-identical no-op
          pass-through (return the same reference).
        - `schema_cls is not None` and the class registers no
          `model_validator(mode="after")` decorators → byte-identical
          pass-through.
        - `schema_cls is not None` with one or more registered
          `mode="after"` validators → materialize a partial-instance
          view via `schema_cls.model_construct(**mapping)` from
          `instance_result.evidence` and invoke each decorator
          in declaration order. failure of any decorator
          (`pydantic.ValidationError` or `ValueError`) returns
          `ValidationFailure(layer="instance", field_id="<instance>",
          instance_key=<resolved>, reason=<str>,
          producer_version=None)` immediately. success returns the
          original `Instance` reference unchanged.

        `mode="before"` and `mode="wrap"` validators do not fire in
        phase 1. `AttributeError`, `TypeError`, and any other
        unexpected exception types raised inside `mode="after"`
        propagate to the caller as implementation defects — they are
        not translated to typed failures, mirroring the layer-2
        defect-vs-typed-negative discipline.
        """

        # spec is consumed by the protocol contract for symmetry with
        # layer 2's caller-held context; phase-1 layer 3 does not need
        # additional spec-derived dispatch beyond the schema_cls hand-
        # off, so it is only referenced as part of the protocol input
        # contract.
        del spec

        if schema_cls is None:
            return instance_result

        return _layer3_pydantic(
            instance_result=instance_result,
            schema_cls=schema_cls,
        )


def algorithmic_code_hash() -> str:
    """return the seam-F validator's `producer_version` string.

    mirrors the pattern used by seams C / D / G.resolver: the
    `code_hash` is composed from the class's fully-qualified name so
    any subclass with different behavior produces a different
    `producer_version` automatically.

    module-level only — this thread does not introduce a class
    `producer_version` property on `LayeredProposalValidator`. the
    M9 phase 1 capture path consumes the module-level helper
    uniformly across all four seams it tracks.
    """

    digest = stable_hash(
        f"{LayeredProposalValidator.__module__}.{LayeredProposalValidator.__qualname__}",
    )
    return algorithmic_producer_version(digest)


# ---------------------------------------------------------------------------
# layer 1 — candidate-shape + source-span validity (ADR-0006)
# ---------------------------------------------------------------------------


def _layer1_check(
    proposed: ProposedField,
    document_view: DocumentView,
) -> NegativeOutcome | None:
    """return a typed `NegativeOutcome` if layer-1 checks fail, else `None`.

    layer 1 is non-retryable: it emits only `NegativeOutcome` or passes
    through. every failure code is stable, small, and drawn from the set:

    - `candidate.text_anchor_space_mismatch` — a span whose
      `text_anchor_space` is inconsistent with the `DocumentView`'s
      adapter subcontract (mixed-space spans within one `DocumentView`).
    - `candidate.utf8_alignment` — a `normalized_text` span whose
      offsets are UTF-8 misaligned or whose `byte_end` exceeds the
      UTF-8 length of `document_view.normalized_text`.
    - `candidate.span_out_of_range` — a `source_bytes` span that is not
      recoverable from `document_view.anchor_map` via `anchor_invert`.
    - `candidate.structured_payload_shape` — `proposed.normalized_hint`
      is not JSON-safe (not a primitive, `Mapping`, or `Sequence` of
      same).
    """

    # 1. structured_payload (normalized_hint) shape check.
    if proposed.normalized_hint is not None and not _is_json_safe(proposed.normalized_hint):
        return _candidate_negative(
            code="candidate.structured_payload_shape",
            proposed=proposed,
        )

    # 2. source_span and every evidence_span under the same rule.
    spans_to_check: tuple[SourceSpan, ...] = (
        proposed.source_span,
        *proposed.evidence_spans,
    )
    # declared subcontract from the DocumentView's anchor_map: a map whose
    # entries carry `source_bytes` spans implies a linearizable
    # (source_bytes) adapter; one whose entries carry `normalized_text`
    # spans implies a paginated-visual (normalized_text) adapter.
    declared_space = _adapter_subcontract_space(document_view)

    for span in spans_to_check:
        # subcontract consistency: every span in a `DocumentView`
        # adapter must share the adapter's declared `text_anchor_space`.
        if (
            declared_space is not None
            and span.text_anchor_space != declared_space
            and not _is_document_head_normalized_point(span)
        ):
            return _candidate_negative(
                code="candidate.text_anchor_space_mismatch",
                proposed=proposed,
            )

        if span.text_anchor_space == "normalized_text":
            try:
                check_normalized_text_span(span, document_view.normalized_text)
            except ValueError:
                return _candidate_negative(
                    code="candidate.utf8_alignment",
                    proposed=proposed,
                )
        else:  # source_bytes
            try:
                anchor_invert(document_view.anchor_map, span)
            except ValueError:
                return _candidate_negative(
                    code="candidate.span_out_of_range",
                    proposed=proposed,
                )

    return None


def _is_document_head_normalized_point(span: SourceSpan) -> bool:
    return (
        span.text_anchor_space == "normalized_text"
        and span.byte_start == 0
        and span.byte_end == 0
    )


def _adapter_subcontract_space(
    document_view: DocumentView,
) -> str | None:
    """return the `text_anchor_space` declared by the `DocumentView`'s
    adapter subcontract, or `None` when the anchor_map is empty (and no
    subcontract is declared).

    per ADR-0006, an adapter must not mix subcontracts within one
    `DocumentView`. the subcontract is declared implicitly by the
    `text_anchor_space` of the spans the adapter carries on its
    `anchor_map` entries. if the map's entries disagree on
    `text_anchor_space` we treat the `DocumentView` as having no honest
    subcontract and skip the mismatch check (the anchor-map validator at
    seam A should have already rejected such a map).
    """

    entries = document_view.anchor_map.entries
    if not entries:
        return None
    declared = entries[0][1].text_anchor_space
    for _offset, span in entries[1:]:
        if span.text_anchor_space != declared:
            return None
    return declared


def _candidate_negative(
    *,
    code: str,
    proposed: ProposedField,
) -> NegativeOutcome:
    """emit a typed `NegativeOutcome` from layer 1.

    `reason=code` keeps diagnostics stable across runs; no prose is drawn
    from candidate content. `candidate_count` is left `None` because seam
    F does not see the full `CandidateSet` from seam C.
    """

    return NegativeOutcome(
        category="validation",
        code=code,
        field_id=proposed.field_id,
        instance_key=proposed.tentative_instance_key,
        reason=code,
    )


def _is_json_safe(value: Any) -> bool:
    """return True when `value` is a JSON-safe structured payload.

    JSON-safe means: `None`, `bool`, `int`, `float`, `str`, or a
    `Mapping` / `Sequence` of the same (recursively). `bytes`, pydantic
    models, custom class instances, sets, and anything else are shape
    defects at this seam per the seam-F layer-1 phase-1 brief.

    `str` and `bytes` are intentionally distinguished: `str` is JSON-
    safe; `bytes` is not.
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
    # distinguish `bytes` / `bytearray` from `Sequence`: both match
    # `Sequence` but are not JSON-safe.
    if isinstance(value, bytes | bytearray):
        return False
    if isinstance(value, Sequence):
        seq_value = cast("Sequence[Any]", value)
        return all(_is_json_safe(v) for v in seq_value)
    return False


# ---------------------------------------------------------------------------
# layer 3 — canonical post-resolution instance-layer validation
# ---------------------------------------------------------------------------


# literal sentinel for the `field_id` slot on layer-3 typed failures.
# the failure is per-instance cross-field; no single `field_id` is
# implicated. the executor maps this to `NegativeOutcome.field_id=None`
# at escalation time. carried as a module constant so the sentinel is
# not duplicated across the validator and executor sides of the seam.
_LAYER3_INSTANCE_SENTINEL: str = "<instance>"


def _layer3_pydantic(
    *,
    instance_result: Instance,
    schema_cls: type[BaseModel],
) -> Instance | ValidationFailure:
    """invoke pydantic `model_validator(mode="after")` on the resolved
    `Instance` and return either the original reference (success
    / pass-through) or a typed `ValidationFailure(layer="instance",
    ...)`.

    decorator-walk shape (phase 1):

    1. read `schema_cls.__pydantic_decorators__.model_validators` —
       the per-class decorator registry pydantic v2 builds during
       class construction. each entry is a `Decorator` exposing
       `.info.mode` and `.func`.
    2. filter to entries with `info.mode == "after"`. `mode="before"`
       and `mode="wrap"` are intentionally skipped per the brief.
    3. if zero `mode="after"` decorators are registered, return the
       input `Instance` unchanged (byte-identical pass-through).
    4. else: build a `{field_id: normalized_value}` mapping from the
       resolved `evidence` and call
       `schema_cls.model_construct(**mapping)` to obtain a partial-
       instance view. `model_construct` skips required-field checks
       and pydantic's own model-validator pipeline; it is the
       documented escape hatch for partial materialization.
    5. invoke each `mode="after"` decorator's `.func(instance)` in
       declaration order. pydantic v2 returns the registered
       descriptor pre-bound — calling `func(instance)` is correct
       whether the registration was a plain instance method or a
       (deprecated) classmethod-bound `mode="after"`.
    6. on `pydantic.ValidationError` or `ValueError`, immediately
       return `ValidationFailure(layer="instance",
       field_id="<instance>", instance_key=<resolved>, reason=<str>,
       producer_version=None)`. other exception types
       (`AttributeError`, `TypeError`, anything unexpected) propagate
       as implementation defects — phase 1 does not mask them.
    7. on full success, return the input `Instance` reference
       unchanged. no defensive rebuild.

    `model_construct` is the materialization seam. it is intentionally
    not the public `.to_pydantic(...)` projection on `Instance` /
    `Extraction`; layer 3 owns a separate partial-instance
    validation view and must not route through user-facing
    materialization.
    """

    decorators = getattr(schema_cls, "__pydantic_decorators__", None)
    model_validators: dict[str, Any] = (
        getattr(decorators, "model_validators", {}) if decorators is not None else {}
    )
    object_validators = _extractx_object_validators(schema_cls)
    if not model_validators and not object_validators:
        return instance_result

    after_decorators: list[Any] = []
    for _name, decorator in model_validators.items():
        info = getattr(decorator, "info", None)
        if info is None:
            continue
        mode = getattr(info, "mode", None)
        if mode != "after":
            # phase-1 layer 3 only fires `mode="after"`; "before" and
            # "wrap" are documented out of scope.
            continue
        func = getattr(decorator, "func", None)
        if func is None:
            continue
        after_decorators.append(func)

    if not after_decorators and not object_validators:
        return instance_result

    # build the partial-instance mapping from the resolved proposals.
    mapping = _layer3_field_mapping(instance_result)
    evidence = _layer3_evidence_mapping(instance_result)

    # `model_construct` skips both pydantic coercion (already at
    # layer 2) and the model-validator pipeline; we then drive each
    # `mode="after"` decorator explicitly.
    instance_view = schema_cls.model_construct(**mapping)

    for func in after_decorators:
        try:
            func(instance_view)
        except (ValidationError, ValueError) as exc:
            return ValidationFailure(
                layer="instance",
                field_id=_LAYER3_INSTANCE_SENTINEL,
                instance_key=instance_result.instance_key,
                reason=str(exc),
                producer_version=None,
            )

    object_issues: list[ObjectIssue] = []
    for object_validator in object_validators:
        raw_issues = _call_object_validator(
            func=object_validator.func,
            schema_cls=schema_cls,
            values=mapping,
            evidence=evidence,
        )
        object_issues.extend(
            _normalize_object_validator_result(
                raw_issues,
                metadata=object_validator.metadata,
            ),
        )

    blocking_issues = tuple(issue for issue in object_issues if issue.severity == "error")
    if blocking_issues:
        return ValidationFailure(
            layer="instance",
            field_id=_LAYER3_INSTANCE_SENTINEL,
            instance_key=instance_result.instance_key,
            reason=_object_issues_reason(blocking_issues),
            producer_version=None,
            object_issues=blocking_issues,
        )

    return instance_result


def _layer3_field_mapping(instance_result: Instance) -> dict[str, Any]:
    """build the `{field_id: normalized_value}` mapping consumed by
    `schema_cls.model_construct(**mapping)`.

    the mapping draws from `Evidence.field_id` and
    `Evidence.normalized_value` directly; layer 3 does
    not invent a second source of truth for field names or values.
    duplicate `field_id`s within one instance (e.g. multi-cardinality
    fields) collapse to the last surviving proposal in declaration
    order — phase-1 layer 3 does not enforce a separate cardinality
    contract here; that policy lives at `G.resolver`.
    """

    out: dict[str, Any] = {}
    for item in instance_result.evidence:
        out[item.field_id] = item.normalized_value
    return out


def _layer3_evidence_mapping(instance_result: Instance) -> dict[str, Evidence]:
    """build the `{field_id: Evidence}` mapping consumed by object validators."""

    out: dict[str, Evidence] = {}
    for item in instance_result.evidence:
        out[item.field_id] = item
    return out


class _ObjectValidatorBinding(BaseModel):
    """internal binding for schema-attached object validators."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    func: Any
    metadata: ObjectValidatorMetadata


def _extractx_object_validators(schema_cls: type[BaseModel]) -> tuple[_ObjectValidatorBinding, ...]:
    bindings: list[_ObjectValidatorBinding] = []
    for value in vars(schema_cls).values():
        metadata = get_object_validator_metadata(value)
        if metadata is None:
            continue
        func: Any = value
        if isinstance(value, (staticmethod, classmethod)):
            descriptor = cast("Any", value)
            func = descriptor.__func__
        bindings.append(_ObjectValidatorBinding(func=func, metadata=metadata))
    return tuple(bindings)


def _call_object_validator(
    *,
    func: Any,
    schema_cls: type[BaseModel],
    values: Mapping[str, Any],
    evidence: Mapping[str, Evidence],
) -> Any:
    import inspect

    sig = inspect.signature(func)
    params = [
        p
        for p in sig.parameters.values()
        if p.name not in ("self",)
        and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if params and params[0].name == "cls":
        return func(schema_cls, values, evidence)
    return func(values, evidence)


def _normalize_object_validator_result(
    value: Any,
    *,
    metadata: ObjectValidatorMetadata,
) -> tuple[ObjectIssue, ...]:
    if value is None:
        return ()
    if isinstance(value, ObjectIssue):
        return (_apply_object_issue_defaults(value, metadata),)
    if isinstance(value, (tuple, list)):
        raw_items = cast("Sequence[Any]", value)
        return tuple(
            _apply_object_issue_defaults(ObjectIssue.model_validate(item), metadata)
            for item in raw_items
        )
    return (_apply_object_issue_defaults(ObjectIssue.model_validate(value), metadata),)


def _apply_object_issue_defaults(
    issue: ObjectIssue,
    metadata: ObjectValidatorMetadata,
) -> ObjectIssue:
    if issue.implicates or not metadata.implicates:
        return issue
    return ObjectIssue(
        severity=issue.severity,
        code=issue.code,
        reason=issue.reason,
        implicates=tuple(FieldRef(field_id=field_id) for field_id in metadata.implicates),
    )


def _object_issues_reason(issues: tuple[ObjectIssue, ...]) -> str:
    return "; ".join(issue.reason for issue in issues)


# ---------------------------------------------------------------------------
# layer 2 — single normalization site
# ---------------------------------------------------------------------------


def _layer2_normalize(
    proposed: ProposedField,
    field_spec: FieldSpec,
    schema_cls: type[BaseModel] | None,
) -> ValidatedField | ValidationFailure:
    """run the single normalization site.

    dispatch by `schema_cls` presence:

    - pydantic-backed path (`schema_cls is not None`): pydantic coercion
      on `proposed.raw_value` via the single-field build, then pydantic
      `field_validator`s attached to that class fire on the coerced
      value. pydantic `model_validator`s are **not** invoked here;
      they are layer 3.
    - manual path (`schema_cls is None`): call
      `field_spec.validation_binding.normalizer(proposed.raw_value)`,
      then call each `FieldValidator` in declared order on the
      normalized value.

    layer-2 failure is translated to `ValidationFailure(layer="field",
    ...)` — never a raised exception to the caller. the
    `field_validation_version` is still computed so diagnostics carry a
    stable producer version even on failure.
    """

    version = _compose_field_validation_version(
        field_spec=field_spec,
        schema_cls=schema_cls,
    )

    if schema_cls is not None:
        return _layer2_pydantic(
            proposed=proposed,
            field_spec=field_spec,
            schema_cls=schema_cls,
            version=version,
        )
    return _layer2_manual(
        proposed=proposed,
        field_spec=field_spec,
        version=version,
    )


def _layer2_pydantic(
    *,
    proposed: ProposedField,
    field_spec: FieldSpec,
    schema_cls: type[BaseModel],
    version: str,
) -> ValidatedField | ValidationFailure:
    """pydantic-backed path: isolated-field coercion + `field_validator`s.

    phase 1 deliberately does **not** call
    `schema_cls.model_validate(...)` on a full instance — a full build
    would invoke pydantic `model_validator`s, which are **layer 3**
    (post-`G.resolver` per ADR-0003) and must never fire during
    layer 2. instead, the field is validated in isolation:

    1. coerce `proposed.raw_value` using
       `pydantic.TypeAdapter(<field annotation>)`;
    2. replay the class's registered `field_validator`s for the target
       field on the coerced value, in declaration order.

    this keeps layer 2 the single normalization site and keeps
    `model_validator`s strictly out of scope.

    `pydantic.ValidationError` (from coercion) and `ValueError` (from
    `field_validator`s) are caught and translated into
    `ValidationFailure(layer="field", ...)`.
    """

    field_id = field_spec.field_id
    if field_id not in schema_cls.model_fields:
        raise ProposalValidatorContractError(
            f"LayeredProposalValidator: schema_cls {schema_cls.__name__!r} "
            f"has no field named {field_id!r}; the caller must pass the "
            "schema class that declares the field under validation",
        )

    from pydantic import TypeAdapter

    field_info = schema_cls.model_fields[field_id]
    annotation = _validation_annotation(
        field_info=field_info,
        cardinality=field_spec.cardinality,
    )
    adapter: TypeAdapter[Any] = TypeAdapter(annotation)
    try:
        coerced: Any = adapter.validate_python(proposed.raw_value)
    except ValidationError as exc:
        return ValidationFailure(
            layer="field",
            field_id=proposed.field_id,
            instance_key=proposed.tentative_instance_key,
            reason=str(exc),
            producer_version=version,
        )

    decorators = getattr(schema_cls, "__pydantic_decorators__", None)
    field_validators: dict[str, Any] = (
        getattr(decorators, "field_validators", {}) if decorators is not None else {}
    )
    normalized_value: Any = coerced
    for _name, decorator in field_validators.items():
        info = getattr(decorator, "info", None)
        if info is None:
            continue
        fields_attr = cast("tuple[str, ...]", tuple(getattr(info, "fields", ())))
        if field_id not in fields_attr:
            continue
        func = getattr(decorator, "func", None)
        if func is None:
            continue
        try:
            result = _call_field_validator(func, schema_cls, normalized_value)
        except ValidationError as exc:
            return ValidationFailure(
                layer="field",
                field_id=proposed.field_id,
                instance_key=proposed.tentative_instance_key,
                reason=str(exc),
                producer_version=version,
            )
        except ValueError as exc:
            # pydantic `field_validator`s conventionally raise
            # `ValueError`; translate those into the typed failure.
            return ValidationFailure(
                layer="field",
                field_id=proposed.field_id,
                instance_key=proposed.tentative_instance_key,
                reason=str(exc),
                producer_version=version,
            )
        normalized_value = result

    return ValidatedField(
        proposed=proposed,
        normalized_value=normalized_value,
        field_validation_version=version,
    )


def _validation_annotation(
    *,
    field_info: FieldInfo,
    cardinality: Cardinality,
) -> object:
    """Return the annotation pydantic should validate at seam F.

    `model_fields[name].annotation` is the bare Python type; pydantic stores
    `Annotated[...]` validators and constraints in `FieldInfo.metadata`.
    Rebuild the annotation before constructing `TypeAdapter`, otherwise
    annotation-level `BeforeValidator` / `AfterValidator` / `WrapValidator`
    metadata is silently skipped.
    """

    from typing import get_args, get_origin

    annotation = field_info.annotation
    if cardinality is Cardinality.MANY and get_origin(annotation) is list:
        (annotation,) = get_args(annotation)
    if field_info.metadata:
        annotation = Annotated[annotation, *field_info.metadata]
    return annotation


def _call_field_validator(func: Any, schema_cls: type[BaseModel], value: Any) -> Any:
    """invoke a pydantic `field_validator` function on `value`.

    pydantic v2 field validators may be declared as classmethods
    (`@classmethod`) or plain functions. this helper forwards `value`
    to whichever shape was registered; it does not forward the
    optional `info` parameter since seam F does not synthesize one
    (the isolated-field fallback runs outside pydantic's own
    validation context).
    """

    import inspect

    raw: Any = cast("Any", func).__func__ if isinstance(func, classmethod) else func

    try:
        sig = inspect.signature(raw)
    except (TypeError, ValueError):
        return raw(schema_cls, value)
    params = [
        p
        for p in sig.parameters.values()
        if p.name not in ("self",)
        and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    # classmethod case: first param is `cls`.
    if params and params[0].name == "cls":
        return raw(schema_cls, value)
    return raw(value)


def _layer2_manual(
    *,
    proposed: ProposedField,
    field_spec: FieldSpec,
    version: str,
) -> ValidatedField | ValidationFailure:
    """manual path: `ValidationBinding.normalizer` + `FieldValidator`s.

    normalization happens once, at the top of this function. each
    registered `FieldValidator` is called in declared order on the
    normalized value. a validator may:

    - return the value unchanged (pass);
    - return a modified value (pass-with-transform);
    - raise `ValueError` (rejection) — translated into
      `ValidationFailure`.
    """

    binding = field_spec.validation_binding
    if binding is None:
        raise ProposalValidatorContractError(
            "LayeredProposalValidator: field_spec.validation_binding is None "
            f"for field_id={field_spec.field_id!r} and schema_cls is None; "
            "this is a seam-B defect — seam B must reject manual FieldSpecs "
            "that carry no ValidationBinding at spec load",
        )

    normalizer = binding.normalizer
    try:
        if normalizer is not None:
            normalized_value: Any = normalizer(proposed.raw_value)
        else:
            normalized_value = proposed.raw_value
    except ValueError as exc:
        return ValidationFailure(
            layer="field",
            field_id=proposed.field_id,
            instance_key=proposed.tentative_instance_key,
            reason=str(exc),
            producer_version=version,
        )

    for validator in binding.field_validators:
        try:
            result = validator(normalized_value)
        except ValueError as exc:
            return ValidationFailure(
                layer="field",
                field_id=proposed.field_id,
                instance_key=proposed.tentative_instance_key,
                reason=str(exc),
                producer_version=version,
            )
        # a validator may return a new value or `None` / original value.
        # treat `None` as "pass, keep the value" rather than "silently
        # erase" — erasure is a typed negative's concern, not a
        # validator's return shape.
        if result is not None:
            normalized_value = result

    return ValidatedField(
        proposed=proposed,
        normalized_value=normalized_value,
        field_validation_version=version,
    )


# ---------------------------------------------------------------------------
# field_validation_version composition
# ---------------------------------------------------------------------------


def _compose_field_validation_version(
    *,
    field_spec: FieldSpec,
    schema_cls: type[BaseModel] | None,
) -> str:
    """compose the seam-F phase-1 `field_validation_version`.

    shape: `algorithmic_producer_version(stable_hash(tuple))` where
    `tuple` is:

        (
            spec_version,                      # the enclosing ExtractionSpec.version
                                               # when available, else ""
            field_id,
            pydantic_backed_bool,
            normalizer_qualname_or_none,
            tuple(field_validator_qualnames),
        )

    - `spec_version` is carried at the `ExtractionSpec` layer (we do not
      have access to it inside `validate`; callers compose a full run-
      version separately). phase-1 uses the empty string here and pins
      the seam-F producer version to the validator pipeline shape
      itself; the enclosing executor is free to mix in
      `ExtractionSpec.version` at its own layer when composing run
      fingerprints. this keeps seam F's producer version tied to the
      code shape of the validators, not the surrounding spec metadata.
    - for the pydantic-backed path, `normalizer_qualname_or_none` is
      `None` and `field_validator_qualnames` enumerates the qualnames
      of pydantic `field_validator`s registered on `schema_cls` for
      `field_spec.field_id`, in declaration order.
    - for the manual path, `normalizer_qualname_or_none` is the
      qualname of `field_spec.validation_binding.normalizer` (or
      `None` when no normalizer is configured) and
      `field_validator_qualnames` enumerates the qualnames of each
      registered `FieldValidator` in declared order.
    """

    pydantic_backed = schema_cls is not None
    normalizer_qualname: str | None = None
    field_validator_qualnames: tuple[str, ...] = ()

    if schema_cls is not None:
        field_validator_qualnames = _pydantic_field_validator_qualnames(
            schema_cls=schema_cls,
            field_id=field_spec.field_id,
        )
    else:
        binding = field_spec.validation_binding
        if binding is not None:
            if binding.normalizer is not None:
                normalizer_qualname = _qualname_of(binding.normalizer)
            field_validator_qualnames = tuple(_qualname_of(v) for v in binding.field_validators)

    deterministic_tuple: tuple[Any, ...] = (
        "",  # spec_version placeholder — see docstring
        field_spec.field_id,
        pydantic_backed,
        normalizer_qualname,
        list(field_validator_qualnames),
    )
    code_hash = stable_hash(deterministic_tuple)
    return algorithmic_producer_version(code_hash)


def _pydantic_field_validator_qualnames(
    *,
    schema_cls: type[BaseModel],
    field_id: str,
) -> tuple[str, ...]:
    """return the qualnames of pydantic `field_validator`s registered on
    `schema_cls` for `field_id`, in declaration order."""

    decorators = getattr(schema_cls, "__pydantic_decorators__", None)
    if decorators is None:
        return ()
    field_validators: dict[str, Any] = getattr(decorators, "field_validators", {})
    out: list[str] = []
    for _name, decorator in field_validators.items():
        info = getattr(decorator, "info", None)
        if info is None:
            continue
        fields_attr = cast("tuple[str, ...]", tuple(getattr(info, "fields", ())))
        if field_id not in fields_attr:
            continue
        func = getattr(decorator, "func", None)
        if func is None:
            continue
        out.append(_qualname_of(func))
    return tuple(out)


def _qualname_of(obj: Any) -> str:
    """return a deterministic qualname for a callable or classmethod.

    prefers `{module}.{__qualname__}` when both are available; falls
    back to `repr` when the callable is exotic (e.g., an instance of a
    class implementing `__call__`). the qualname is hashed into
    `field_validation_version`, so stability across runs for the same
    code shape is the contract.
    """

    target: Any = cast("Any", obj).__func__ if isinstance(obj, classmethod) else obj
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
    if module is not None and qualname is not None:
        return f"{module}.{qualname}"
    if qualname is not None:
        return qualname
    return repr(target)


# keep `ExtractionSpec` importable for type checkers reading the module
# docstring references; at runtime the validator does not consume it.
if TYPE_CHECKING:
    _ExtractionSpec = ExtractionSpec  # pyright: ignore[reportUnusedVariable]
