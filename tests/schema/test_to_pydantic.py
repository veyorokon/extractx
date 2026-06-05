"""contract tests for pydantic result materialization."""

from __future__ import annotations

from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from extractx import Cardinality, SpecError, ValueKind, extract_field
from extractx.core.anchors import SourceRef, SourceSpan
from extractx.core.objects import GroupingEvidence, InstanceGroupingKey
from extractx.core.outcomes import (
    Evidence,
    ExecutionTrace,
    Extraction,
    Instance,
    ProposalProvenance,
)


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _span(start: int = 0, end: int = 1) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def _key(ordinal: int = 0) -> InstanceGroupingKey:
    return InstanceGroupingKey(
        group_id=f"group-{ordinal}",
        ordinal=ordinal,
        group_anchors=(_span(),),
    )


def _proposal(
    field_id: str,
    normalized_value: object,
    *,
    key: InstanceGroupingKey | None = None,
    offset: int = 0,
) -> Evidence:
    instance_key = key if key is not None else _key()
    return Evidence(
        field_id=field_id,
        instance_key=instance_key,
        raw_value=str(normalized_value),
        evidence_text=str(normalized_value),
        source_span=_span(offset, offset + 1),
        evidence_spans=(),
        normalized_value=normalized_value,
        proposal_provenance=ProposalProvenance(strategy_id="test"),
    )


def _instance(
    proposals: tuple[Evidence, ...] = (),
    *,
    ordinal: int = 0,
) -> Instance:
    return Instance(
        instance_key=_key(ordinal),
        outcome="complete",
        evidence=proposals,
        grouping_evidence=GroupingEvidence(
            stage="resolved",
            anchor_spans=(_span(),),
            producer_version="test",
        ),
    )


def _result(
    instances: tuple[Instance, ...],
    *,
    outcome: Literal["complete", "partial", "failed"] = "complete",
) -> Extraction:
    return Extraction(
        document_id="doc-1",
        spec_version="v1",
        outcome=outcome,
        strategy="independent",
        instances=instances,
        trace=ExecutionTrace(trace_id="trace-1"),
        replay_artifact_ref="",
    )


class _Invoice(BaseModel):
    number: Annotated[str, ValueKind.CARDINAL] = extract_field(description="invoice number")
    total: Annotated[int, ValueKind.CARDINAL] = extract_field(description="total")


class _OptionalInvoice(BaseModel):
    number: Annotated[str, ValueKind.CARDINAL] = extract_field(description="invoice number")
    note: Annotated[str, ValueKind.PERSON] | None = extract_field(description="note")


class _ManyInvoice(BaseModel):
    number: Annotated[str, ValueKind.CARDINAL] = extract_field(description="invoice number")
    tags: list[Annotated[str, ValueKind.PERSON]] = extract_field(description="tags")


class _DefaultedInvoice(BaseModel):
    number: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="invoice number",
        default="missing",
    )


class _LineItem(BaseModel):
    sku: Annotated[str, ValueKind.CARDINAL] = extract_field(description="sku")


class _NestedInvoice(BaseModel):
    line_items: list[_LineItem] = extract_field(description="line items")


class _ExplicitMany(BaseModel):
    alias: Annotated[str, ValueKind.PERSON] = extract_field(
        description="aliases",
        cardinality=Cardinality.MANY,
    )


def test_instance_to_pydantic_materializes_one_instance() -> None:
    instance = _instance((
        _proposal("number", "INV-1"),
        _proposal("total", 42),
    ))

    invoice = instance.to_pydantic(_Invoice)

    assert isinstance(invoice, _Invoice)
    assert invoice.number == "INV-1"
    assert invoice.total == 42


def test_result_to_pydantic_materializes_instances_in_order() -> None:
    first = _instance((_proposal("number", "INV-1"), _proposal("total", 1)), ordinal=0)
    second = _instance((_proposal("number", "INV-2"), _proposal("total", 2)), ordinal=1)

    invoices = _result((first, second)).to_pydantic(_Invoice)

    assert [invoice.number for invoice in invoices] == ["INV-1", "INV-2"]
    assert [invoice.total for invoice in invoices] == [1, 2]


def test_failed_result_with_no_instances_projects_to_empty_list() -> None:
    assert _result((), outcome="failed").to_pydantic(_Invoice) == []


def test_optional_missing_materializes_as_none() -> None:
    invoice = _instance((_proposal("number", "INV-1"),)).to_pydantic(_OptionalInvoice)

    assert invoice.note is None


def test_many_missing_materializes_as_empty_list() -> None:
    invoice = _instance((_proposal("number", "INV-1"),)).to_pydantic(_ManyInvoice)

    assert invoice.tags == []


def test_many_populated_preserves_proposal_order() -> None:
    invoice = _instance((
        _proposal("number", "INV-1"),
        _proposal("tags", "late", offset=1),
        _proposal("tags", "paid", offset=2),
    )).to_pydantic(_ManyInvoice)

    assert invoice.tags == ["late", "paid"]


def test_explicit_many_override_is_honored_for_scalar_annotation() -> None:
    invoice = _instance((
        _proposal("alias", "primary", offset=1),
        _proposal("alias", "secondary", offset=2),
    )).to_pydantic(_ExplicitMany)

    assert invoice.alias == ["primary", "secondary"]


def test_one_missing_required_raises_spec_error() -> None:
    with pytest.raises(SpecError, match=r"^to_pydantic\.missing_required: "):
        _instance(()).to_pydantic(_Invoice)


def test_one_missing_default_is_omitted_so_pydantic_default_applies() -> None:
    invoice = _instance(()).to_pydantic(_DefaultedInvoice)

    assert invoice.number == "missing"


def test_duplicate_one_raises_before_value_is_picked() -> None:
    instance = _instance((
        _proposal("number", "INV-1", offset=1),
        _proposal("number", "INV-2", offset=2),
        _proposal("total", 42),
    ))

    with pytest.raises(SpecError, match=r"^to_pydantic\.cardinality: "):
        instance.to_pydantic(_Invoice)


def test_duplicate_optional_raises_before_value_is_picked() -> None:
    instance = _instance((
        _proposal("number", "INV-1"),
        _proposal("note", "first", offset=1),
        _proposal("note", "second", offset=2),
    ))

    with pytest.raises(SpecError, match=r"^to_pydantic\.cardinality: "):
        instance.to_pydantic(_OptionalInvoice)


def test_unknown_proposal_field_raises_before_pydantic_can_ignore_extra() -> None:
    class _IgnoreExtra(BaseModel):
        model_config = ConfigDict(extra="ignore")

        number: Annotated[str, ValueKind.CARDINAL] = extract_field(
            description="invoice number",
        )

    instance = _instance((
        _proposal("number", "INV-1"),
        _proposal("unexpected", "dropped by pydantic"),
    ))

    with pytest.raises(SpecError, match=r"^to_pydantic\.unknown_field: "):
        instance.to_pydantic(_IgnoreExtra)


def test_per_instance_field_is_unsupported_in_phase_one() -> None:
    with pytest.raises(SpecError, match=r"^to_pydantic\.per_instance_unsupported: "):
        _instance(()).to_pydantic(_NestedInvoice)


def test_pydantic_validators_do_not_run_again() -> None:
    class _NoSecondValidation(BaseModel):
        amount: Annotated[int, ValueKind.CARDINAL] = extract_field(description="amount")

        @field_validator("amount")
        @classmethod
        def _field_validator_would_fail(cls, value: int) -> int:
            raise AssertionError(f"field validator reran for {value!r}")

        @model_validator(mode="after")
        def _model_validator_would_fail(self) -> _NoSecondValidation:
            raise AssertionError("model validator reran")

    materialized = _instance((_proposal("amount", "not an int"),)).to_pydantic(
        _NoSecondValidation,
    )

    assert materialized.amount == "not an int"


def test_invalid_schema_raises_spec_error() -> None:
    with pytest.raises(SpecError, match=r"^to_pydantic\.invalid_schema: "):
        _instance(()).to_pydantic(object)


def test_field_without_extract_field_metadata_raises_spec_error() -> None:
    class _NoExtractField(BaseModel):
        number: Annotated[str, ValueKind.CARDINAL]

    with pytest.raises(SpecError, match=r"^to_pydantic\.invalid_schema: "):
        _instance((_proposal("number", "INV-1"),)).to_pydantic(_NoExtractField)
