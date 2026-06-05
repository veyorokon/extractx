"""cardinality primitives per docs/architecture.md §7 seam E and §12.

this module provides the `Cardinality` enum only. seam-E cardinality-table
logic (the `(cardinality, k, outcome)` → outcome dispatch) lives in
`proposals/adapter.py` where the `SelectionAdapter` owns it. the pydantic-
type inference table lives in `schema/inference.py`. both reference this
enum.
"""

from __future__ import annotations

from enum import StrEnum


class Cardinality(StrEnum):
    """the four canonical cardinality modes for a `FieldSpec`.

    see the cardinality table in docs/architecture.md §7 seam E for the
    full `(cardinality, k, outcome)` dispatch.
    """

    ONE = "one"
    OPTIONAL = "optional"
    MANY = "many"
    PER_INSTANCE = "per_instance"


__all__ = ["Cardinality"]
