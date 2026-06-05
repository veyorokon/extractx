"""`SpecSummary` round-trip and `summarize_spec` shape tests.

per docs/tasks/m9-phase-1-replay-storage-skeleton.md §2 / §9. the
phase-1 round-trip claim is explicitly **on `SpecSummary` only** —
`ExtractionSpec` itself is not round-trippable in phase 1 (drift §3
of the M9 phase-1 brief). these tests assert that downgrade honestly.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import BaseModel

from extractx import ValueKind, extract_field
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.cardinality import Cardinality
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import (
    BudgetSpec,
    DistanceMetric,
    ExtractionSpec,
    FieldSpec,
    GroupingPolicy,
    InstanceProposerBinding,
    PromptPolicy,
    StrategyBinding,
    ValidationBinding,
    ValidationPolicy,
)
from extractx.core.versions import stable_hash
from extractx.schema import SpecSummary, summarize_spec


class _Phone(BaseModel):
    phone: Annotated[str, ValueKind.PERSON] = extract_field(
        description="phone",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
    )


class _FakeInstanceProposer:
    pass


def _identity_normalizer(raw: Any) -> Any:
    return raw


def _build_manual_spec() -> ExtractionSpec:
    field = FieldSpec(
        field_id="phone",
        description="phone number",
        value_kind=ValueKind.PERSON,
        cardinality=Cardinality.ONE,
        priority=0,
        depends_on=(),
        python_type=str,
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{3}-\d{4}"},
                kind="candidate",
            ),
        ),
        validation_binding=ValidationBinding(
            normalizer=_identity_normalizer,
            field_validators=(),
        ),
    )
    fields = (field,)
    payload = {
        "manual": True,
        "fields": [{"field_id": f.field_id, "cardinality": f.cardinality.value} for f in fields],
    }
    return ExtractionSpec(
        fields=fields,
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="default"),
        ),
        budget=BudgetSpec(),
        version=stable_hash(payload),
        source_schema_ref=None,
    )


def test_summarize_spec_pydantic_round_trip() -> None:
    """`summarize_spec → blob → SpecSummary → blob` is byte-equal.

    proves the M9 phase-1 brief's `SpecSummary` round-trip target.
    """

    spec = ExtractionSpec.from_pydantic(_Phone)
    summary1 = summarize_spec(spec)
    blob1 = summary1.model_dump_json().encode("utf-8")

    summary2 = SpecSummary.model_validate_json(blob1)
    blob2 = summary2.model_dump_json().encode("utf-8")

    assert blob1 == blob2
    assert summary1 == summary2
    assert summary1.instance_type == "_Phone"
    assert summary1.instance_cardinality is Cardinality.ONE
    assert summary1.instance_proposer_binding_summary is None


def test_summarize_spec_manual_round_trip() -> None:
    """manual spec also round-trips through `SpecSummary`."""

    spec = _build_manual_spec()
    summary1 = summarize_spec(spec)
    blob1 = summary1.model_dump_json().encode("utf-8")

    summary2 = SpecSummary.model_validate_json(blob1)
    blob2 = summary2.model_dump_json().encode("utf-8")

    assert blob1 == blob2
    assert summary1 == summary2


def test_summarize_spec_instance_proposer_binding_summary() -> None:
    field = _build_manual_spec().fields[0]
    spec = ExtractionSpec(
        fields=(field,),
        instance_type="ReceiptRecord",
        instance_cardinality=Cardinality.MANY,
        instance_proposer_binding=InstanceProposerBinding(
            cls=_FakeInstanceProposer,
            params={"model_id": "fake:model"},
        ),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="default"),
        ),
        budget=BudgetSpec(),
        version="manual-v1",
        source_schema_ref=None,
    )

    summary = summarize_spec(spec)

    assert summary.instance_type == "ReceiptRecord"
    assert summary.instance_cardinality is Cardinality.MANY
    proposer_summary = summary.instance_proposer_binding_summary
    assert proposer_summary is not None
    assert proposer_summary.cls_qualname.endswith("_FakeInstanceProposer")
    assert proposer_summary.params == {"model_id": "fake:model"}


def test_summarize_spec_field_summary_carries_qualnames() -> None:
    """`FieldSummary.python_type_qualname` is a `module.qualname`
    string. live class refs become qualname strings (M9 phase-1 hard
    pin #2)."""

    spec = _build_manual_spec()
    summary = summarize_spec(spec)
    field_summary = summary.field_summaries[0]

    assert field_summary.python_type_qualname == "builtins.str"
    assert field_summary.value_kind_name == "PERSON"
    assert field_summary.cardinality is Cardinality.ONE

    assert len(field_summary.strategy_binding_summaries) == 1
    binding = field_summary.strategy_binding_summaries[0]
    assert binding.cls_qualname.endswith("RegexCandidateStrategy")
    assert binding.kind == "candidate"
    assert binding.params == {"pattern": r"\d{3}-\d{4}"}


def test_summarize_spec_validation_binding_summary() -> None:
    spec = _build_manual_spec()
    summary = summarize_spec(spec)
    field_summary = summary.field_summaries[0]

    vb = field_summary.validation_binding_summary
    assert vb is not None
    assert vb.normalizer_qualname is not None
    assert "_identity_normalizer" in vb.normalizer_qualname
    assert vb.field_validator_qualnames == ()


def test_summarize_spec_unsafe_params_raises() -> None:
    """non-JSON-safe binding params raise `InfrastructureError` with
    the documented prefix `"spec_summary.unsafe_params: "`."""

    field = FieldSpec(
        field_id="phone",
        description="phone",
        value_kind=ValueKind.PERSON,
        cardinality=Cardinality.ONE,
        priority=0,
        depends_on=(),
        python_type=str,
        # bytes are not json-safe per the seam-F layer-1 rule.
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"raw": b"\x00\x01"},
                kind="candidate",
            ),
        ),
    )
    spec = ExtractionSpec(
        fields=(field,),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="default"),
        ),
        budget=BudgetSpec(),
        version="manual-v1",
        source_schema_ref=None,
    )

    with pytest.raises(InfrastructureError) as exc_info:
        summarize_spec(spec)
    assert str(exc_info.value).startswith("spec_summary.unsafe_params: ")


def test_summary_version_is_v1() -> None:
    """`SpecSummary.summary_version` is the fixed `"v1"` literal."""

    spec = _build_manual_spec()
    summary = summarize_spec(spec)
    assert summary.summary_version == "v1"
