from __future__ import annotations

import json
from pathlib import Path

import pytest
from extractx.core.anchors import SourceRef, SourceSpan

from extractx_eval import (
    BenchmarkFixture,
    FixturePack,
    GoldEvidence,
    GoldField,
    GoldInstance,
    load_fixture_pack,
)


def _source_ref() -> SourceRef:
    return SourceRef(source_id="invoice-1", content_hash="sha256:test")


def _source_span() -> SourceSpan:
    return SourceSpan(
        source_ref=_source_ref(),
        text_anchor_space="source_bytes",
        byte_start=8,
        byte_end=14,
    )


def test_gold_evidence_accepts_text_only_as_first_class_fixture_shape() -> None:
    evidence = GoldEvidence(text="$12.34")

    assert evidence.text == "$12.34"
    assert evidence.span is None


def test_gold_evidence_accepts_source_span() -> None:
    span = _source_span()

    evidence = GoldEvidence(span=span)

    assert evidence.text is None
    assert evidence.span == span


def test_gold_evidence_requires_text_or_span() -> None:
    with pytest.raises(ValueError, match="GoldEvidence requires text or span"):
        GoldEvidence()


def test_fixture_pack_models_are_json_serializable() -> None:
    fixture = BenchmarkFixture(
        case_id="invoice-1",
        schema_id="invoice_summary_v1",
        document_path="raw/invoice-1.txt",
        document="Total: $12.34",
        expected_instances=(
            GoldInstance(
                fields=(
                    GoldField(
                        field_id="total",
                        expected_value="$12.34",
                        evidence=(
                            GoldEvidence(text="$12.34"),
                            GoldEvidence(span=_source_span()),
                        ),
                    ),
                ),
            ),
        ),
    )
    pack = FixturePack(pack_id="invoices", fixtures=(fixture,))

    dumped = pack.model_dump_json()
    loaded = FixturePack.model_validate_json(dumped)

    assert loaded == pack


def test_load_fixture_pack_reads_jsonl_and_raw_documents(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "invoice-1.txt").write_text("Vendor: Ada\nTotal: $12.34\n", encoding="utf-8")
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps(
            {
                "case_id": "invoice-1",
                "schema_id": "invoice_summary_v1",
                "document_path": "invoice-1.txt",
                "expected_instances": [
                    {
                        "fields": [
                            {
                                "field_id": "total",
                                "expected_value": "$12.34",
                                "evidence": [{"text": "$12.34"}],
                            },
                        ],
                    },
                ],
            },
        )
        + "\n",
        encoding="utf-8",
    )

    pack = load_fixture_pack(cases, raw_dir=raw_dir)

    assert pack.pack_id == "cases"
    assert len(pack.fixtures) == 1
    fixture = pack.fixtures[0]
    assert fixture.case_id == "invoice-1"
    assert fixture.document == "Vendor: Ada\nTotal: $12.34\n"
    assert fixture.expected_instances[0].fields[0].evidence[0].text == "$12.34"


def test_load_fixture_pack_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "invoice.txt").write_text("Total: $12.34", encoding="utf-8")
    row: dict[str, object] = {
        "case_id": "duplicate",
        "document_path": "invoice.txt",
        "expected_instances": [],
    }
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"^fixture_pack\.duplicate_case_id: "):
        load_fixture_pack(cases, raw_dir=raw_dir)


def test_load_fixture_pack_rejects_document_path_escape(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps(
            {
                "case_id": "escape",
                "document_path": "../outside.txt",
                "expected_instances": [],
            },
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"^fixture_pack\.path_escape: "):
        load_fixture_pack(cases, raw_dir=raw_dir)
