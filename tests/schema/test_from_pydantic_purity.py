"""contract tests for `ExtractionSpec.from_pydantic` purity.

proof targets:
- `ExtractionSpec.from_pydantic(Cls)` is pure: same class → same
  `ExtractionSpec` → same `spec.version`.
- dependency cycles in `depends_on` raise `SpecError`.
- unknown `depends_on` references raise `SpecError`.
- `candidate_overflow_policy="truncate_sorted"` without `sorter_binding`
  on every field raises `SpecError`.
- passing a non-BaseModel class to `from_pydantic` raises `SpecError`.
- plain `pydantic.Field(...)` (no `extract_field`) raises `SpecError`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from extractx import ExtractionSpec, SpecError, ValueKind, extract_field
from extractx.core.cardinality import Cardinality
from extractx.core.objects import (
    InstanceProposerBinding,
    PromptPolicy,
    SorterBinding,
)

Money = Annotated[Decimal, ValueKind.MONEY]
Org = Annotated[str, ValueKind.ORG]


class _FakeSorter:
    """stand-in for a real `CandidateSorter` class."""


class _FakeInstanceProposer:
    """stand-in for a real `InstanceProposer` class."""


class TestFromPydanticPurity:
    def test_same_class_same_spec_version(self) -> None:
        class Invoice(BaseModel):
            total: Money = extract_field(description="total due")
            vendor: Org = extract_field(description="billing org")

        spec_a = ExtractionSpec.from_pydantic(Invoice)
        spec_b = ExtractionSpec.from_pydantic(Invoice)
        assert spec_a.version == spec_b.version

    def test_same_class_same_field_shapes(self) -> None:
        class Invoice(BaseModel):
            total: Money = extract_field(description="total due")
            vendor: Org = extract_field(description="billing org")

        spec_a = ExtractionSpec.from_pydantic(Invoice)
        spec_b = ExtractionSpec.from_pydantic(Invoice)
        assert spec_a.fields == spec_b.fields

    def test_field_id_is_pydantic_attribute_name_not_alias(self) -> None:
        class Invoice(BaseModel):
            total_due: Money = extract_field(
                description="total due",
                alias="totalDue",
            )

        spec = ExtractionSpec.from_pydantic(Invoice)

        assert spec.fields[0].field_id == "total_due"

    def test_instance_type_defaults_to_schema_class_name(self) -> None:
        class Invoice(BaseModel):
            total: Money = extract_field(description="total due")

        spec = ExtractionSpec.from_pydantic(Invoice)

        assert spec.instance_type == "Invoice"
        assert spec.instance_cardinality is Cardinality.ONE
        assert spec.instance_proposer_binding is None

    def test_instance_type_override_changes_version(self) -> None:
        class Invoice(BaseModel):
            total: Money = extract_field(description="total due")

        assert (
            ExtractionSpec.from_pydantic(Invoice).version
            != ExtractionSpec.from_pydantic(Invoice, instance_type="ReceiptRecord").version
        )

    def test_many_without_instance_proposer_binding_raises_spec_error(self) -> None:
        class Invoice(BaseModel):
            total: Money = extract_field(description="total due")

        with pytest.raises(SpecError, match="instance_proposer_binding"):
            ExtractionSpec.from_pydantic(
                Invoice,
                instance_cardinality=Cardinality.MANY,
            )

    def test_one_with_instance_proposer_binding_raises_spec_error(self) -> None:
        class Invoice(BaseModel):
            total: Money = extract_field(description="total due")

        with pytest.raises(SpecError, match="not used"):
            ExtractionSpec.from_pydantic(
                Invoice,
                instance_proposer_binding=InstanceProposerBinding(cls=_FakeInstanceProposer),
            )

    def test_changing_description_changes_version(self) -> None:
        class A(BaseModel):
            total: Money = extract_field(description="total due")

        class B(BaseModel):
            total: Money = extract_field(description="grand total")

        assert ExtractionSpec.from_pydantic(A).version != ExtractionSpec.from_pydantic(B).version

    def test_changing_priority_changes_version(self) -> None:
        class A(BaseModel):
            total: Money = extract_field(description="total due")

        class B(BaseModel):
            total: Money = extract_field(description="total due", priority=5)

        assert ExtractionSpec.from_pydantic(A).version != ExtractionSpec.from_pydantic(B).version


class TestDependencyValidation:
    def test_cyclic_depends_on_raises_spec_error(self) -> None:
        class Cyclic(BaseModel):
            a: Money = extract_field(description="a", depends_on=("b",))
            b: Money = extract_field(description="b", depends_on=("a",))

        with pytest.raises(SpecError, match="cycle"):
            ExtractionSpec.from_pydantic(Cyclic)

    def test_self_cycle_raises(self) -> None:
        class SelfDep(BaseModel):
            a: Money = extract_field(description="a", depends_on=("a",))

        with pytest.raises(SpecError, match="cycle"):
            ExtractionSpec.from_pydantic(SelfDep)

    def test_unknown_depends_on_raises(self) -> None:
        class Unknown(BaseModel):
            a: Money = extract_field(description="a", depends_on=("missing",))

        with pytest.raises(SpecError, match="unknown"):
            ExtractionSpec.from_pydantic(Unknown)

    def test_valid_acyclic_dependencies_pass(self) -> None:
        class Acyclic(BaseModel):
            a: Money = extract_field(description="a")
            b: Money = extract_field(description="b", depends_on=("a",))
            c: Money = extract_field(description="c", depends_on=("a", "b"))

        spec = ExtractionSpec.from_pydantic(Acyclic)
        assert len(spec.fields) == 3


class TestAdr0005SorterRule:
    def test_truncate_sorted_without_sorter_raises(self) -> None:
        class M(BaseModel):
            a: Money = extract_field(description="a")
            b: Money = extract_field(description="b")

        policy = PromptPolicy(
            candidate_overflow_policy="truncate_sorted",
            candidate_count_bound=32,
        )
        with pytest.raises(SpecError, match="sorter_binding"):
            ExtractionSpec.from_pydantic(M, prompt_policy=policy)

    def test_truncate_sorted_with_sorter_on_every_field_passes(self) -> None:
        sorter = SorterBinding(cls=_FakeSorter)

        class M(BaseModel):
            a: Money = extract_field(description="a", sorter_binding=sorter)
            b: Money = extract_field(description="b", sorter_binding=sorter)

        policy = PromptPolicy(
            candidate_overflow_policy="truncate_sorted",
            candidate_count_bound=32,
        )
        spec = ExtractionSpec.from_pydantic(M, prompt_policy=policy)
        assert spec.prompt_policy.candidate_overflow_policy == "truncate_sorted"

    def test_truncate_sorted_partial_sorter_coverage_raises(self) -> None:
        sorter = SorterBinding(cls=_FakeSorter)

        class M(BaseModel):
            a: Money = extract_field(description="a", sorter_binding=sorter)
            b: Money = extract_field(description="b")  # missing

        policy = PromptPolicy(
            candidate_overflow_policy="truncate_sorted",
            candidate_count_bound=32,
        )
        with pytest.raises(SpecError, match="sorter_binding"):
            ExtractionSpec.from_pydantic(M, prompt_policy=policy)

    def test_default_fail_policy_does_not_require_sorter(self) -> None:
        class M(BaseModel):
            a: Money = extract_field(description="a")

        spec = ExtractionSpec.from_pydantic(M)  # default PromptPolicy()
        assert spec.prompt_policy.candidate_overflow_policy == "fail"


class TestInputValidation:
    def test_non_basemodel_raises(self) -> None:
        class NotAModel:
            pass

        with pytest.raises(SpecError, match="BaseModel"):
            ExtractionSpec.from_pydantic(NotAModel)  # type: ignore[arg-type]

    def test_plain_pydantic_field_raises(self) -> None:
        class M(BaseModel):
            total: Money = Field(description="total")  # no extract_field metadata

        with pytest.raises(SpecError, match="extract_field"):
            ExtractionSpec.from_pydantic(M)
