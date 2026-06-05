"""prove `_build_independent_context_pack(...)` lands the brief's fixed shape.

every field on the produced `ContextPack` is the brief's documented
phase-1 default; if a future widening drifts the shape it will fail
loudly here.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from extractx import ExtractionSpec, ValueKind, extract_field
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import ContextBudget, StrategyBinding
from extractx.execution.strategies.independent import (
    _build_independent_context_pack,
)


class _Phone(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone number",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )


def test_independent_context_pack_shape_pydantic() -> None:
    spec = ExtractionSpec.from_pydantic(_Phone)
    field_spec = spec.fields[0]
    context_pack = _build_independent_context_pack(spec, field_spec)

    # schema_description: pydantic-backed → spec.source_schema_ref.ref.
    assert spec.source_schema_ref is not None
    assert context_pack.schema_description == spec.source_schema_ref.ref
    assert context_pack.document_summary == ""
    assert dict(context_pack.field_context) == {"phone": "phone number"}
    assert context_pack.prior_proposals == ()
    assert context_pack.retry_feedback == ()
    assert context_pack.bounds == ContextBudget()
    assert context_pack.candidate_overflow is None
