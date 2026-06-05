"""pydantic materialization for `Extraction` projections.

`Instance` / `Extraction` are canonical result objects. this
module builds a user-facing pydantic model as a derived projection over
`Instance.evidence`, preserving the schema cardinality contract
without rerunning pydantic validators.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel

from ..core.cardinality import Cardinality
from ..core.exceptions import SpecError
from ..core.outcomes import Evidence, Extraction, Instance
from .inference import analyze_field_annotation
from .metadata import ExtractxFieldMetadata

__all__ = ["instance_to_pydantic", "result_to_pydantic"]


def instance_to_pydantic(instance: Instance, cls: type[BaseModel]) -> BaseModel:
    """materialize one resolved instance into `cls`.

    Values are read from `Evidence.normalized_value`. pydantic
    validation has already happened at seam F, so construction deliberately
    uses `model_construct(...)` after local cardinality and field-shape
    precondition checks.
    """

    _ensure_model_cls(cls)
    model_fields = cls.model_fields
    proposals_by_field = _group_proposals(instance.evidence)
    _reject_unknown_fields(proposals_by_field, model_fields)

    mapping: dict[str, Any] = {}
    missing_required: list[str] = []

    for field_id, field_info in model_fields.items():
        annotation = field_info.rebuild_annotation()
        type_info = analyze_field_annotation(field_id, annotation)
        metadata = _read_metadata(field_id, field_info)
        cardinality = (
            metadata.cardinality
            if metadata.cardinality is not None
            else type_info.inferred_cardinality
        )
        values = [p.normalized_value for p in proposals_by_field.get(field_id, ())]

        if cardinality is Cardinality.ONE:
            if len(values) == 1:
                mapping[field_id] = values[0]
            elif len(values) == 0:
                if _is_required(field_info):
                    missing_required.append(field_id)
            else:
                raise SpecError(
                    "to_pydantic.cardinality: "
                    f"field {field_id!r} has cardinality 'one' but received "
                    f"{len(values)} proposals.",
                )
        elif cardinality is Cardinality.OPTIONAL:
            if len(values) == 0:
                mapping[field_id] = None
            elif len(values) == 1:
                mapping[field_id] = values[0]
            else:
                raise SpecError(
                    "to_pydantic.cardinality: "
                    f"field {field_id!r} has cardinality 'optional' but received "
                    f"{len(values)} proposals.",
                )
        elif cardinality is Cardinality.MANY:
            mapping[field_id] = values
        elif cardinality is Cardinality.PER_INSTANCE:
            raise SpecError(
                "to_pydantic.per_instance_unsupported: "
                f"field {field_id!r} uses per_instance materialization, which is "
                "out of scope for phase 1.",
            )
        else:
            raise SpecError(
                "to_pydantic.cardinality: "
                f"field {field_id!r} has unsupported cardinality {cardinality!r}.",
            )

    if missing_required:
        raise SpecError(
            "to_pydantic.missing_required: "
            f"missing required field proposals for {missing_required!r}.",
        )

    return cls.model_construct(**mapping)


def result_to_pydantic(result: Extraction, cls: type[BaseModel]) -> list[BaseModel]:
    """materialize every instance in order."""

    _ensure_model_cls(cls)
    return [instance_to_pydantic(instance, cls) for instance in result.instances]


def _ensure_model_cls(cls: object) -> None:
    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        raise SpecError(
            f"to_pydantic.invalid_schema: expected a pydantic BaseModel subclass; got {cls!r}.",
        )


def _group_proposals(
    proposals: tuple[Evidence, ...],
) -> dict[str, list[Evidence]]:
    grouped: defaultdict[str, list[Evidence]] = defaultdict(list)
    for proposal in proposals:
        grouped[proposal.field_id].append(proposal)
    return dict(grouped)


def _reject_unknown_fields(
    proposals_by_field: dict[str, list[Evidence]],
    model_fields: dict[str, Any],
) -> None:
    unknown = [field_id for field_id in proposals_by_field if field_id not in model_fields]
    if unknown:
        raise SpecError(
            "to_pydantic.unknown_field: "
            f"resolved proposals contain fields not declared on the requested schema: "
            f"{unknown!r}.",
        )


def _read_metadata(field_id: str, field_info: Any) -> ExtractxFieldMetadata:
    raw_metadata_list = getattr(field_info, "metadata", [])
    matches = [m for m in raw_metadata_list if isinstance(m, ExtractxFieldMetadata)]
    if len(matches) == 0:
        raise SpecError(
            "to_pydantic.invalid_schema: "
            f"field {field_id!r} must be declared with extract_field(...).",
        )
    if len(matches) > 1:
        raise SpecError(
            "to_pydantic.invalid_schema: multiple ExtractxFieldMetadata instances "
            "attached to one pydantic field.",
        )
    return matches[0]


def _is_required(field_info: Any) -> bool:
    is_required = getattr(field_info, "is_required", None)
    if callable(is_required):
        return bool(is_required())
    return bool(is_required)
