"""proof that `extractx.types` branded aliases are honest thin wrappers.

each alias must be a plain `Annotated[python_type, ValueKind.X]`. the
module carries no runtime behavior beyond the aliases. seam B must be
able to read the `ValueKind` marker via `ExtractionSpec.from_pydantic(...)`.
"""

from __future__ import annotations

import typing
from datetime import date
from decimal import Decimal

import pytest
from pydantic import BaseModel

from extractx import (
    Bool,
    Cardinal,
    Cardinality,
    Category,
    Date,
    ExtractionSpec,
    Gpe,
    Money,
    Ordinal,
    Org,
    Percent,
    Person,
    ValueKind,
    extract_field,
)

# ---------------------------------------------------------------------------
# shape: each alias is Annotated[python_type, ValueKind.X]
# ---------------------------------------------------------------------------


EXPECTED_ALIAS_SHAPE: list[tuple[object, type, ValueKind]] = [
    (Money, Decimal, ValueKind.MONEY),
    (Percent, Decimal, ValueKind.PERCENT),
    (Date, date, ValueKind.DATE),
    (Org, str, ValueKind.ORG),
    (Person, str, ValueKind.PERSON),
    (Gpe, str, ValueKind.GPE),
    (Cardinal, int, ValueKind.CARDINAL),
    (Ordinal, int, ValueKind.ORDINAL),
    (Bool, bool, ValueKind.BOOL),
    (Category, str, ValueKind.CATEGORY),
]


class TestBrandedAliasShape:
    @pytest.mark.parametrize(("alias", "expected_type", "expected_kind"), EXPECTED_ALIAS_SHAPE)
    def test_alias_is_annotated_wrapper(
        self, alias: object, expected_type: type, expected_kind: ValueKind
    ) -> None:
        """each alias resolves to `Annotated[python_type, ValueKind.X]`."""
        origin = typing.get_origin(alias)
        assert origin is not None, f"{alias!r} is not an Annotated alias"
        args = typing.get_args(alias)
        assert args[0] is expected_type, f"{alias!r} wraps {args[0]!r}, expected {expected_type!r}"
        assert expected_kind in args[1:], (
            f"{alias!r} metadata does not carry {expected_kind!r}; got {args[1:]!r}"
        )


# ---------------------------------------------------------------------------
# seam B can read the ValueKind through ExtractionSpec.from_pydantic(...)
# ---------------------------------------------------------------------------


class _AllBrandedTypes(BaseModel):
    money: Money = extract_field(description="money field")
    percent: Percent = extract_field(description="percent field")
    day: Date = extract_field(description="date field")
    org: Org = extract_field(description="org field")
    person: Person = extract_field(description="person field")
    place: Gpe = extract_field(description="gpe field")
    count: Cardinal = extract_field(description="cardinal field")
    rank: Ordinal = extract_field(description="ordinal field")
    flag: Bool = extract_field(description="bool field")


class TestSeamBReadsBrandedValueKind:
    def test_from_pydantic_extracts_value_kind_from_each_alias(self) -> None:
        """`from_pydantic` extracts the correct `ValueKind` for every alias."""
        spec = ExtractionSpec.from_pydantic(_AllBrandedTypes)
        by_field = {f.field_id: f for f in spec.fields}

        expected: dict[str, ValueKind] = {
            "money": ValueKind.MONEY,
            "percent": ValueKind.PERCENT,
            "day": ValueKind.DATE,
            "org": ValueKind.ORG,
            "person": ValueKind.PERSON,
            "place": ValueKind.GPE,
            "count": ValueKind.CARDINAL,
            "rank": ValueKind.ORDINAL,
            "flag": ValueKind.BOOL,
        }

        for field_id, expected_kind in expected.items():
            assert by_field[field_id].value_kind is expected_kind, (
                f"{field_id}: expected {expected_kind!r}, got {by_field[field_id].value_kind!r}"
            )

    def test_optional_branded_alias_inference(self) -> None:
        """`Optional[Money]` still extracts `ValueKind.MONEY` and infers `optional`."""

        class _OptionalModel(BaseModel):
            maybe_money: Money | None = extract_field(description="optional money")

        spec = ExtractionSpec.from_pydantic(_OptionalModel)
        (field,) = spec.fields
        assert field.value_kind is ValueKind.MONEY
        assert field.cardinality is Cardinality.OPTIONAL

    def test_list_of_branded_alias_is_many(self) -> None:
        """`list[Money]` is a scalar list — cardinality `many`; VK still `MONEY`."""

        class _ListModel(BaseModel):
            amounts: list[Money] = extract_field(description="many money values")

        spec = ExtractionSpec.from_pydantic(_ListModel)
        (field,) = spec.fields
        assert field.value_kind is ValueKind.MONEY
        assert field.cardinality is Cardinality.MANY


# ---------------------------------------------------------------------------
# no runtime behavior is smuggled in
# ---------------------------------------------------------------------------


class TestTypesModuleHasNoBehavior:
    def test_module_exposes_only_aliases_and_value_kind_import(self) -> None:
        """`extractx.types.__all__` lists the built-in §12 aliases and nothing else."""
        from extractx import types as extractx_types

        assert set(extractx_types.__all__) == {
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
        }

    def test_aliases_are_typing_annotated_instances(self) -> None:
        """each alias is a `typing.Annotated[...]` alias, not a user class."""
        for alias, _, _ in EXPECTED_ALIAS_SHAPE:
            # `Annotated[X, Y]` is a typing special form; it is not a class.
            # (typing._AnnotatedAlias is technically callable — it delegates
            # to the wrapped python type for instantiation. we assert only
            # that no user-defined class is being smuggled in here.)
            assert type(alias).__module__ == "typing", (
                f"{alias!r} has type {type(alias)!r}; must be a typing special form"
            )
            assert not isinstance(alias, type), f"{alias!r} must not be a class"
