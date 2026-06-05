from __future__ import annotations

import json
from pathlib import Path

import pytest

from extractx_eval.cli import load_schema_registry, main

from .invoice_schema import InvoiceSummary


def _manifest_path() -> Path:
    return Path(__file__).parent / "fixtures" / "shared_invoice_v1.json"


def test_cli_run_manifest_outputs_report(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "run",
            str(_manifest_path()),
            "--schema",
            "invoice_summary_v1=tests.invoice_schema:InvoiceSummary",
            "--store-root",
            str(tmp_path / "stores"),
        ],
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert report["total_cases"] == 3
    assert report["total_value_mismatches"] == 0
    assert report["total_errors"] == 0
    assert tuple(result["case_id"] for result in report["smoke_results"]) == (
        "acme-2020-invoice",
        "brightpath-2020-invoice",
        "cascade-2023-invoice",
    )


def test_cli_schema_registry_loads_explicit_schema() -> None:
    registry = load_schema_registry(
        ("invoice_summary_v1=tests.invoice_schema:InvoiceSummary",),
    )

    assert registry == {"invoice_summary_v1": InvoiceSummary}


def test_cli_schema_registry_rejects_duplicate_schema_id() -> None:
    with pytest.raises(SystemExit, match=r"^eval_cli\.duplicate_schema: "):
        load_schema_registry(
            (
                "invoice_summary_v1=tests.invoice_schema:InvoiceSummary",
                "invoice_summary_v1=tests.invoice_schema:InvoiceSummary",
            ),
        )


def test_cli_schema_registry_rejects_non_pydantic_target() -> None:
    with pytest.raises(SystemExit, match=r"^eval_cli\.invalid_schema: "):
        load_schema_registry(("bad=json:loads",))
