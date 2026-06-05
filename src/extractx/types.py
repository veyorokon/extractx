"""branded-type aliases per docs/architecture.md §12.

each alias is a thin `Annotated[python_type, ValueKind.X]` wrapper. the
alias carries only a semantic `ValueKind` marker; no runtime behavior,
no normalization, no validation logic lives here. seam B's
`ExtractionSpec.from_pydantic(...)` reads the `ValueKind` marker off the
annotation when building the `FieldSpec`.

users can register additional value kinds via `ValueKind.register("NAME")`
and build their own branded aliases downstream; see §14 extensibility
map. this module ships the built-in set referenced in the architecture's
§12 examples.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from .core.value_kinds import ValueKind

__all__ = [
    "Bool",
    "Cardinal",
    "Category",
    "Date",
    "Gpe",
    "Money",
    "Ordinal",
    "Org",
    "Percent",
    "Person",
]

Money = Annotated[Decimal, ValueKind.MONEY]
Percent = Annotated[Decimal, ValueKind.PERCENT]
Date = Annotated[date, ValueKind.DATE]
Org = Annotated[str, ValueKind.ORG]
Person = Annotated[str, ValueKind.PERSON]
Gpe = Annotated[str, ValueKind.GPE]
Cardinal = Annotated[int, ValueKind.CARDINAL]
Ordinal = Annotated[int, ValueKind.ORDINAL]
Bool = Annotated[bool, ValueKind.BOOL]
Category = Annotated[str, ValueKind.CATEGORY]
