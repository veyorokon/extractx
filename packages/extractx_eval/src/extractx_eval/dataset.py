"""Local JSON smoke-manifest loader for `extractx_eval`.

This loader supports the live smoke surface. Benchmark fixtures use
`fixtures.load_fixture_pack(...)` instead.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from extractx.storage.protocol import ExtractxStore
from pydantic import BaseModel, ConfigDict, ValidationError

from .scoring import ExpectedField, ExpectedInstance
from .smoke import SmokeCase

__all__ = [
    "ExpectedFieldSpec",
    "ExpectedInstanceSpec",
    "SmokeCaseSpec",
    "SmokeDataset",
    "load_smoke_dataset",
]


class ExpectedFieldSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str
    value: Any
    source_text: str | None = None

    def to_expected_field(self) -> ExpectedField:
        return ExpectedField(
            field_id=self.field_id,
            value=self.value,
            source_text=self.source_text,
        )


class ExpectedInstanceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: tuple[ExpectedFieldSpec, ...]

    def to_expected_instance(self) -> ExpectedInstance:
        return ExpectedInstance(
            fields=tuple(field.to_expected_field() for field in self.fields),
        )


class SmokeCaseSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    document_path: str
    schema_id: str
    expected_instances: tuple[ExpectedInstanceSpec, ...]


class SmokeDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["extractx_eval.smoke_dataset.v1"]
    dataset_id: str
    cases: tuple[SmokeCaseSpec, ...]


def load_smoke_dataset(
    manifest_path: Path,
    *,
    schema_registry: Mapping[str, type[BaseModel]],
    store_factory: Callable[[str], ExtractxStore],
    base_dir: Path | None = None,
) -> tuple[SmokeCase, ...]:
    """Load `SmokeCase`s from a local JSON manifest.

    `schema_registry` is explicit so manifests cannot trigger dynamic
    imports. `store_factory` receives each `case_id` and returns the store
    used for that case's persisted replay.
    """

    manifest = _read_manifest(manifest_path)
    root = manifest_path.parent if base_dir is None else base_dir
    _assert_unique_case_ids(manifest)
    cases: list[SmokeCase] = []
    for case_spec in manifest.cases:
        schema = schema_registry.get(case_spec.schema_id)
        if schema is None:
            raise ValueError(
                "smoke_dataset.missing_schema: "
                f"schema_id={case_spec.schema_id!r} is not registered",
            )
        document = _read_document(_resolve_document_path(root, case_spec.document_path))
        cases.append(
            SmokeCase(
                case_id=case_spec.case_id,
                document=document,
                schema=schema,
                store=store_factory(case_spec.case_id),
                expected_instances=tuple(
                    instance.to_expected_instance()
                    for instance in case_spec.expected_instances
                ),
            ),
        )
    return tuple(cases)


def _read_manifest(manifest_path: Path) -> SmokeDataset:
    try:
        raw: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(
            f"smoke_dataset.read_failed: could not read manifest {manifest_path}: {exc!s}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"smoke_dataset.malformed_json: manifest {manifest_path} is not valid JSON: {exc!s}",
        ) from exc

    try:
        return SmokeDataset.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            f"smoke_dataset.malformed: manifest {manifest_path} failed validation: {exc!s}",
        ) from exc


def _assert_unique_case_ids(manifest: SmokeDataset) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for case in manifest.cases:
        if case.case_id in seen:
            duplicates.add(case.case_id)
        seen.add(case.case_id)
    if duplicates:
        raise ValueError(
            "smoke_dataset.duplicate_case_id: duplicate case ids "
            f"{tuple(sorted(duplicates))!r}",
        )


def _resolve_document_path(root: Path, document_path: str) -> Path:
    relative = Path(document_path)
    if relative.is_absolute():
        raise ValueError(
            f"smoke_dataset.path_escape: document_path={document_path!r} must be relative",
        )
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            "smoke_dataset.path_escape: "
            f"document_path={document_path!r} escapes manifest directory",
        ) from exc
    return candidate


def _read_document(document_path: Path) -> str:
    try:
        return document_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            f"smoke_dataset.read_failed: could not read document {document_path}: {exc!s}",
        ) from exc
