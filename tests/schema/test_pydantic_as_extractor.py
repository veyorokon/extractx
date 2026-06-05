"""contract tests for the pydantic-as-extractor prohibition at spec load.

proof target: pydantic-as-extractor prohibition raises `SpecError` on the
detectable bad pattern — a `mode='before'` validator accepting a `str`
annotation. less-detectable patterns (closures whose signature hides the
raw-text intent, validators that call parsing helpers internally without
a typed hint) are intentionally not flagged.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

import pytest
from pydantic import BaseModel, field_validator

from extractx import ExtractionSpec, SpecError, ValueKind, extract_field
from extractx.schema.validators import pydantic_as_extractor_disallowed

Money = Annotated[Decimal, ValueKind.MONEY]


class TestPydanticAsExtractorDetection:
    def test_before_validator_taking_str_raises_spec_error(self) -> None:
        class M(BaseModel):
            total: Money = extract_field(description="total")

            @field_validator("total", mode="before")
            @classmethod
            def parse_total(cls, v: str) -> Decimal:
                # this is the bad pattern — pulling value out of raw text.
                return Decimal(v.replace("$", ""))

        with pytest.raises(SpecError, match="Pydantic-as-Extractor"):
            ExtractionSpec.from_pydantic(M)

    def test_explicit_disallowed_marker_raises(self) -> None:
        class M(BaseModel):
            total: Money = extract_field(description="total")

            @field_validator("total")
            @classmethod
            @pydantic_as_extractor_disallowed
            def bad(cls, v: Decimal) -> Decimal:
                return v

        with pytest.raises(SpecError, match="pydantic_as_extractor_disallowed"):
            ExtractionSpec.from_pydantic(M)

    def test_after_validator_with_decimal_passes(self) -> None:
        """validator operating on a normalized `Decimal` is the legitimate
        seam-F layer-2 use case and must not trigger detection."""

        class M(BaseModel):
            total: Money = extract_field(description="total")

            @field_validator("total")
            @classmethod
            def must_be_positive(cls, v: Decimal) -> Decimal:
                if v <= 0:
                    raise ValueError("must be positive")
                return v

        spec = ExtractionSpec.from_pydantic(M)
        assert len(spec.fields) == 1

    def test_no_validators_is_fine(self) -> None:
        class M(BaseModel):
            total: Money = extract_field(description="total")

        spec = ExtractionSpec.from_pydantic(M)
        assert len(spec.fields) == 1
