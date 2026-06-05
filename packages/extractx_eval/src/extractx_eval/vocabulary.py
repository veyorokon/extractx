"""Accessors for the ADR-0008 Instance/Evidence vocabulary."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from extractx.core.anchors import SourceSpan
from extractx.core.objects import InstanceGroupingKey

__all__ = [
    "evidence_spans",
    "evidence_source_text",
    "evidence_value",
    "field_id",
    "instance_evidence",
    "instance_id",
]


def instance_id(instance: object) -> str:
    value = getattr(instance, "instance_id", None)
    if isinstance(value, str):
        return value

    grouping_key = getattr(instance, "instance_key", None)
    if isinstance(grouping_key, InstanceGroupingKey):
        return grouping_key.group_id
    if isinstance(grouping_key, str):
        return grouping_key
    raise TypeError(f"eval.instance_id_unsupported: {type(instance).__name__}")


def instance_evidence(instance: object) -> tuple[object, ...]:
    value = getattr(instance, "evidence", None)
    if value is not None:
        return tuple(cast("Iterable[object]", value))

    raise TypeError(f"eval.instance_evidence_unsupported: {type(instance).__name__}")


def field_id(evidence: object) -> str:
    value = getattr(evidence, "field_id", None)
    if isinstance(value, str):
        return value
    raise TypeError(f"eval.evidence_field_id_unsupported: {type(evidence).__name__}")


def evidence_value(evidence: object) -> Any:
    if not hasattr(evidence, "normalized_value"):
        raise TypeError(f"eval.evidence_value_unsupported: {type(evidence).__name__}")
    return cast("Any", evidence).normalized_value


def evidence_spans(evidence: object) -> tuple[SourceSpan, ...]:
    value = getattr(evidence, "evidence_spans", ())
    if isinstance(value, tuple):
        return cast("tuple[SourceSpan, ...]", value)
    return tuple(cast("Iterable[SourceSpan]", value))


def evidence_source_text(evidence: object) -> str | None:
    source_text = getattr(evidence, "source_text", None)
    if isinstance(source_text, str):
        return source_text
    evidence_text = getattr(evidence, "evidence_text", None)
    if isinstance(evidence_text, str):
        return evidence_text
    return None
