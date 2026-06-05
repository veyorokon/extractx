"""typed metadata container for `extract_field` per docs/architecture.md §12.

`extract_field(...)` attaches an `ExtractxFieldMetadata` instance to a
pydantic `FieldInfo.metadata` list so that `from_pydantic` can recover
the extractx-specific declarations later without shoving an untyped dict
into `json_schema_extra`.

the container is a frozen dataclass — *not* a pydantic `BaseModel`. a
`BaseModel` instance placed in `Annotated[T, ...]` metadata is treated
by pydantic as a validation constraint (it tries to coerce the field
value into the model at validation time), which is wrong for a descriptor
container. a plain dataclass is ignored by pydantic's validator system
and survives through `FieldInfo.metadata` unchanged.

`FieldSpec.strategy_bindings` is a tuple of candidate strategy bindings and
`FieldSpec.validation_binding` is `ValidationBinding | None`
(docs/architecture.md §9). when `extract_field(...)` did not receive explicit
strategy bindings, the resulting `FieldSpec` carries an empty tuple — no
sentinel, no synthetic default except the schema-derived CATEGORY literal-set
strategy inserted by `from_pydantic`. downstream seams interpret an empty tuple
per their own contracts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

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

__all__ = [
    "EXTRACTX_METADATA_ATTR",
    "ExtractxFieldMetadata",
]


EXTRACTX_METADATA_ATTR = "__extractx_metadata__"
"""legacy attribute name. the container is carried on `FieldInfo.metadata`
(the typed per-field metadata list that also holds `Annotated[...]`
markers) and `from_pydantic` recovers it via an isinstance filter over
that list. this constant is kept for observability and as a documented
name for the metadata role, not as an attribute."""


@dataclass(frozen=True)
class ExtractxFieldMetadata:
    """typed metadata carried on a pydantic `FieldInfo` by `extract_field`.

    frozen dataclass (not `BaseModel`): pydantic would try to apply a
    `BaseModel` found in `Annotated[...]` metadata as a validation
    constraint on the field value. a plain dataclass is opaque to
    pydantic's validator and rides through `FieldInfo.metadata` intact.

    `cardinality=None` means "infer from the pydantic annotation"; a
    non-null value overrides the inference table in §12.

    every field here mirrors an argument on `extract_field`. `from_pydantic`
    reads this to build `FieldSpec` without needing to re-derive user
    intent from pydantic's json-schema-extra dict.
    """

    description: str
    cardinality: Cardinality | None = None
    priority: int = 0
    depends_on: tuple[FieldId, ...] = ()
    strategy_bindings: tuple[StrategyBinding, ...] = ()
    validation_binding: ValidationBinding | None = None
    grouping_binding: GroupingBinding | None = None
    prompt_binding: PromptBinding | None = None
    filter_binding: FilterBinding | None = None
    selector_binding: SelectorBinding | None = None
    sorter_binding: SorterBinding | None = None
    has_explicit_default: bool = False
    explicit_default: Any = field(default=None)

    @classmethod
    def new(
        cls,
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
        has_explicit_default: bool = False,
        explicit_default: Any = None,
    ) -> ExtractxFieldMetadata:
        """construct an `ExtractxFieldMetadata` with normalized `depends_on`."""

        return cls(
            description=description,
            cardinality=cardinality,
            priority=priority,
            depends_on=tuple(depends_on),
            strategy_bindings=tuple(strategy_bindings),
            validation_binding=validation_binding,
            grouping_binding=grouping_binding,
            prompt_binding=prompt_binding,
            filter_binding=filter_binding,
            selector_binding=selector_binding,
            sorter_binding=sorter_binding,
            has_explicit_default=has_explicit_default,
            explicit_default=explicit_default,
        )
