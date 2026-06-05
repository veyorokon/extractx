"""contract tests for cardinality / `ValueKind` inference per §12.

proof targets:
- cardinality inference table applied correctly for all four rows
  (`X`, `X | None` / `Optional[X]`, `list[X]` for scalar, `list[X]` for submodel).
- explicit `cardinality=` on `extract_field(...)` overrides inference.
- `list[SubModel]` → `PER_INSTANCE`.
- `list[Scalar]` → `MANY`.
- missing or multiple `ValueKind` markers raise `SpecError`.
- `ExtractionSpec.from_pydantic(Cls)` extracts `FieldSpec.value_kind` from
  `Annotated[..., ValueKind.X]`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

import pytest
from pydantic import BaseModel

from extractx import (
    Cardinality,
    ExtractionSpec,
    SpecError,
    ValueKind,
    extract_field,
)

Money = Annotated[Decimal, ValueKind.MONEY]
Org = Annotated[str, ValueKind.ORG]
Percent = Annotated[Decimal, ValueKind.PERCENT]


class TestCardinalityInferenceTable:
    def test_bare_x_is_one(self) -> None:
        class M(BaseModel):
            amount: Money = extract_field(description="amount")

        spec = ExtractionSpec.from_pydantic(M)
        [f] = spec.fields
        assert f.cardinality is Cardinality.ONE
        assert f.value_kind == ValueKind.MONEY

    def test_optional_x_is_optional(self) -> None:
        class M(BaseModel):
            amount: Money | None = extract_field(description="amount", default=None)

        spec = ExtractionSpec.from_pydantic(M)
        [f] = spec.fields
        assert f.cardinality is Cardinality.OPTIONAL

    def test_list_of_scalar_is_many(self) -> None:
        class M(BaseModel):
            amounts: list[Money] = extract_field(description="all amounts", default_factory=list)

        spec = ExtractionSpec.from_pydantic(M)
        [f] = spec.fields
        assert f.cardinality is Cardinality.MANY

    def test_list_of_submodel_is_per_instance(self) -> None:
        class LineItem(BaseModel):
            notional: Money = extract_field(description="notional")

        class Invoice(BaseModel):
            line_items: list[LineItem] = extract_field(
                description="line items",
                default_factory=list,
            )

        spec = ExtractionSpec.from_pydantic(Invoice)
        [f] = spec.fields
        assert f.cardinality is Cardinality.PER_INSTANCE
        assert f.python_type is LineItem


class TestExplicitCardinalityOverride:
    def test_explicit_cardinality_overrides_bare_x_inference(self) -> None:
        class M(BaseModel):
            amount: Money = extract_field(
                description="amount",
                cardinality=Cardinality.OPTIONAL,
                default=None,
            )

        spec = ExtractionSpec.from_pydantic(M)
        [f] = spec.fields
        assert f.cardinality is Cardinality.OPTIONAL

    def test_explicit_many_over_bare(self) -> None:
        class M(BaseModel):
            amount: Money = extract_field(description="amount", cardinality=Cardinality.MANY)

        spec = ExtractionSpec.from_pydantic(M)
        [f] = spec.fields
        assert f.cardinality is Cardinality.MANY


class TestValueKindExtraction:
    def test_value_kind_extracted_from_annotated(self) -> None:
        class M(BaseModel):
            amt: Money = extract_field(description="amount")
            vendor: Org = extract_field(description="vendor")

        spec = ExtractionSpec.from_pydantic(M)
        by_id = {f.field_id: f for f in spec.fields}
        assert by_id["amt"].value_kind == ValueKind.MONEY
        assert by_id["vendor"].value_kind == ValueKind.ORG

    def test_missing_value_kind_raises_spec_error(self) -> None:
        class M(BaseModel):
            amt: Decimal = extract_field(description="amount")

        with pytest.raises(SpecError, match="ValueKind"):
            ExtractionSpec.from_pydantic(M)

    def test_multiple_value_kinds_raises_spec_error(self) -> None:
        double_kind = Annotated[Decimal, ValueKind.MONEY, ValueKind.PERCENT]

        class M(BaseModel):
            amt: double_kind = extract_field(description="amount")  # type: ignore[valid-type]

        with pytest.raises(SpecError, match="multiple ValueKind"):
            ExtractionSpec.from_pydantic(M)

    def test_missing_value_kind_on_list_scalar_raises(self) -> None:
        class M(BaseModel):
            amts: list[Decimal] = extract_field(description="amounts", default_factory=list)

        with pytest.raises(SpecError, match="ValueKind"):
            ExtractionSpec.from_pydantic(M)


class TestPythonTypeExtraction:
    def test_bare_annotation_python_type(self) -> None:
        class M(BaseModel):
            amt: Money = extract_field(description="amount")

        spec = ExtractionSpec.from_pydantic(M)
        [f] = spec.fields
        assert f.python_type is Decimal

    def test_optional_annotation_python_type(self) -> None:
        class M(BaseModel):
            amt: Money | None = extract_field(description="amount", default=None)

        spec = ExtractionSpec.from_pydantic(M)
        [f] = spec.fields
        assert f.python_type is Decimal

    def test_list_scalar_python_type(self) -> None:
        class M(BaseModel):
            amts: list[Money] = extract_field(description="amounts", default_factory=list)

        spec = ExtractionSpec.from_pydantic(M)
        [f] = spec.fields
        assert f.python_type is Decimal


class TestUnsupportedAnnotationShapes:
    def test_set_annotation_rejected(self) -> None:
        class M(BaseModel):
            amts: set[Money] = extract_field(description="amounts", default_factory=set)

        with pytest.raises(SpecError, match="inference table"):
            ExtractionSpec.from_pydantic(M)

    def test_dict_annotation_rejected(self) -> None:
        class M(BaseModel):
            amts: dict[str, Money] = extract_field(description="amounts", default_factory=dict)

        with pytest.raises(SpecError, match="inference table"):
            ExtractionSpec.from_pydantic(M)

    def test_multi_arm_union_rejected(self) -> None:
        class M(BaseModel):
            amt: Money | Percent = extract_field(description="amount")

        with pytest.raises(SpecError, match="union types"):
            ExtractionSpec.from_pydantic(M)
