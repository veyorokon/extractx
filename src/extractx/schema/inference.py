"""cardinality and `ValueKind` inference from pydantic annotations.

implements the inference table in docs/architecture.md §12:

| pydantic annotation                          | inferred `Cardinality` |
| -------------------------------------------- | ---------------------- |
| `X` (bare)                                   | `Cardinality.ONE`      |
| `X \\| None` or `Optional[X]`                 | `Cardinality.OPTIONAL` |
| `list[X]` where X is a pydantic `BaseModel`  | `Cardinality.PER_INSTANCE` |
| `list[X]` where X is scalar / non-model      | `Cardinality.MANY`     |
| explicit `cardinality=` in `extract_field()` | overrides inference    |

`ValueKind` extraction: `Annotated[T, ValueKind.X, ...]` must carry exactly
one `ValueKind` in its metadata. zero or multiple `ValueKind`s raise
`SpecError` at spec load (docs/architecture.md §7 seam B: "invalid
`ValueKind`s, missing required bindings"). bare annotations (no
`Annotated`) also have zero value_kinds and therefore raise `SpecError`.

`python_type` extraction: the element type of the annotation. for
`Optional[T]`, it is `T`; for `list[T]`, it is `T`; for `Annotated[T, ...]`,
it is `T` (the underlying); for bare `X`, it is `X`.

annotations outside the documented table (`tuple[X, ...]`, `set[X]`,
`dict[K, V]`, `Union[A, B]` with more than one non-None arm, etc.) raise
`SpecError`. that is the narrowest honest behavior for the v1 contract —
the table in §12 is the full declared surface, and silently picking a
cardinality for anything else would smuggle policy into the schema seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import NoneType, UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from ..core.cardinality import Cardinality
from ..core.exceptions import SpecError
from ..core.value_kinds import ValueKind

__all__ = ["FieldTypeInfo", "analyze_field_annotation"]


@dataclass(frozen=True)
class FieldTypeInfo:
    """result of analyzing a pydantic field annotation.

    - `inferred_cardinality`: the cardinality the table in §12 produces
      for this annotation. the caller decides whether to use it or the
      explicit override from `extract_field(cardinality=...)`.
    - `value_kind`: the unique `ValueKind` found in the annotation's
      `Annotated` metadata.
    - `python_type`: the element type. for `list[T]` it is `T`; for
      `Optional[T]` / `T | None` it is `T`; for bare `X` it is `X`. this
      is what `FieldSpec.python_type` carries — the semantic element type,
      not the list wrapper or optional wrapper.
    """

    inferred_cardinality: Cardinality
    value_kind: ValueKind
    python_type: type
    literal_values: tuple[str, ...] = ()


def _strip_annotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    """peel `Annotated[...]` off `annotation`, returning `(inner, metadata)`.

    when `annotation` is not an `Annotated`, returns `(annotation, ())`.
    `Annotated[Annotated[T, a], b]` is folded into a single
    `(T, (a, b))` by typing internals already, so one call suffices.
    """

    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        # args[0] is the inner type; args[1:] is the metadata tuple.
        return args[0], tuple(args[1:])
    return annotation, ()


def _is_optional_union(annotation: Any) -> tuple[bool, Any]:
    """return `(is_optional, inner_type)`.

    matches both `Optional[T]` (typing.Union) and `T | None` (types.UnionType).
    supports only the two-arm form with `NoneType` on exactly one arm.
    a three-or-more-arm union raises `SpecError` later via `_reject_shape`;
    here we only report "does this look like an `Optional` wrapper?"
    """

    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = get_args(annotation)
        non_none = tuple(a for a in args if a is not NoneType)
        has_none = any(a is NoneType for a in args)
        if has_none and len(non_none) == 1 and len(args) == 2:
            return True, non_none[0]
    return False, annotation


def _classify_list_element(element_type: Any) -> Cardinality:
    """return `PER_INSTANCE` for pydantic model element types, else `MANY`.

    the check is `issubclass(element_type, BaseModel)` when the candidate
    is a class; everything else (bare `str`, `int`, a `typing.Annotated`
    wrapper over a scalar, etc.) routes to `MANY` per §12.
    """

    # an element type may be itself `Annotated[...]` — strip before
    # testing subclass, so `list[Annotated[Decimal, ValueKind.MONEY]]`
    # correctly lands on MANY rather than failing the isinstance check.
    inner, _ = _strip_annotated(element_type)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return Cardinality.PER_INSTANCE
    return Cardinality.MANY


def _extract_value_kind(field_id: str, metadata: tuple[Any, ...]) -> ValueKind:
    """return the unique `ValueKind` in `metadata`; raise `SpecError` otherwise.

    `FieldSpec.value_kind` is required by the core object layer. missing or
    multiple `ValueKind`s at this seam is a spec-load violation per §7 B.
    """

    kinds = [m for m in metadata if isinstance(m, ValueKind)]
    if len(kinds) == 0:
        raise SpecError(
            f"field {field_id!r}: annotation must be `Annotated[T, ValueKind.X]` "
            f"carrying exactly one ValueKind; got none. see docs/architecture.md §12.",
        )
    if len(kinds) > 1:
        names = ", ".join(k.name for k in kinds)
        raise SpecError(
            f"field {field_id!r}: annotation carries multiple ValueKind markers ({names}); "
            f"exactly one is required.",
        )
    return kinds[0]


def _reject_unsupported_shape(field_id: str, annotation: Any) -> None:
    """raise `SpecError` for annotations outside the §12 inference table.

    this is the narrowest honest behavior — a `tuple[X, ...]` could
    plausibly infer to `MANY`, but §12 does not declare that, and
    extrapolating would smuggle cardinality policy into the schema seam.
    users who want such shapes either restate them as `list[X]` or set
    `cardinality=` explicitly in `extract_field(...)` and narrow the
    python type accordingly.
    """

    origin = get_origin(annotation)
    if origin is None:
        return
    if origin is Literal:
        return
    if origin is list:
        return
    if origin is Union or origin is UnionType:
        # `_is_optional_union` already handled the two-arm `X | None` case;
        # anything still reaching here is a wider union that we don't infer.
        raise SpecError(
            f"field {field_id!r}: union types with more than one non-None arm are not "
            f"supported by the §12 inference table. pass `cardinality=` explicitly "
            f"and narrow the annotation to one arm to declare intent.",
        )
    # tuples, sets, dicts, pydantic generic containers, etc. are not in the
    # declared table. fail loudly.
    raise SpecError(
        f"field {field_id!r}: annotation shape {annotation!r} is not in the "
        f"§12 cardinality inference table (bare X, Optional[X], list[X]). "
        f"declare `cardinality=` explicitly in `extract_field(...)` if you "
        f"need a custom shape, and keep the annotation to the supported forms.",
    )


def analyze_field_annotation(field_id: str, annotation: Any) -> FieldTypeInfo:
    """produce `FieldTypeInfo` for a pydantic field annotation.

    strict on the §12 table. calling code is expected to override
    `inferred_cardinality` with an explicit `cardinality=` from
    `extract_field(...)` when one was passed.

    raises `SpecError` when:
    - the annotation carries zero or multiple `ValueKind` markers
      (when a `ValueKind` is required at that layer; see below)
    - the annotation shape is outside the documented table

    `ValueKind` is required at the leaf annotation — the element type of
    a `list[X]`, the non-None arm of `Optional[X]`, or the bare `X` itself.
    for `list[SubModel]` where `SubModel` is a pydantic `BaseModel`, no
    `ValueKind` is required on the list element — submodels define their
    own fields with their own `ValueKind`s, and the parent field's
    `python_type` is the submodel class.
    """

    # unwrap one level of `Annotated` to capture any metadata attached at
    # the outermost layer; then handle `Optional[...]` and `list[...]`.
    outer_type, outer_meta = _strip_annotated(annotation)

    # 1. check Optional[X] / X | None
    is_optional, inner = _is_optional_union(outer_type)
    if is_optional:
        # the `ValueKind` may be on the outer `Annotated` or on the inner
        # non-None arm — the branded-types pattern in §12 puts it on the
        # leaf (`Money = Annotated[Decimal, ValueKind.MONEY]`). unwrap the
        # inner to find its metadata and python type.
        inner_type, inner_meta = _strip_annotated(inner)
        combined_meta = outer_meta + inner_meta
        kind = _extract_value_kind(field_id, combined_meta)
        _reject_unsupported_shape(field_id, inner_type)
        return FieldTypeInfo(
            inferred_cardinality=Cardinality.OPTIONAL,
            value_kind=kind,
            python_type=_resolve_python_type(field_id, inner_type),
            literal_values=_literal_values(field_id, inner_type),
        )

    # 2. check list[X]
    origin = get_origin(outer_type)
    if origin is list:
        (element,) = get_args(outer_type)
        element_type, element_meta = _strip_annotated(element)
        cardinality = _classify_list_element(element)
        if cardinality is Cardinality.PER_INSTANCE:
            # list[SubModel] — submodel defines fields via its own annotations;
            # no ValueKind required at this layer.
            return FieldTypeInfo(
                inferred_cardinality=Cardinality.PER_INSTANCE,
                # ValueKind not semantically meaningful for a submodel aggregate;
                # we still need to populate one to satisfy FieldSpec — use a
                # dedicated brand registered for this shape so downstream code
                # can distinguish "container of submodel" from a scalar kind.
                value_kind=_per_instance_value_kind(),
                python_type=_resolve_python_type(field_id, element_type),
                literal_values=(),
            )
        # list[Scalar] — ValueKind required on the element annotation.
        combined_meta = outer_meta + element_meta
        kind = _extract_value_kind(field_id, combined_meta)
        _reject_unsupported_shape(field_id, element_type)
        return FieldTypeInfo(
            inferred_cardinality=Cardinality.MANY,
            value_kind=kind,
            python_type=_resolve_python_type(field_id, element_type),
            literal_values=_literal_values(field_id, element_type),
        )

    # 3. bare X — the `ValueKind` must live in `outer_meta` (i.e. on the
    # branded annotation). scalars without `Annotated` metadata are
    # rejected as missing a ValueKind.
    _reject_unsupported_shape(field_id, outer_type)
    kind = _extract_value_kind(field_id, outer_meta)
    return FieldTypeInfo(
        inferred_cardinality=Cardinality.ONE,
        value_kind=kind,
        python_type=_resolve_python_type(field_id, outer_type),
        literal_values=_literal_values(field_id, outer_type),
    )


def _resolve_python_type(field_id: str, t: Any) -> type:
    """narrow an annotation to a `type` for `FieldSpec.python_type`.

    the core `FieldSpec` requires `python_type: type`. for bare class
    annotations (`Decimal`, `str`, a pydantic `BaseModel` subclass), `t`
    is already a class. `Annotated` has been stripped by the caller.
    """

    if get_origin(t) is Literal:
        values = tuple(get_args(t))
        literal_types: set[type] = {type(value) for value in values}
        if len(literal_types) == 1:
            return next(iter(literal_types))
        raise SpecError(
            f"field {field_id!r}: Literal fields must use one python value type; "
            f"got {[typ.__name__ for typ in literal_types]!r}.",
        )
    if isinstance(t, type):
        return t
    raise SpecError(
        f"field {field_id!r}: python element type {t!r} is not a class; "
        f"cannot set FieldSpec.python_type.",
    )


def _literal_values(field_id: str, t: Any) -> tuple[str, ...]:
    if get_origin(t) is not Literal:
        return ()
    values = get_args(t)
    if not values:
        raise SpecError(f"field {field_id!r}: Literal annotation must contain at least one arm.")
    if not all(isinstance(value, str) for value in values):
        raise SpecError(
            f"field {field_id!r}: LiteralSetCandidateStrategy supports string Literal arms; "
            f"got {values!r}.",
        )
    return tuple(values)


# a dedicated ValueKind for `list[SubModel]` aggregates. `from_pydantic`
# assigns this to the parent field's `FieldSpec.value_kind` because every
# FieldSpec carries a non-optional ValueKind and "SUBMODEL" is the honest
# tag for "this field nests a pydantic model per instance". submodel
# *children* carry their own branded ValueKinds via their own extract_field
# declarations.
_SUBMODEL_KIND_NAME = "SUBMODEL"


def _per_instance_value_kind() -> ValueKind:
    """return the `ValueKind` used for `list[SubModel]` aggregate fields.

    lazily registered so module import order does not matter; idempotent
    by ValueKind.register() contract.
    """

    return ValueKind.register(_SUBMODEL_KIND_NAME)
