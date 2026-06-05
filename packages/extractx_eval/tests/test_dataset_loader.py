from __future__ import annotations

import json
from pathlib import Path

import pytest
from extractx.storage import LocalFilesystemStore
from pydantic import BaseModel

from extractx_eval import load_smoke_dataset, smoke_run_and_check

from .invoice_schema import InvoiceSummary


def _manifest_path() -> Path:
    return Path(__file__).parent / "fixtures" / "shared_invoice_v1.json"


def _schema_registry() -> dict[str, type[BaseModel]]:
    return {"invoice_summary_v1": InvoiceSummary}


@pytest.mark.asyncio
async def test_manifest_backed_dataset_runs_shared_invoice_case(
    tmp_path: Path,
) -> None:
    cases = load_smoke_dataset(
        _manifest_path(),
        schema_registry=_schema_registry(),
        store_factory=lambda case_id: LocalFilesystemStore(tmp_path / case_id),
    )

    assert tuple(case.case_id for case in cases) == (
        "acme-2020-invoice",
        "brightpath-2020-invoice",
        "cascade-2023-invoice",
    )

    report = await smoke_run_and_check(cases)

    assert report.total_cases == 3
    assert report.total_value_mismatches == 0
    assert report.total_errors == 0
    by_case_id = {result.case_id: result for result in report.smoke_results}
    assert set(by_case_id) == {
        "acme-2020-invoice",
        "brightpath-2020-invoice",
        "cascade-2023-invoice",
    }
    for result in by_case_id.values():
        assert result.run_status == "completed"
        assert result.extraction_outcome == "complete"
        assert result.replay_artifact_ref != ""
    checks_by_case_id = {check.case_id: check for check in report.value_checks}
    assert set(checks_by_case_id) == set(by_case_id)
    assert all(check.status == "matched" for check in checks_by_case_id.values())
    assert all(check.misses == () for check in checks_by_case_id.values())


def test_loader_rejects_unregistered_schema_ref(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"^smoke_dataset\.missing_schema: "):
        load_smoke_dataset(
            _manifest_path(),
            schema_registry={},
            store_factory=lambda case_id: LocalFilesystemStore(tmp_path / case_id),
        )


def test_loader_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    manifest: dict[str, object] = {
        "schema_version": "extractx_eval.smoke_dataset.v1",
        "dataset_id": "duplicate-cases",
        "cases": [
            {
                "case_id": "duplicate",
                "document_path": "acme_invoice_excerpt.txt",
                "schema_id": "invoice_summary_v1",
                "expected_instances": [],
            },
            {
                "case_id": "duplicate",
                "document_path": "acme_invoice_excerpt.txt",
                "schema_id": "invoice_summary_v1",
                "expected_instances": [],
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=r"^smoke_dataset\.duplicate_case_id: "):
        load_smoke_dataset(
            path,
            schema_registry=_schema_registry(),
            store_factory=lambda case_id: LocalFilesystemStore(tmp_path / case_id),
            base_dir=_manifest_path().parent,
        )


def test_loader_rejects_document_path_escape(tmp_path: Path) -> None:
    manifest: dict[str, object] = {
        "schema_version": "extractx_eval.smoke_dataset.v1",
        "dataset_id": "path-escape",
        "cases": [
            {
                "case_id": "escape",
                "document_path": "../outside.txt",
                "schema_id": "invoice_summary_v1",
                "expected_instances": [],
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=r"^smoke_dataset\.path_escape: "):
        load_smoke_dataset(
            path,
            schema_registry=_schema_registry(),
            store_factory=lambda case_id: LocalFilesystemStore(tmp_path / case_id),
        )


def test_manifest_does_not_carry_domain_correlation_fields() -> None:
    manifest = _manifest_path().read_text(encoding="utf-8")

    assert "account_id" not in manifest
    assert "CorrelationContext" not in manifest
