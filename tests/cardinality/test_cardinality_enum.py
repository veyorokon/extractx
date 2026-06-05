"""invariant tests for the `Cardinality` enum.

proof target: the four documented cardinality modes exist and serialize to
their documented string values (lowercase).
"""

from __future__ import annotations

from extractx.core import Cardinality


def test_four_documented_modes() -> None:
    assert {c.value for c in Cardinality} == {"one", "optional", "many", "per_instance"}


def test_value_strings_match_docs() -> None:
    assert Cardinality.ONE.value == "one"
    assert Cardinality.OPTIONAL.value == "optional"
    assert Cardinality.MANY.value == "many"
    assert Cardinality.PER_INSTANCE.value == "per_instance"


def test_cardinality_is_str_enum() -> None:
    # `str` enum inheritance lets callers use `cardinality.value` directly
    # as a json-safe literal without inventing a serializer.
    assert Cardinality.ONE == "one"
