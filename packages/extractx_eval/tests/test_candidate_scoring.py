from __future__ import annotations

from typing import Annotated, Any

from extractx import ValueKind, extract_field
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.anchors import SourceRef, SourceSpan
from extractx.core.filters import ContextContains
from extractx.core.objects import FilterBinding, StrategyBinding
from pydantic import BaseModel

from extractx_eval import (
    BenchmarkFixture,
    GoldEvidence,
    GoldField,
    GoldInstance,
    MissAttribution,
    SpanMatchConfig,
    score_candidates,
)


def _regex_binding(
    pattern: str,
    *,
    context_window_bytes: int | None = None,
) -> StrategyBinding:
    params: dict[str, Any] = {"pattern": pattern}
    if context_window_bytes is not None:
        params["context_window_bytes"] = context_window_bytes
    return StrategyBinding(cls=RegexCandidateStrategy, params=params, kind="candidate")


class _PhoneBook(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="contact phone number",
        strategy_bindings=(_regex_binding(r"\d{3}-\d{4}"),),
    )


class _FilteredOrder(BaseModel):
    order_id: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="approved order identifier",
        strategy_bindings=(_regex_binding(r"\d+", context_window_bytes=12),),
        filter_binding=FilterBinding(
            expr=ContextContains(all_of=("approved",)),
        ),
    )


class _InvoiceNumber(BaseModel):
    invoice_number: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="invoice number",
        strategy_bindings=(_regex_binding(r"\d+"),),
    )


def _fixture(
    *,
    case_id: str,
    document: str,
    field_id: str,
    evidence: tuple[GoldEvidence, ...],
) -> BenchmarkFixture:
    return BenchmarkFixture(
        case_id=case_id,
        schema_id="test_schema",
        document_path=f"{case_id}.txt",
        document=document,
        expected_instances=(
            GoldInstance(
                fields=(
                    GoldField(
                        field_id=field_id,
                        expected_value=None,
                        evidence=evidence,
                    ),
                ),
            ),
        ),
    )


def _aggregate(report_name: str, report: Any) -> Any:
    return next(aggregate for aggregate in report.aggregates if aggregate.name == report_name)


def _row(report: Any, *, field_id: str, stage: str) -> Any:
    return next(row for row in report.field_rows if row.field_id == field_id and row.stage == stage)


def test_score_candidates_reports_recall_and_precision_for_text_gold() -> None:
    fixture = _fixture(
        case_id="phones",
        document="Call 555-1234 or 555-9999.",
        field_id="phone",
        evidence=(GoldEvidence(text="555-1234"),),
    )

    report = score_candidates(_PhoneBook, (fixture,))

    assert report.case_rows[0].status == "passed"
    assert report.metadata.parameters["span_match"]["mode"] == "overlap"
    assert _aggregate("recall_at_candidates", report).rate == 1.0
    assert _aggregate("recall_at_filtered", report).rate == 1.0
    precision = _aggregate("precision_at_filtered", report)
    assert precision.count == 1
    assert precision.total == 2
    assert precision.rate == 0.5
    observed = _row(report, field_id="phone", stage="candidates").observed
    assert observed["candidates"][0]["strategy_id"].startswith("regex:")


def test_score_candidates_reports_filter_drop_attribution() -> None:
    document = "Order 123." + (" " * 40) + "Approved 456."
    fixture = _fixture(
        case_id="orders",
        document=document,
        field_id="order_id",
        evidence=(GoldEvidence(text="123"),),
    )

    report = score_candidates(_FilteredOrder, (fixture,))

    assert report.case_rows[0].status == "failed"
    filtered = _row(report, field_id="order_id", stage="filtered_candidates")
    assert filtered.gap_kind == "missing_expected"
    assert filtered.attributions[0].kind == "generated_then_filtered"
    assert filtered.attributions[0].details["candidate_text"] == "123"
    assert filtered.attributions[0].details["filter_reason"].startswith("missing all_of")
    drop = filtered.observed["filter_drops"][0]
    assert drop["candidate"]["text"] == "123"
    assert drop["rejected_by"]["kind"] == "context_contains"
    assert "missing all_of" in drop["reason"]


def test_score_candidates_supports_span_gold() -> None:
    document = "Invoice 123 is due."
    span = SourceSpan(
        source_ref=SourceRef(source_id="fixture", content_hash="fixture"),
        text_anchor_space="source_bytes",
        byte_start=document.index("123"),
        byte_end=document.index("123") + len("123"),
    )
    fixture = _fixture(
        case_id="invoice-number",
        document=document,
        field_id="invoice_number",
        evidence=(GoldEvidence(span=span),),
    )

    report = score_candidates(_InvoiceNumber, (fixture,), span_match=SpanMatchConfig(mode="exact"))

    assert report.case_rows[0].status == "passed"
    matches = _row(report, field_id="invoice_number", stage="candidates").observed["matches"]
    assert matches[0]["matches"][0]["matched_via"] == "span"


def test_score_candidates_marks_context_text_fallback() -> None:
    fixture = _fixture(
        case_id="invoice-context",
        document="Invoice 123 is due.",
        field_id="invoice_number",
        evidence=(GoldEvidence(text="Invoice 123"),),
    )

    report = score_candidates(_InvoiceNumber, (fixture,))

    matches = _row(report, field_id="invoice_number", stage="candidates").observed["matches"]
    assert matches[0]["matches"][0]["matched_via"] == "context"


def test_score_candidates_reports_fixture_schema_comparability_failures() -> None:
    fixture = _fixture(
        case_id="unknown-field",
        document="Call 555-1234.",
        field_id="unknown",
        evidence=(GoldEvidence(text="555-1234"),),
    )

    report = score_candidates(_PhoneBook, (fixture,))

    assert report.case_rows[0].status == "comparability_failed"
    row = report.field_rows[0]
    assert row.gap_kind == "comparability_failure"
    assert row.attributions[0].kind == "fixture_schema_mismatch"
    assert row.message is not None
    assert "not present in schema" in row.message


def test_score_candidates_attributes_not_generated_misses() -> None:
    fixture = _fixture(
        case_id="missing-phone",
        document="Call 555-1234.",
        field_id="phone",
        evidence=(GoldEvidence(text="555-0000"),),
    )

    report = score_candidates(_PhoneBook, (fixture,))

    row = _row(report, field_id="phone", stage="candidates")
    assert row.gap_kind == "missing_expected"
    assert row.attributions[0].kind == "not_generated"
    assert row.attributions[0].details["gold_text"] == "555-0000"


def test_score_candidates_attributes_span_near_misses() -> None:
    document = "Invoice 123 is due."
    span = SourceSpan(
        source_ref=SourceRef(source_id="fixture", content_hash="fixture"),
        text_anchor_space="source_bytes",
        byte_start=document.index("Invoice"),
        byte_end=document.index("123") + len("123"),
    )
    fixture = _fixture(
        case_id="near-miss",
        document=document,
        field_id="invoice_number",
        evidence=(GoldEvidence(span=span),),
    )

    report = score_candidates(_InvoiceNumber, (fixture,), span_match=SpanMatchConfig(mode="exact"))

    row = _row(report, field_id="invoice_number", stage="candidates")
    assert row.attributions[0].kind == "span_near_miss"
    assert row.attributions[0].details["candidate_text"] == "123"


def test_miss_attribution_is_serializable() -> None:
    attribution = MissAttribution(
        stage="candidates",
        kind="not_generated",
        field_id="phone",
        reason="missing",
        details={"gold_text": "555-0000"},
    )

    assert "not_generated" in attribution.model_dump_json()


def test_score_candidates_handles_explicit_absent_field_as_false_positive_check() -> None:
    fixture = BenchmarkFixture(
        case_id="absent-phone",
        schema_id="test_schema",
        document_path="absent-phone.txt",
        document="Call 555-1234.",
        expected_instances=(
            GoldInstance(
                fields=(
                    GoldField(
                        field_id="phone",
                        expected_absent=True,
                    ),
                ),
            ),
        ),
    )

    report = score_candidates(_PhoneBook, (fixture,))

    assert report.case_rows[0].status == "failed"
    row = _row(report, field_id="phone", stage="filtered_candidates")
    assert row.gap_kind == "unexpected_observed"
    false_positive = _aggregate("false_positive_at_filtered", report)
    assert false_positive.count == 1
    assert false_positive.total == 1
