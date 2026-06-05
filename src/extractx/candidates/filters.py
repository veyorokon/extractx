"""candidate-set filter evaluation.

This module executes the declarative filter AST from `extractx.core.filters`.
It owns evaluation semantics only; filter declarations remain core objects so
schema loading, summaries, and replay can serialize them without importing a
strategy implementation.
"""

from __future__ import annotations

from decimal import Decimal

from extractx.core.filters import (
    And,
    ContainedBy,
    Contains,
    ContextContains,
    FilterExpr,
    LabelIn,
    LabelNotIn,
    Not,
    NumericRange,
    Or,
)
from extractx.core.objects import Candidate, CandidateSet, FilterBinding

from .scalars import decimal_from_candidate_value

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
    "apply_filter_binding",
    "filter_candidate_set",
]


def apply_filter_binding(
    candidate_set: CandidateSet,
    binding: FilterBinding,
) -> CandidateSet:
    """apply a field filter binding."""

    return filter_candidate_set(candidate_set, binding.expr)


def filter_candidate_set(candidate_set: CandidateSet, expr: FilterExpr) -> CandidateSet:
    """return a copy of `candidate_set` containing only candidates matching `expr`."""

    kept = tuple(
        candidate
        for candidate in candidate_set.candidates
        if _matches(candidate=candidate, expr=expr, candidate_set=candidate_set)
    )
    return candidate_set.model_copy(update={"candidates": kept})


def _matches(*, candidate: Candidate, expr: FilterExpr, candidate_set: CandidateSet) -> bool:
    if isinstance(expr, LabelIn):
        return candidate.entity_type in expr.labels
    if isinstance(expr, LabelNotIn):
        return candidate.entity_type not in expr.labels
    if isinstance(expr, ContainedBy):
        return any(
            _label_matches(other, expr.label) and _span_contains(outer=other, inner=candidate)
            for other in candidate_set.candidates
            if other.candidate_id != candidate.candidate_id
        )
    if isinstance(expr, Contains):
        return any(
            _label_matches(other, expr.label) and _span_contains(outer=candidate, inner=other)
            for other in candidate_set.candidates
            if other.candidate_id != candidate.candidate_id
        )
    if isinstance(expr, NumericRange):
        return _numeric_in_range(candidate, expr)
    if isinstance(expr, ContextContains):
        return _context_contains(candidate, expr)
    if isinstance(expr, And):
        return all(
            _matches(candidate=candidate, expr=e, candidate_set=candidate_set)
            for e in expr.exprs
        )
    if isinstance(expr, Or):
        return any(
            _matches(candidate=candidate, expr=e, candidate_set=candidate_set)
            for e in expr.exprs
        )
    return not _matches(candidate=candidate, expr=expr.expr, candidate_set=candidate_set)


def _label_matches(candidate: Candidate, label: str | None) -> bool:
    return label is None or candidate.entity_type == label


def _span_contains(*, outer: Candidate, inner: Candidate) -> bool:
    outer_span = outer.source_span
    inner_span = inner.source_span
    if outer_span.source_ref != inner_span.source_ref:
        return False
    if outer_span.text_anchor_space != inner_span.text_anchor_space:
        return False
    contains = (
        outer_span.byte_start <= inner_span.byte_start
        and outer_span.byte_end >= inner_span.byte_end
    )
    same_span = (
        outer_span.byte_start == inner_span.byte_start
        and outer_span.byte_end == inner_span.byte_end
    )
    return contains and not same_span


def _numeric_in_range(candidate: Candidate, expr: NumericRange) -> bool:
    value = _candidate_decimal(candidate)
    if value is None:
        return False
    if expr.lo is not None:
        lo = Decimal(expr.lo)
        if value < lo or (value == lo and not expr.include_lo):
            return False
    if expr.hi is not None:
        hi = Decimal(expr.hi)
        if value > hi or (value == hi and not expr.include_hi):
            return False
    return True


def _candidate_decimal(candidate: Candidate) -> Decimal | None:
    raw = candidate.normalized_hint
    if raw is None:
        raw = candidate.text
    return decimal_from_candidate_value(raw)


def _context_contains(candidate: Candidate, expr: ContextContains) -> bool:
    haystack = candidate.context if expr.case_sensitive else candidate.context.casefold()
    any_of = expr.any_of if expr.case_sensitive else tuple(s.casefold() for s in expr.any_of)
    all_of = expr.all_of if expr.case_sensitive else tuple(s.casefold() for s in expr.all_of)
    if any_of and not any(needle in haystack for needle in any_of):
        return False
    return not (all_of and not all(needle in haystack for needle in all_of))
