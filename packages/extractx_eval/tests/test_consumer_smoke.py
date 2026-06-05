from __future__ import annotations

from pathlib import Path

import pytest
from extractx.replay import read_replay
from extractx.storage import LocalFilesystemStore

from extractx_eval import (
    ExpectedField,
    ExpectedInstance,
    InstanceCountMismatch,
    MissingField,
    SmokeCase,
    SmokeReport,
    SmokeResult,
    UnexpectedField,
    ValueCheckResult,
    ValueMismatch,
    smoke_check_values,
    smoke_run,
)

from .invoice_schema import InvoiceSummary


def _fixture_text() -> str:
    return (
        Path(__file__).parent / "fixtures" / "acme_invoice_excerpt.txt"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_consumer_shaped_invoice_smoke(tmp_path: Path) -> None:
    store = LocalFilesystemStore(tmp_path)
    expected = ExpectedInstance(
        fields=(
            ExpectedField(field_id="vendor_id", value="VND-1001"),
            ExpectedField(field_id="invoice_date", value="April 13, 2020"),
            ExpectedField(field_id="due_date", value="April 28, 2020"),
            ExpectedField(field_id="tax_rate", value="2.25%"),
            ExpectedField(field_id="total_amount", value="$220.18"),
            ExpectedField(field_id="subtotal_amount", value="$700.00"),
        ),
    )
    case = SmokeCase(
        case_id="consumer-acme-invoice",
        document=_fixture_text(),
        schema=InvoiceSummary,
        store=store,
        expected_instances=(expected,),
    )

    result = await smoke_run(case)

    assert result.error is None
    assert result.run_status == "completed"
    assert result.extraction_outcome == "complete"
    assert result.replay_artifact_ref is not None
    value_check = smoke_check_values(result, case)
    assert value_check.status == "matched"
    assert value_check.misses == ()

    artifact = read_replay(store, result.replay_artifact_ref)
    assert artifact.producer_versions == result.producer_versions


def test_consumer_smoke_does_not_publish_domain_identity() -> None:
    assert "account_id" not in InvoiceSummary.model_fields
    assert "account_id" not in SmokeResult.model_fields
    assert "account_id" not in SmokeReport.model_fields
    assert "account_id" not in ValueCheckResult.model_fields
    assert "account_id" not in MissingField.model_fields
    assert "account_id" not in UnexpectedField.model_fields
    assert "account_id" not in ValueMismatch.model_fields
    assert "account_id" not in InstanceCountMismatch.model_fields
    assert "CorrelationContext" not in _fixture_text()
