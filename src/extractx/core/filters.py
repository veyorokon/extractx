"""candidate-filter predicate AST.

Filters are declarative, JSON-serializable refinements over a generated
`CandidateSet`. They are intentionally not callables: replay and spec hashing
must see the same durable expression shape that runtime executes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _FilterNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LabelIn(_FilterNode):
    """candidate `entity_type` is one of `labels`."""

    kind: Literal["label_in"] = "label_in"
    labels: tuple[str, ...]

    @model_validator(mode="after")
    def _check_labels(self) -> LabelIn:
        if not self.labels:
            raise ValueError("LabelIn.labels must be non-empty")
        return self


class LabelNotIn(_FilterNode):
    """candidate `entity_type` is absent from `labels`."""

    kind: Literal["label_not_in"] = "label_not_in"
    labels: tuple[str, ...]

    @model_validator(mode="after")
    def _check_labels(self) -> LabelNotIn:
        if not self.labels:
            raise ValueError("LabelNotIn.labels must be non-empty")
        return self


class ContainedBy(_FilterNode):
    """candidate span is strictly contained by another candidate's span.

    When `label` is provided, only containing candidates with that
    `entity_type` satisfy the predicate. A candidate never contains itself.
    """

    kind: Literal["contained_by"] = "contained_by"
    label: str | None = None


class Contains(_FilterNode):
    """candidate span strictly contains another candidate's span.

    When `label` is provided, only contained candidates with that
    `entity_type` satisfy the predicate. A candidate never contains itself.
    """

    kind: Literal["contains"] = "contains"
    label: str | None = None


class NumericRange(_FilterNode):
    """candidate numeric value falls within the configured bounds.

    Bounds are strings to keep JSON payloads stable across Python numeric
    representations. Runtime evaluates them with `Decimal`.
    """

    kind: Literal["numeric_range"] = "numeric_range"
    lo: str | None = None
    hi: str | None = None
    include_lo: bool = True
    include_hi: bool = True

    @model_validator(mode="after")
    def _check_any_bound(self) -> NumericRange:
        if self.lo is None and self.hi is None:
            raise ValueError("NumericRange requires at least one bound")
        return self


class ContextContains(_FilterNode):
    """candidate context contains configured strings."""

    kind: Literal["context_contains"] = "context_contains"
    any_of: tuple[str, ...] = ()
    all_of: tuple[str, ...] = ()
    case_sensitive: bool = False

    @model_validator(mode="after")
    def _check_needles(self) -> ContextContains:
        if not self.any_of and not self.all_of:
            raise ValueError("ContextContains requires any_of or all_of")
        return self


class And(_FilterNode):
    """logical conjunction over child expressions."""

    kind: Literal["and"] = "and"
    exprs: tuple[FilterExpr, ...]

    @model_validator(mode="after")
    def _check_exprs(self) -> And:
        if not self.exprs:
            raise ValueError("And.exprs must be non-empty")
        return self


class Or(_FilterNode):
    """logical disjunction over child expressions."""

    kind: Literal["or"] = "or"
    exprs: tuple[FilterExpr, ...]

    @model_validator(mode="after")
    def _check_exprs(self) -> Or:
        if not self.exprs:
            raise ValueError("Or.exprs must be non-empty")
        return self


class Not(_FilterNode):
    """logical negation over one child expression."""

    kind: Literal["not"] = "not"
    expr: FilterExpr


type FilterExpr = Annotated[
    LabelIn
    | LabelNotIn
    | ContainedBy
    | Contains
    | NumericRange
    | ContextContains
    | And
    | Or
    | Not,
    Field(discriminator="kind"),
]


__all__ = [
    "And",
    "ContainedBy",
    "Contains",
    "ContextContains",
    "FilterExpr",
    "LabelIn",
    "LabelNotIn",
    "Not",
    "NumericRange",
    "Or",
]
