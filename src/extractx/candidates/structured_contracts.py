"""pydantic-backed structural contract evaluation for structured candidates.

Structured candidate sources author ordinary pydantic models as their
contracts. This module adapts `ValidationError` into extractx's small
audit kernel without scraping provider or pydantic error messages.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, Literal, Protocol, get_args, get_origin, runtime_checkable

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from extractx.core import (
    Candidate,
    ConstraintValue,
    PredicateConstraint,
    RangeConstraint,
    SetConstraint,
    StructuralFailure,
    StructuralStatus,
)

__all__ = [
    "NamedPredicate",
    "StructuredContractError",
    "evaluate_structured_contract",
    "evaluate_structured_payload",
]


@runtime_checkable
class NamedPredicate(Protocol):
    """validator callable that exposes a stable audit name."""

    name: str

    def __call__(self, value: Any) -> Any: ...


class StructuredContractError(ValueError):
    """raised when a structural contract cannot be adapted honestly."""


def evaluate_structured_contract(
    candidate: Candidate,
    contract_class: type[BaseModel],
) -> StructuralStatus:
    """evaluate an already-emitted structured candidate against a contract."""

    if candidate.source_kind != "structured":
        raise StructuredContractError(
            "evaluate_structured_contract requires a structured candidate",
        )
    if candidate.structured_payload is None:
        raise StructuredContractError("structured candidate is missing structured_payload")
    return evaluate_structured_payload(candidate.structured_payload, contract_class)


def evaluate_structured_payload(
    payload: Mapping[str, Any],
    contract_class: type[BaseModel],
) -> StructuralStatus:
    """evaluate a structured payload against a pydantic contract class.

    Structured sources call this before constructing `Candidate`, then attach
    the returned status. A passing pydantic validation marks the candidate
    eligible for the deterministic selection gate. A semantic contract failure
    is converted into typed `StructuralFailure` entries.
    """

    try:
        contract_class.model_validate(payload)
    except ValidationError as exc:
        failures = tuple(
            _pydantic_error_to_structural_failure(err, contract_class)
            for err in exc.errors()
        )
        return StructuralStatus(
            passed=False,
            contract_id=_contract_id(contract_class),
            failures=failures,
        )
    return StructuralStatus(passed=True, contract_id=_contract_id(contract_class))


def _contract_id(contract_class: type[BaseModel]) -> str:
    return f"{contract_class.__module__}.{contract_class.__qualname__}"


def _pydantic_error_to_structural_failure(
    err: ErrorDetails,
    contract_class: type[BaseModel],
) -> StructuralFailure:
    loc = err.get("loc", ())
    if not loc:
        raise StructuredContractError("contract error has no field location")
    root_field = str(loc[0])
    field_name = ".".join(str(part) for part in loc)
    field_info = contract_class.model_fields.get(root_field)
    if field_info is None:
        raise StructuredContractError(f"contract error references unknown field {root_field!r}")

    err_type = str(err.get("type", ""))
    actual = _constraint_value(err.get("input", ""))

    if err_type in {"literal_error", "enum"}:
        expected = SetConstraint(allowed=_allowed_values(field_info.annotation))
    elif err_type in {
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
    }:
        lo, lo_inclusive, hi, hi_inclusive = _range_bounds(field_info.metadata)
        expected = RangeConstraint(
            lo=lo,
            hi=hi,
            lo_inclusive=lo_inclusive,
            hi_inclusive=hi_inclusive,
        )
    elif err_type == "value_error":
        expected = PredicateConstraint(name=_predicate_name(field_info.metadata))
    else:
        raise StructuredContractError(
            f"unsupported structured contract error type {err_type!r} for field {field_name!r}",
        )

    return StructuralFailure(field=field_name, actual=actual, expected=expected)


def _allowed_values(annotation: object) -> tuple[ConstraintValue, ...]:
    origin = get_origin(annotation)
    if origin is Literal:
        return tuple(_constraint_value(value) for value in get_args(annotation))
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return tuple(_constraint_value(member.value) for member in annotation)
    raise StructuredContractError(f"cannot recover set constraint from annotation {annotation!r}")


def _range_bounds(
    metadata: list[Any],
) -> tuple[ConstraintValue | None, bool, ConstraintValue | None, bool]:
    lo: ConstraintValue | None = None
    lo_inclusive = True
    hi: ConstraintValue | None = None
    hi_inclusive = True
    for item in metadata:
        ge = getattr(item, "ge", None)
        if ge is not None:
            lo = _constraint_value(ge)
            lo_inclusive = True
        gt = getattr(item, "gt", None)
        if gt is not None:
            lo = _constraint_value(gt)
            lo_inclusive = False
        le = getattr(item, "le", None)
        if le is not None:
            hi = _constraint_value(le)
            hi_inclusive = True
        lt = getattr(item, "lt", None)
        if lt is not None:
            hi = _constraint_value(lt)
            hi_inclusive = False
    if lo is None and hi is None:
        raise StructuredContractError("cannot recover range bounds from field metadata")
    return lo, lo_inclusive, hi, hi_inclusive


def _predicate_name(metadata: list[Any]) -> str:
    for item in metadata:
        validator = getattr(item, "func", None)
        if validator is None:
            continue
        if isinstance(validator, NamedPredicate):
            return validator.name
        name = getattr(validator, "name", None)
        if isinstance(name, str) and name:
            return name
        class_name = type(validator).__name__
        if class_name != "function":
            return class_name
        function_name = getattr(validator, "__name__", None)
        if isinstance(function_name, str) and function_name:
            return function_name
    raise StructuredContractError("cannot recover predicate name from field metadata")


def _constraint_value(value: object) -> ConstraintValue:
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)
