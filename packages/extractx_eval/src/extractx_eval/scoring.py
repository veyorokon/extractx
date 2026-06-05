"""Exact final-value comparison for canonical extractx outputs.

The scorer compares expected fixture instances to canonical
`Extraction.instances`. It is a deterministic value-check projection only: it
does not rank candidates, select evidence, resolve instances, or feed back into
the runtime extraction path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, cast

from extractx.core.anchors import SourceSpan
from pydantic import BaseModel, ConfigDict

from . import vocabulary

type ValueDiffKind = Literal[
    "missing_field",
    "unexpected_field",
    "value_mismatch",
    "instance_count_mismatch",
]

VALUE_DIFF_KINDS: tuple[ValueDiffKind, ...] = (
    "missing_field",
    "unexpected_field",
    "value_mismatch",
    "instance_count_mismatch",
)


@dataclass(frozen=True)
class ExpectedField:
    field_id: str
    value: object
    source_text: str | None = None


@dataclass(frozen=True)
class ExpectedInstance:
    fields: tuple[ExpectedField, ...]


class _ValueDiffBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    replay_artifact_ref: str = ""


class MissingField(_ValueDiffBase):
    kind: Literal["missing_field"] = "missing_field"
    instance_index: int
    instance_id: str
    field_id: str
    expected: Any
    source_text: str | None = None


class UnexpectedField(_ValueDiffBase):
    kind: Literal["unexpected_field"] = "unexpected_field"
    instance_index: int
    instance_id: str
    field_id: str
    actual: Any
    evidence_spans: tuple[SourceSpan, ...] = ()
    source_text: str | None = None


class ValueMismatch(_ValueDiffBase):
    kind: Literal["value_mismatch"] = "value_mismatch"
    instance_index: int
    instance_id: str
    field_id: str
    expected: Any
    actual: Any
    evidence_spans: tuple[SourceSpan, ...] = ()
    source_text: str | None = None


class InstanceCountMismatch(_ValueDiffBase):
    kind: Literal["instance_count_mismatch"] = "instance_count_mismatch"
    expected_count: int
    actual_count: int


type ValueDiff = MissingField | UnexpectedField | ValueMismatch | InstanceCountMismatch


def score_instances(
    *,
    case_id: str,
    expected: tuple[ExpectedInstance, ...],
    actual: tuple[object, ...],
    replay_artifact_ref: str = "",
) -> tuple[ValueDiff, ...]:
    """Compare expected fixture instances to canonical extracted instances."""

    misses: list[ValueDiff] = []
    if len(expected) != len(actual):
        misses.append(
            InstanceCountMismatch(
                case_id=case_id,
                replay_artifact_ref=replay_artifact_ref,
                expected_count=len(expected),
                actual_count=len(actual),
            ),
        )

    for instance_index, (expected_instance, actual_instance) in enumerate(
        zip(expected, actual, strict=False),
    ):
        misses.extend(
            _score_instance(
                case_id=case_id,
                replay_artifact_ref=replay_artifact_ref,
                instance_index=instance_index,
                expected=expected_instance,
                actual=actual_instance,
            ),
        )
    return tuple(misses)


def _score_instance(
    *,
    case_id: str,
    replay_artifact_ref: str,
    instance_index: int,
    expected: ExpectedInstance,
    actual: object,
) -> tuple[ValueDiff, ...]:
    misses: list[ValueDiff] = []
    actual_instance_id = vocabulary.instance_id(actual)
    expected_by_field = {field.field_id: field for field in expected.fields}
    actual_by_field = {
        vocabulary.field_id(evidence): evidence for evidence in vocabulary.instance_evidence(actual)
    }

    for field_id, expected_field in expected_by_field.items():
        actual_field = actual_by_field.get(field_id)
        if actual_field is None:
            misses.append(
                MissingField(
                    case_id=case_id,
                    replay_artifact_ref=replay_artifact_ref,
                    instance_index=instance_index,
                    instance_id=actual_instance_id,
                    field_id=field_id,
                    expected=json_safe(expected_field.value),
                    source_text=expected_field.source_text,
                ),
            )
            continue

        expected_value = json_safe(expected_field.value)
        actual_value = json_safe(vocabulary.evidence_value(actual_field))
        if expected_value != actual_value:
            misses.append(
                ValueMismatch(
                    case_id=case_id,
                    replay_artifact_ref=replay_artifact_ref,
                    instance_index=instance_index,
                    instance_id=actual_instance_id,
                    field_id=field_id,
                    expected=expected_value,
                    actual=actual_value,
                    evidence_spans=vocabulary.evidence_spans(actual_field),
                    source_text=vocabulary.evidence_source_text(actual_field),
                ),
            )

    for field_id, actual_field in actual_by_field.items():
        if field_id not in expected_by_field:
            misses.append(
                UnexpectedField(
                    case_id=case_id,
                    replay_artifact_ref=replay_artifact_ref,
                    instance_index=instance_index,
                    instance_id=actual_instance_id,
                    field_id=field_id,
                    actual=json_safe(vocabulary.evidence_value(actual_field)),
                    evidence_spans=vocabulary.evidence_spans(actual_field),
                    source_text=vocabulary.evidence_source_text(actual_field),
                ),
            )
    return tuple(misses)


def json_safe(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        tuple_value = cast("tuple[object, ...]", value)
        return tuple(json_safe(item) for item in tuple_value)
    if isinstance(value, list):
        list_value = cast("list[object]", value)
        return tuple(json_safe(item) for item in list_value)
    if isinstance(value, dict):
        mapping_value = cast("dict[object, object]", value)
        return {
            str(key): json_safe(item)
            for key, item in sorted(
                mapping_value.items(),
                key=lambda pair: str(pair[0]),
            )
        }
    return value
