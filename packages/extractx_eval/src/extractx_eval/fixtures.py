"""Benchmark fixture contracts for extractx eval primitives.

Fixtures are portable expectations over extractx's public objects: documents,
field IDs, expected normalized values, and expected evidence text or spans.
They deliberately do not carry domain thresholds or product policy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from extractx.core.anchors import SourceSpan
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "BenchmarkFixture",
    "FixturePack",
    "GoldEvidence",
    "GoldField",
    "GoldInstance",
    "load_fixture_pack",
]


class GoldEvidence(BaseModel):
    """Expected grounding for a field.

    Text snippets and byte spans are both first-class. Spans use extractx's
    public `SourceSpan` contract: UTF-8 byte offsets in the declared anchor
    space, normally `source_bytes` for fixture-authored raw documents.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str | None = None
    span: SourceSpan | None = None

    @model_validator(mode="after")
    def _check_any_evidence(self) -> GoldEvidence:
        if self.text is None and self.span is None:
            raise ValueError("GoldEvidence requires text or span")
        return self


class GoldField(BaseModel):
    """Expected field value and grounding expectations.

    `expected_absent=True` is the explicit field-level negative fixture shape:
    the scorer should see no surviving evidence for this field. Empty evidence
    without `expected_absent=True` remains unlabeled for candidate scoring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(min_length=1)
    expected_value: Any = None
    evidence: tuple[GoldEvidence, ...] = ()
    expected_absent: bool = False

    @model_validator(mode="after")
    def _check_absence_shape(self) -> GoldField:
        if self.expected_absent and self.evidence:
            raise ValueError("GoldField.expected_absent=True requires evidence == ()")
        if self.expected_absent and self.expected_value is not None:
            raise ValueError("GoldField.expected_absent=True requires expected_value is None")
        return self


class GoldInstance(BaseModel):
    """Expected fields for one extracted instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str | None = None
    fields: tuple[GoldField, ...]


class BenchmarkFixture(BaseModel):
    """One benchmark case with raw document text and expected instances."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    schema_id: str | None = None
    document_path: str
    document: str
    expected_instances: tuple[GoldInstance, ...]


class FixturePack(BaseModel):
    """Loaded fixture pack from a JSONL case file plus raw document directory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["extractx_eval.fixtures.v1"] = "extractx_eval.fixtures.v1"
    pack_id: str = Field(min_length=1)
    fixtures: tuple[BenchmarkFixture, ...]


class _FixtureLine(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    document_path: str
    schema_id: str | None = None
    expected_instances: tuple[GoldInstance, ...]


def load_fixture_pack(
    cases_jsonl: Path,
    *,
    raw_dir: Path,
    pack_id: str | None = None,
) -> FixturePack:
    """Load benchmark fixtures from JSONL metadata and a raw document directory."""

    root = raw_dir.resolve()
    lines = _read_jsonl(cases_jsonl)
    fixtures: list[BenchmarkFixture] = []
    seen_case_ids: set[str] = set()

    for line_number, raw in lines:
        line = _validate_fixture_line(raw, cases_jsonl=cases_jsonl, line_number=line_number)
        if line.case_id in seen_case_ids:
            raise ValueError(
                "fixture_pack.duplicate_case_id: "
                f"case_id={line.case_id!r} appears more than once",
            )
        seen_case_ids.add(line.case_id)
        document_path = _resolve_document_path(root, line.document_path)
        fixtures.append(
            BenchmarkFixture(
                case_id=line.case_id,
                schema_id=line.schema_id,
                document_path=line.document_path,
                document=_read_document(document_path),
                expected_instances=line.expected_instances,
            ),
        )

    return FixturePack(
        pack_id=pack_id if pack_id is not None else cases_jsonl.stem,
        fixtures=tuple(fixtures),
    )


def _read_jsonl(cases_jsonl: Path) -> tuple[tuple[int, object], ...]:
    try:
        raw_lines = cases_jsonl.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(
            f"fixture_pack.read_failed: could not read cases_jsonl {cases_jsonl}: {exc!s}",
        ) from exc

    parsed: list[tuple[int, object]] = []
    for index, raw_line in enumerate(raw_lines, start=1):
        line = raw_line.strip()
        if line == "":
            continue
        try:
            parsed.append((index, json.loads(line)))
        except json.JSONDecodeError as exc:
            raise ValueError(
                "fixture_pack.malformed_jsonl: "
                f"{cases_jsonl}:{index} is not valid JSON: {exc!s}",
            ) from exc
    return tuple(parsed)


def _validate_fixture_line(
    raw: object,
    *,
    cases_jsonl: Path,
    line_number: int,
) -> _FixtureLine:
    try:
        return _FixtureLine.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            "fixture_pack.malformed_case: "
            f"{cases_jsonl}:{line_number} failed validation: {exc!s}",
        ) from exc


def _resolve_document_path(root: Path, document_path: str) -> Path:
    relative = Path(document_path)
    if relative.is_absolute():
        raise ValueError(
            f"fixture_pack.path_escape: document_path={document_path!r} must be relative",
        )
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "fixture_pack.path_escape: "
            f"document_path={document_path!r} escapes raw_dir",
        ) from exc
    return candidate


def _read_document(document_path: Path) -> str:
    try:
        return document_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            f"fixture_pack.read_failed: could not read document {document_path}: {exc!s}",
        ) from exc
