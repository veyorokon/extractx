"""selector-binding schema plumbing for ADR-0008 selector ownership."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from extractx import ExtractionSpec, ValueKind, extract_field
from extractx.core.objects import SelectorBinding
from extractx.schema import summarize_spec
from extractx.schema.metadata import ExtractxFieldMetadata


class _Selector:
    pass


def _metadata_from_model(schema_cls: type[BaseModel], field_id: str) -> ExtractxFieldMetadata:
    matches = [
        item
        for item in schema_cls.model_fields[field_id].metadata
        if isinstance(item, ExtractxFieldMetadata)
    ]
    assert len(matches) == 1
    return matches[0]


def test_extract_field_carries_selector_binding_metadata() -> None:
    binding = SelectorBinding(cls=_Selector, params={"model": "fake:model"})

    class Invoice(BaseModel):
        total: Annotated[str, ValueKind.MONEY] = extract_field(
            description="invoice total",
            selector_binding=binding,
        )

    metadata = _metadata_from_model(Invoice, "total")
    assert metadata.selector_binding is binding


def test_from_pydantic_threads_selector_binding_into_fieldspec_and_summary() -> None:
    binding = SelectorBinding(cls=_Selector, params={"model": "fake:model"})

    class Invoice(BaseModel):
        total: Annotated[str, ValueKind.MONEY] = extract_field(
            description="invoice total",
            selector_binding=binding,
        )

    spec = ExtractionSpec.from_pydantic(Invoice)

    assert spec.fields[0].selector_binding == binding

    summary = summarize_spec(spec)
    selector_summary = summary.field_summaries[0].selector_binding_summary
    assert selector_summary is not None
    assert selector_summary.cls_qualname == f"{_Selector.__module__}.{_Selector.__qualname__}"
    assert selector_summary.params == {"model": "fake:model"}
