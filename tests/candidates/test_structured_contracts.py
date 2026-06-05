"""contract tests for structured candidate status and pydantic contracts."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pytest
from pydantic import AfterValidator, BaseModel, Field

from extractx.candidates import (
    StructuredContractError,
    evaluate_structured_payload,
)
from extractx.core import (
    Candidate,
    PredicateConstraint,
    RangeConstraint,
    SetConstraint,
    SourceRef,
    SourceSpan,
    StructuralFailure,
    StructuralStatus,
)


def _span() -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="doc-1", content_hash="sha256:abc"),
        text_anchor_space="source_bytes",
        byte_start=0,
        byte_end=1,
    )


class InvoiceDateOrTerms:
    name = "InvoiceDateOrTerms"

    def __call__(self, value: str) -> str:
        if value != "As_Of_2024_08_15":
            raise ValueError("context does not match invoice date")
        return value


class StructuredInvoiceTotalContract(BaseModel):
    concept: Literal[
        "invoice:TotalDue",
        "invoice:AmountPayable",
    ]
    unit: Literal["USD", "usd"]
    context: Annotated[str, AfterValidator(InvoiceDateOrTerms())]
    decimals: int = Field(ge=-3, le=0)


def test_text_candidate_must_not_carry_structural_status() -> None:
    with pytest.raises(ValueError, match="text candidates must not carry structural_status"):
        Candidate(
            candidate_id="c1",
            text="x",
            source_span=_span(),
            structural_status=StructuralStatus(passed=True, contract_id="c"),
        )


def test_structured_candidate_requires_structural_status() -> None:
    with pytest.raises(ValueError, match="structured candidates must carry structural_status"):
        Candidate(
            candidate_id="c1",
            text="x",
            source_kind="structured",
            source_id="structured-source",
            source_span=_span(),
            structured_payload={"unit": "USD"},
        )


def test_structural_status_invariants() -> None:
    failure = StructuralFailure(
        field="unit",
        actual="USD",
        expected=SetConstraint(allowed=("USD",)),
    )
    with pytest.raises(ValueError, match="passed=True"):
        StructuralStatus(passed=True, contract_id="c", failures=(failure,))
    with pytest.raises(ValueError, match="passed=False"):
        StructuralStatus(passed=False, contract_id="c")


def test_pydantic_contract_success() -> None:
    status = evaluate_structured_payload(
        {
            "concept": "invoice:TotalDue",
            "unit": "USD",
            "context": "As_Of_2024_08_15",
            "decimals": -2,
        },
        StructuredInvoiceTotalContract,
    )

    assert status.passed is True
    assert status.failures == ()
    assert status.contract_id.endswith(".StructuredInvoiceTotalContract")


def test_pydantic_contract_literal_range_and_predicate_failures() -> None:
    status = evaluate_structured_payload(
        {
            "concept": "invoice:WrongConcept",
            "unit": "EUR",
            "context": "As_Of_2022_01_01",
            "decimals": -5,
        },
        StructuredInvoiceTotalContract,
    )

    assert status.passed is False
    failures = {failure.field: failure for failure in status.failures}
    assert failures["concept"].actual == "invoice:WrongConcept"
    assert failures["concept"].expected == SetConstraint(
        allowed=(
            "invoice:TotalDue",
            "invoice:AmountPayable",
        ),
    )
    assert failures["unit"].expected == SetConstraint(
        allowed=("USD", "usd"),
    )
    assert failures["context"].expected == PredicateConstraint(
        name="InvoiceDateOrTerms",
    )
    assert failures["decimals"].expected == RangeConstraint(
        lo=-3,
        hi=0,
        lo_inclusive=True,
        hi_inclusive=True,
    )


def test_malformed_payload_type_error_is_not_structural_failure() -> None:
    class Contract(BaseModel):
        decimals: int

    with pytest.raises(StructuredContractError, match="unsupported structured contract error type"):
        evaluate_structured_payload({"decimals": object()}, Contract)


def test_structural_failure_round_trips_json() -> None:
    failure = StructuralFailure(
        field="unit",
        actual="EUR",
        expected=SetConstraint(allowed=("USD",)),
    )

    dumped: dict[str, Any] = failure.model_dump(mode="json")
    assert dumped == {
        "field": "unit",
        "actual": "EUR",
        "expected": {"kind": "set", "allowed": ["USD"]},
    }
    assert StructuralFailure.model_validate(dumped) == failure
