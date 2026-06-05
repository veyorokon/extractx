"""candidate-level scalar coercion helpers.

These helpers are shared by seam C producers and C.filter evaluation. They are
not field validation; seam F remains the owner of final normalized values.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

__all__ = [
    "decimal_from_candidate_value",
    "normalized_decimal_hint",
]


_NUMERIC_TOKEN_RE = re.compile(
    r"""
    (?<![\w.])
    (?P<sign>-?)
    (?:US\$|U\.S\.\$|[$€£¥])?\s*
    (?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)
    (?P<percent>%?)
    (?:
        \s*
        (?P<magnitude>
            trillion|billion|million|thousand|
            mln|mm|mn|tn|bn|k|m|b|t
        )
    )?
    (?![\w.])
    """,
    re.IGNORECASE | re.VERBOSE,
)
_MAGNITUDE_MULTIPLIERS = {
    "k": Decimal("1000"),
    "thousand": Decimal("1000"),
    "m": Decimal("1000000"),
    "mm": Decimal("1000000"),
    "mn": Decimal("1000000"),
    "mln": Decimal("1000000"),
    "million": Decimal("1000000"),
    "b": Decimal("1000000000"),
    "bn": Decimal("1000000000"),
    "billion": Decimal("1000000000"),
    "t": Decimal("1000000000000"),
    "tn": Decimal("1000000000000"),
    "trillion": Decimal("1000000000000"),
}


def decimal_from_candidate_value(raw: object) -> Decimal | None:
    """Return an unambiguous decimal from a candidate value or hint.

    Full-token numeric strings are accepted directly. Phrasal strings are only
    accepted when exactly one numeric token is present, so filters do not guess
    between competing values in evidence such as "8.6073 units per $1,000".
    """

    full_match = _NUMERIC_TOKEN_RE.fullmatch(str(raw).strip())
    if full_match is not None:
        try:
            return _decimal_from_match(full_match)
        except (InvalidOperation, ValueError):
            return None

    text = str(raw)
    matches = tuple(_NUMERIC_TOKEN_RE.finditer(text))
    if len(matches) != 1:
        return None
    try:
        return _decimal_from_match(matches[0])
    except (InvalidOperation, ValueError):
        return None


def normalized_decimal_hint(raw: object) -> str | None:
    """Return a JSON-safe decimal hint when `raw` has one unambiguous number."""

    value = decimal_from_candidate_value(raw)
    if value is None:
        return None
    return format(value.normalize(), "f")


def _decimal_from_match(match: re.Match[str]) -> Decimal:
    value = Decimal(f"{match.group('sign')}{match.group('number').replace(',', '')}")
    magnitude = match.group("magnitude")
    if magnitude is None:
        return value
    if match.group("percent"):
        raise ValueError("percent tokens cannot carry magnitude suffixes")
    return value * _MAGNITUDE_MULTIPLIERS[magnitude.casefold()]
