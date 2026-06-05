"""contract tests for `extract_field(...)` typed metadata attachment.

proof targets (from the seam-B brief):
- `extract_field(...)` returns a `pydantic.fields.FieldInfo` with a typed
  `ExtractxFieldMetadata` attached — not a raw dict stuffed into
  `json_schema_extra`.
- `extract_field(...)` behaves as a pydantic field declaration helper —
  the resulting model still validates and exposes fields normally.
- top-level `from extractx import extract_field` works.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel
from pydantic.fields import FieldInfo

import extractx
from extractx import ValueKind, extract_field
from extractx.schema.metadata import ExtractxFieldMetadata

Money = Annotated[Decimal, ValueKind.MONEY]
Org = Annotated[str, ValueKind.ORG]


def _get_extractx_metadata(info: FieldInfo) -> ExtractxFieldMetadata:
    matches = [m for m in info.metadata if isinstance(m, ExtractxFieldMetadata)]
    assert len(matches) == 1, "expected exactly one ExtractxFieldMetadata on FieldInfo"
    return matches[0]


class TestTopLevelExtractFieldExport:
    def test_top_level_import(self) -> None:
        assert extract_field is extractx.extract_field

    def test_top_level_listed_in_all(self) -> None:
        assert "extract_field" in extractx.__all__


class TestExtractFieldAttachesTypedMetadata:
    def test_returns_pydantic_field_info(self) -> None:
        info = extract_field(description="amount")
        assert isinstance(info, FieldInfo)

    def test_metadata_is_typed_container(self) -> None:
        info = extract_field(description="amount")
        metadata = _get_extractx_metadata(info)
        assert isinstance(metadata, ExtractxFieldMetadata)
        assert metadata.description == "amount"

    def test_metadata_not_in_json_schema_extra(self) -> None:
        """the typed metadata lives on `FieldInfo.metadata` (typed list),
        not in `json_schema_extra` (avoids the `Raw-Payload Escape Hatch`
        anti-pattern)."""

        info = extract_field(description="amount")
        extra = info.json_schema_extra
        if isinstance(extra, dict):
            assert "extractx_metadata" not in extra
            assert "__extractx_metadata__" not in extra

    def test_metadata_carries_all_kwargs(self) -> None:
        info = extract_field(
            description="amount",
            priority=5,
            depends_on=("currency",),
        )
        metadata = _get_extractx_metadata(info)
        assert metadata.priority == 5
        assert metadata.depends_on == ("currency",)


class TestExtractFieldBehavesAsPydanticField:
    def test_field_declaration_model_validates(self) -> None:
        class Invoice(BaseModel):
            total: Money = extract_field(description="total due")
            vendor: Org = extract_field(description="billing org")

        inv = Invoice(total=Decimal("10.50"), vendor="ACME Inc")
        assert inv.total == Decimal("10.50")
        assert inv.vendor == "ACME Inc"

    def test_default_value_flows_through(self) -> None:
        class Config(BaseModel):
            label: Org = extract_field(description="label", default="untitled")

        c = Config()
        assert c.label == "untitled"

    def test_description_flows_to_pydantic_field(self) -> None:
        class M(BaseModel):
            x: Org = extract_field(description="x-description")

        info = M.model_fields["x"]
        assert info.description == "x-description"
