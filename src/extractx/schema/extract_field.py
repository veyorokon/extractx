"""`extract_field()` wrapper over `pydantic.Field` per docs/architecture.md §12.

`extract_field(...)` is a pydantic field-declaration helper. it returns a
`pydantic.fields.FieldInfo` — exactly what `pydantic.Field(...)` returns
— with an attached `ExtractxFieldMetadata` carrying the extractx-specific
declarations (`description`, `cardinality`, `depends_on`, bindings, …).

the metadata rides on the `FieldInfo` under the
`EXTRACTX_METADATA_ATTR` attribute (see `metadata.py`). `from_pydantic`
reads it back to build `FieldSpec`s. pydantic itself ignores the attribute
at runtime; json-schema generation is unaffected.

signature mirrors docs/architecture.md §12 exactly, including the
`sorter_binding` arg added by ADR-0005.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import Field
from pydantic.fields import FieldInfo

from ..core.cardinality import Cardinality
from ..core.objects import (
    FieldId,
    FilterBinding,
    GroupingBinding,
    PromptBinding,
    SelectorBinding,
    SorterBinding,
    StrategyBinding,
    ValidationBinding,
)
from .metadata import ExtractxFieldMetadata

__all__ = ["extract_field"]


_UNSET: Any = object()
"""sentinel distinguishing "user did not pass `default`" from
"user passed `default=None`". `...` (PydanticUndefined) already serves a
similar role inside pydantic; we use a module-local sentinel to keep the
check explicit at this seam."""


def extract_field(
    *,
    description: str,
    cardinality: Cardinality | None = None,
    priority: int = 0,
    depends_on: Sequence[FieldId] = (),
    strategy_bindings: Sequence[StrategyBinding] = (),
    validation_binding: ValidationBinding | None = None,
    grouping_binding: GroupingBinding | None = None,
    prompt_binding: PromptBinding | None = None,
    filter_binding: FilterBinding | None = None,
    selector_binding: SelectorBinding | None = None,
    sorter_binding: SorterBinding | None = None,
    default: Any = _UNSET,
    **pydantic_field_kwargs: Any,
) -> Any:
    """declare an extractx-annotated pydantic field.

    returns a `pydantic.fields.FieldInfo` with a typed `ExtractxFieldMetadata`
    attached under `EXTRACTX_METADATA_ATTR`. behaves as a drop-in
    replacement for `pydantic.Field(...)` — the return type is declared
    as `Any` so it can stand in for an arbitrary typed attribute default
    (pydantic's own `Field` does the same).

    see docs/architecture.md §12 for the signature (including `sorter_binding`
    per ADR-0005) and §15 "Raw-Payload Escape Hatch" for why the metadata
    is a typed container rather than a dict stuffed into `json_schema_extra`.
    """

    metadata = ExtractxFieldMetadata.new(
        description=description,
        cardinality=cardinality,
        priority=priority,
        depends_on=depends_on,
        strategy_bindings=strategy_bindings,
        validation_binding=validation_binding,
        grouping_binding=grouping_binding,
        prompt_binding=prompt_binding,
        filter_binding=filter_binding,
        selector_binding=selector_binding,
        sorter_binding=sorter_binding,
        has_explicit_default=(default is not _UNSET),
        explicit_default=(None if default is _UNSET else default),
    )

    # forward to pydantic.Field. pydantic accepts a positional `default` as
    # well as many keyword args; we keep the surface narrow and forward
    # the user-provided default only when one was explicitly passed.
    field_kwargs: dict[str, Any] = dict(pydantic_field_kwargs)
    field_kwargs.setdefault("description", description)

    if default is _UNSET:
        info: FieldInfo = Field(**field_kwargs)
    else:
        info = Field(default=default, **field_kwargs)

    # carry the typed metadata on `FieldInfo.metadata` — pydantic's per-field
    # metadata list, which also holds `Annotated[...]` markers and survives
    # through `model_fields`. `from_pydantic` recovers the container by an
    # isinstance filter over that list. `FieldInfo` uses __slots__ so a plain
    # setattr is not an option.
    info.metadata.append(metadata)
    return info
