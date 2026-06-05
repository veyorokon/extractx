"""invariant tests for `ValueKind` registration semantics.

proof target: `ValueKind.register("NAME")` behaves deterministically and
does not break built-in kinds.
"""

from __future__ import annotations

import pytest

from extractx.core.value_kinds import ValueKind


class TestBuiltInKinds:
    def test_built_in_members_present(self) -> None:
        for name in (
            "MONEY",
            "PERCENT",
            "DATE",
            "ORG",
            "PERSON",
            "GPE",
            "CARDINAL",
            "ORDINAL",
            "BOOL",
        ):
            member = getattr(ValueKind, name)
            assert isinstance(member, ValueKind)
            assert member.name == name

    def test_built_in_members_iterable(self) -> None:
        names = {k.name for k in ValueKind}
        # just confirm the built-ins appear; users may register more.
        assert {"MONEY", "PERCENT", "DATE", "ORG"}.issubset(names)

    def test_built_in_members_unique(self) -> None:
        members = list(ValueKind)
        assert len({m.name for m in members}) == len(members)


class TestRegisterSemantics:
    def test_registration_is_idempotent(self) -> None:
        a = ValueKind.register("MY_NEW_KIND_A")
        b = ValueKind.register("MY_NEW_KIND_A")
        assert a is b

    def test_registration_returns_existing_built_in(self) -> None:
        assert ValueKind.register("MONEY") is ValueKind.MONEY

    def test_re_register_does_not_break_other_kinds(self) -> None:
        snapshot = {k.name: k for k in ValueKind}
        ValueKind.register("MONEY")
        ValueKind.register("PERCENT")
        # snapshot members still compare equal / are still the same objects.
        for name, member in snapshot.items():
            assert getattr(ValueKind, name) is member

    def test_equality_and_hash_by_name(self) -> None:
        a = ValueKind.register("MY_KIND_EQ")
        b = ValueKind.register("MY_KIND_EQ")
        assert a == b
        assert hash(a) == hash(b)
        # distinct kinds compare unequal
        c = ValueKind.register("MY_OTHER_KIND_EQ")
        assert a != c

    def test_instances_are_immutable(self) -> None:
        kind = ValueKind.register("MY_IMMUTABLE_KIND")
        with pytest.raises(AttributeError):
            kind._name = "MUTATED"  # type: ignore[misc]

    def test_contains_check_by_instance_and_name(self) -> None:
        assert ValueKind.MONEY in ValueKind
        assert "MONEY" in ValueKind
        assert "DOES_NOT_EXIST_XYZ" not in ValueKind

    def test_register_annotated_brand(self) -> None:
        from typing import Annotated

        kind = ValueKind.register("MY_ANNOTATED_BRAND")
        # matches the Annotated[pytype, ValueKind.X] shape in §12.
        brand = Annotated[str, kind]
        assert brand.__metadata__ == (kind,)
