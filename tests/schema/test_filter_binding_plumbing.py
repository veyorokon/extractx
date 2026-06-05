"""filter-binding schema plumbing tests."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from extractx import ExtractionSpec, FilterBinding, LabelIn, ValueKind, extract_field
from extractx.schema import summarize_spec
from extractx.schema.metadata import ExtractxFieldMetadata


def _metadata_from_model(schema_cls: type[BaseModel], field_id: str) -> ExtractxFieldMetadata:
    matches = [
        item
        for item in schema_cls.model_fields[field_id].metadata
        if isinstance(item, ExtractxFieldMetadata)
    ]
    assert len(matches) == 1
    return matches[0]


def test_extract_field_carries_filter_binding_metadata() -> None:
    binding = FilterBinding(expr=LabelIn(labels=("MONEY",)))

    class Invoice(BaseModel):
        total: Annotated[str, ValueKind.MONEY] = extract_field(
            description="invoice total",
            filter_binding=binding,
        )

    metadata = _metadata_from_model(Invoice, "total")
    assert metadata.filter_binding == binding


def test_from_pydantic_threads_filter_binding_into_fieldspec_summary_and_version() -> None:
    binding = FilterBinding(expr=LabelIn(labels=("MONEY",)))

    class Invoice(BaseModel):
        total: Annotated[str, ValueKind.MONEY] = extract_field(
            description="invoice total",
            filter_binding=binding,
        )

    class InvoiceWithoutFilter(BaseModel):
        total: Annotated[str, ValueKind.MONEY] = extract_field(
            description="invoice total",
        )

    spec = ExtractionSpec.from_pydantic(Invoice)
    no_filter_spec = ExtractionSpec.from_pydantic(InvoiceWithoutFilter)

    assert spec.fields[0].filter_binding == binding
    assert spec.version != no_filter_spec.version

    summary = summarize_spec(spec)
    assert summary.field_summaries[0].filter_binding_summary == binding.model_dump(mode="json")
