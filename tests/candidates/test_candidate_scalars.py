"""candidate scalar coercion tests."""

from __future__ import annotations

from decimal import Decimal

from extractx.candidates.scalars import (
    decimal_from_candidate_value,
    normalized_decimal_hint,
)


def test_decimal_from_candidate_value_accepts_unambiguous_phrasal_money() -> None:
    assert decimal_from_candidate_value("approximately $116.18") == Decimal("116.18")
    assert normalized_decimal_hint("approximately $116.18") == "116.18"


def test_decimal_from_candidate_value_applies_money_magnitude_suffixes() -> None:
    assert decimal_from_candidate_value("$42.1M") == Decimal("42100000")
    assert decimal_from_candidate_value("$42.1 million") == Decimal("42100000")
    value = decimal_from_candidate_value("about $42.1 million aggregate subtotal amount")
    assert value == Decimal("42100000")
    assert decimal_from_candidate_value("US$258M") == Decimal("258000000")
    assert normalized_decimal_hint("$300.1 million") == "300100000"


def test_decimal_from_candidate_value_rejects_ambiguous_multi_number_text() -> None:
    assert decimal_from_candidate_value("8.6073 units per $1,000") is None
    assert normalized_decimal_hint("8.6073 units per $1,000") is None
    assert decimal_from_candidate_value("about $258M and about $42.1M") is None
