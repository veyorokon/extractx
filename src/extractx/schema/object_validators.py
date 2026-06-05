"""schema-attached object validator registration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from extractx.core.objects import FieldId

__all__ = [
    "ObjectValidatorMetadata",
    "extractx_object_validator",
    "get_object_validator_metadata",
]


_OBJECT_VALIDATOR_ATTR = "__extractx_object_validator__"


class ObjectValidatorMetadata(BaseModel):
    """metadata attached to a schema object-validator callable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    implicates: tuple[FieldId, ...] = ()


def extractx_object_validator(
    *,
    implicates: Sequence[FieldId] = (),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a schema method as an extractx object validator."""

    metadata = ObjectValidatorMetadata(implicates=tuple(implicates))

    def _decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, _OBJECT_VALIDATOR_ATTR, metadata)
        return func

    return _decorate


def get_object_validator_metadata(func: object) -> ObjectValidatorMetadata | None:
    """Return object-validator metadata attached by `extractx_object_validator`."""

    raw: object = func
    if isinstance(func, (staticmethod, classmethod)):
        descriptor = cast("Any", func)
        raw = cast("object", descriptor.__func__)
    metadata = getattr(raw, _OBJECT_VALIDATOR_ATTR, None)
    if metadata is None:
        return None
    if isinstance(metadata, ObjectValidatorMetadata):
        return metadata
    return ObjectValidatorMetadata.model_validate(metadata)
