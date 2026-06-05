"""behavioral tests for `LayeredProposalValidator.validate_instance(...)`.

proof targets (from docs/tasks/seam-f-layer3-phase-1-instance-validation.md):

- pydantic precedence:
    - raising `model_validator(mode="after")` (`pydantic.ValidationError`
      or `ValueError`) → `ValidationFailure(layer="instance", ...)`
    - `field_id == "<instance>"` (literal sentinel)
    - `producer_version is None`
    - `mode="before"` does not fire
    - `mode="wrap"` does not fire
    - `AttributeError` / `TypeError` raised inside `mode="after"`
      propagate as implementation defects (not caught)
- manual path:
    - `schema_cls=None` → byte-identical pass-through
    - pydantic-backed with no registered `model_validator`s →
      byte-identical pass-through
- success-path identity:
    - on success, `validate_instance` returns the **same object**
      (identity preserved), not a defensive rebuild
- determinism:
    - same `(instance_result, spec, schema_cls)` → same output (and
      same `model_dump` payload on `ValidationFailure`)
- no reassignment / no mutation:
    - `instance_key` unchanged
    - `evidence` unchanged
- materialization independence:
    - layer 3 never routes through public `.to_pydantic(...)`
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, model_validator

from extractx.core.anchors import SourceRef, SourceSpan
from extractx.core.cardinality import Cardinality
from extractx.core.objects import (
    BudgetSpec,
    DistanceMetric,
    ExtractionSpec,
    FieldSpec,
    GroupingEvidence,
    GroupingPolicy,
    InstanceGroupingKey,
    PromptPolicy,
    ValidationBinding,
    ValidationPolicy,
    ValueKind,
)
from extractx.core.outcomes import (
    Evidence,
    FieldRef,
    Instance,
    ObjectIssue,
    ProposalProvenance,
    ValidationFailure,
)
from extractx.proposals.validation import (
    _LAYER3_INSTANCE_SENTINEL,
    LayeredProposalValidator,
)
from extractx.schema import extractx_object_validator

# ---------------------------------------------------------------------------
# fixtures — local so each test's dependencies are legible
# ---------------------------------------------------------------------------


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _sb_span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def _instance_key(*, ordinal: int = 0) -> InstanceGroupingKey:
    return InstanceGroupingKey(
        group_id=f"grp-{ordinal}",
        ordinal=ordinal,
        group_anchors=(_sb_span(0, 4),),
    )


def _provenance() -> ProposalProvenance:
    return ProposalProvenance(strategy_id="regex:test")


def _resolved_proposal(
    *,
    field_id: str,
    normalized_value: Any,
    instance_key: InstanceGroupingKey,
) -> Evidence:
    return Evidence(
        field_id=field_id,
        instance_key=instance_key,
        raw_value=str(normalized_value),
        evidence_text=str(normalized_value),
        source_span=_sb_span(0, 4),
        evidence_spans=(),
        normalized_value=normalized_value,
        proposal_provenance=_provenance(),
    )


def _grouping_evidence() -> GroupingEvidence:
    return GroupingEvidence(
        stage="resolved",
        anchor_spans=(_sb_span(0, 4),),
        clustering_signals={},
        confidence=None,
        producer_version="code:test",
    )


def _instance_result(
    *,
    instance_key: InstanceGroupingKey | None = None,
    field_values: dict[str, Any] | None = None,
    outcome: str = "complete",
    pre_existing_negatives: tuple[Any, ...] = (),
) -> Instance:
    key = instance_key if instance_key is not None else _instance_key()
    field_values = field_values if field_values is not None else {"x": 1, "y": 2}
    proposals = tuple(
        _resolved_proposal(field_id=field_id, normalized_value=value, instance_key=key)
        for field_id, value in field_values.items()
    )
    return Instance(
        instance_key=key,
        outcome=outcome,  # type: ignore[arg-type]
        evidence=proposals,
        negative_outcomes=pre_existing_negatives,  # type: ignore[arg-type]
        grouping_evidence=_grouping_evidence(),
    )


def _build_manual_spec(field_ids: tuple[str, ...] = ("x", "y")) -> ExtractionSpec:
    """build a minimal manual `ExtractionSpec` with the given fields.

    layer 3 does not consume `spec` for dispatch in phase 1 (the
    `schema_cls` parameter does the work), but the protocol contract
    requires one — this helper keeps test bodies readable.
    """

    fields = tuple(
        FieldSpec(
            field_id=fid,
            description=f"field {fid}",
            value_kind=ValueKind.PERSON,
            cardinality=Cardinality.ONE,
            python_type=int,
            strategy_bindings=(),
            validation_binding=ValidationBinding(),
        )
        for fid in field_ids
    )
    return ExtractionSpec(
        fields=fields,
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=GroupingPolicy(
            default_distance_metric=DistanceMetric(name="default"),
        ),
        budget=BudgetSpec(),
        version="test-spec",
        source_schema_ref=None,
    )


# ---------------------------------------------------------------------------
# manual-path / no-op pass-through
# ---------------------------------------------------------------------------


class _NoModelValidatorSchema(BaseModel):
    x: int = 0
    y: int = 0


class TestPassThrough:
    def test_manual_spec_returns_input_reference_unchanged(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result()

        result = validator.validate_instance(instance, spec, schema_cls=None)

        # success-path identity: same reference, not a defensive rebuild.
        assert result is instance

    def test_manual_spec_default_schema_cls_is_pass_through(self) -> None:
        # protocol allows calling without `schema_cls` (default None).
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result()

        result = validator.validate_instance(instance, spec)

        assert result is instance

    def test_pydantic_class_with_no_model_validators_is_pass_through(self) -> None:
        # the brief pins this as byte-identical pass-through. identity
        # preservation is the strongest possible "byte-identical".
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result()

        result = validator.validate_instance(
            instance,
            spec,
            schema_cls=_NoModelValidatorSchema,
        )

        assert result is instance


# ---------------------------------------------------------------------------
# pydantic mode="after" precedence — success paths
# ---------------------------------------------------------------------------


class _PassingAfter(BaseModel):
    x: int = 0
    y: int = 0

    @model_validator(mode="after")
    def _coherent(self) -> _PassingAfter:
        # passes for the {x:1, y:2} fixture used in tests below.
        if self.x > self.y:
            raise ValueError("x must be <= y")
        return self


class _ObjectValidatorRejects(BaseModel):
    x: int = 0
    y: int = 0

    @staticmethod
    @extractx_object_validator(implicates=("x", "y"))
    def _x_before_y(values: dict[str, Any], evidence: dict[str, Evidence]) -> ObjectIssue | None:
        assert evidence["x"].field_id == "x"
        if values["x"] > values["y"]:
            return ObjectIssue(
                code="x_after_y",
                reason="x must be <= y",
            )
        return None


class _ObjectValidatorWarns(BaseModel):
    x: int = 0

    @staticmethod
    @extractx_object_validator(implicates=("x",))
    def _warning(values: dict[str, Any], evidence: dict[str, Evidence]) -> ObjectIssue:
        del values, evidence
        return ObjectIssue(
            severity="warning",
            code="x_suspicious",
            reason="x is suspicious",
        )


class TestPydanticAfterSuccess:
    def test_passing_after_validator_returns_input_reference(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result(field_values={"x": 1, "y": 2})

        result = validator.validate_instance(instance, spec, schema_cls=_PassingAfter)

        # success-path identity preserved.
        assert result is instance


class TestObjectValidators:
    def test_object_validator_issue_translates_to_validation_failure(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result(field_values={"x": 3, "y": 2})

        result = validator.validate_instance(
            instance,
            spec,
            schema_cls=_ObjectValidatorRejects,
        )

        assert isinstance(result, ValidationFailure)
        assert result.layer == "instance"
        assert result.field_id == _LAYER3_INSTANCE_SENTINEL
        assert result.reason == "x must be <= y"
        assert len(result.object_issues) == 1
        issue = result.object_issues[0]
        assert issue.code == "x_after_y"
        assert issue.severity == "error"
        assert issue.implicates == (FieldRef(field_id="x"), FieldRef(field_id="y"))

    def test_object_validator_warning_does_not_block_instance(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec(field_ids=("x",))
        instance = _instance_result(field_values={"x": 1})

        result = validator.validate_instance(
            instance,
            spec,
            schema_cls=_ObjectValidatorWarns,
        )

        assert result is instance


# ---------------------------------------------------------------------------
# pydantic mode="after" precedence — failure paths
# ---------------------------------------------------------------------------


class _RaisingValueErrorAfter(BaseModel):
    x: int = 0
    y: int = 0

    @model_validator(mode="after")
    def _bad(self) -> _RaisingValueErrorAfter:
        raise ValueError("layer-3 reject: x and y inconsistent")


class _RaisingValidationErrorAfter(BaseModel):
    x: int = 0
    y: int = 0

    @model_validator(mode="after")
    def _bad(self) -> _RaisingValidationErrorAfter:
        # raise a real `pydantic.ValidationError` via a nested
        # `model_validate` call that fails. this is the simplest way
        # to surface a `ValidationError` without using the deprecated
        # constructor path.
        raise ValueError("nested-validation-error simulant")


class TestPydanticAfterFailure:
    def test_raising_value_error_translates_to_validation_failure(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        key = _instance_key(ordinal=3)
        instance = _instance_result(instance_key=key)

        result = validator.validate_instance(
            instance,
            spec,
            schema_cls=_RaisingValueErrorAfter,
        )

        assert isinstance(result, ValidationFailure)
        assert result.layer == "instance"
        # literal sentinel — no individual field is implicated.
        assert result.field_id == _LAYER3_INSTANCE_SENTINEL == "<instance>"
        # carry the resolved instance_key through unchanged.
        assert result.instance_key == key
        # producer_version is `None` per the brief — phase-1 layer 3
        # does not compose a versioned producer string.
        assert result.producer_version is None
        assert "layer-3 reject" in result.reason

    def test_raising_validation_error_translates_to_validation_failure(self) -> None:
        # build a schema whose `mode="after"` raises an actual
        # `pydantic.ValidationError`.
        class _Inner(BaseModel):
            n: int

        class _OuterRaisesValidationError(BaseModel):
            x: int = 0

            @model_validator(mode="after")
            def _trigger(self) -> _OuterRaisesValidationError:
                # forcing pydantic to raise a real ValidationError.
                _Inner.model_validate({"n": "not-an-int"})
                return self

        validator = LayeredProposalValidator()
        spec = _build_manual_spec(field_ids=("x",))
        instance = _instance_result(field_values={"x": 7})

        result = validator.validate_instance(
            instance,
            spec,
            schema_cls=_OuterRaisesValidationError,
        )

        assert isinstance(result, ValidationFailure)
        assert result.layer == "instance"
        assert result.field_id == "<instance>"
        assert result.producer_version is None
        # the reason carries the underlying pydantic error string —
        # contents are stable across pydantic patch versions in shape
        # but not in prose; check that we caught and carried something.
        assert isinstance(result.reason, str)
        assert result.reason  # non-empty

    def test_validation_failure_is_deterministic_for_same_inputs(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result()

        first = validator.validate_instance(
            instance,
            spec,
            schema_cls=_RaisingValueErrorAfter,
        )
        second = validator.validate_instance(
            instance,
            spec,
            schema_cls=_RaisingValueErrorAfter,
        )

        assert isinstance(first, ValidationFailure)
        assert isinstance(second, ValidationFailure)
        # byte-identical typed failure shape.
        assert first.model_dump(mode="json") == second.model_dump(mode="json")


# ---------------------------------------------------------------------------
# unexpected exception types propagate (not masked as typed failures)
# ---------------------------------------------------------------------------


class _RaisingAttributeError(BaseModel):
    x: int = 0

    @model_validator(mode="after")
    def _boom(self) -> _RaisingAttributeError:
        raise AttributeError("implementation defect")


class _RaisingTypeError(BaseModel):
    x: int = 0

    @model_validator(mode="after")
    def _boom(self) -> _RaisingTypeError:
        raise TypeError("implementation defect")


class _RaisingRuntimeError(BaseModel):
    x: int = 0

    @model_validator(mode="after")
    def _boom(self) -> _RaisingRuntimeError:
        raise RuntimeError("implementation defect")


class TestUnexpectedExceptionsPropagate:
    def test_attribute_error_propagates(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec(field_ids=("x",))
        instance = _instance_result(field_values={"x": 1})

        with pytest.raises(AttributeError):
            validator.validate_instance(
                instance,
                spec,
                schema_cls=_RaisingAttributeError,
            )

    def test_type_error_propagates(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec(field_ids=("x",))
        instance = _instance_result(field_values={"x": 1})

        with pytest.raises(TypeError):
            validator.validate_instance(
                instance,
                spec,
                schema_cls=_RaisingTypeError,
            )

    def test_other_exception_propagates(self) -> None:
        # the brief: anything that is not `ValidationError` or
        # `ValueError` propagates as an implementation defect.
        validator = LayeredProposalValidator()
        spec = _build_manual_spec(field_ids=("x",))
        instance = _instance_result(field_values={"x": 1})

        with pytest.raises(RuntimeError):
            validator.validate_instance(
                instance,
                spec,
                schema_cls=_RaisingRuntimeError,
            )


# ---------------------------------------------------------------------------
# mode="before" and mode="wrap" do not fire in phase 1 layer 3
# ---------------------------------------------------------------------------


class _BlockingBeforeOnly(BaseModel):
    x: int = 0

    @model_validator(mode="before")
    @classmethod
    def _block(cls, data: Any) -> Any:
        raise AssertionError(
            "model_validator(mode='before') must not fire in phase-1 layer 3",
        )


class _BlockingWrapOnly(BaseModel):
    x: int = 0

    @model_validator(mode="wrap")
    @classmethod
    def _block(cls, data: Any, handler: Any) -> Any:  # noqa: ARG003
        raise AssertionError(
            "model_validator(mode='wrap') must not fire in phase-1 layer 3",
        )


class TestModeBeforeAndWrapDoNotFire:
    def test_mode_before_does_not_fire(self) -> None:
        # the brief pins: phase-1 layer 3 invokes only `mode="after"`.
        # if a `mode="before"` fires here, the AssertionError surfaces.
        validator = LayeredProposalValidator()
        spec = _build_manual_spec(field_ids=("x",))
        instance = _instance_result(field_values={"x": 1})

        result = validator.validate_instance(
            instance,
            spec,
            schema_cls=_BlockingBeforeOnly,
        )

        # `mode="before"` skipped → pass-through.
        assert result is instance

    def test_mode_wrap_does_not_fire(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec(field_ids=("x",))
        instance = _instance_result(field_values={"x": 1})

        result = validator.validate_instance(
            instance,
            spec,
            schema_cls=_BlockingWrapOnly,
        )

        assert result is instance


# ---------------------------------------------------------------------------
# determinism — same inputs → same output
# ---------------------------------------------------------------------------


class TestLayer3Determinism:
    def test_repeated_calls_yield_same_reference_on_pass_through(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result()

        first = validator.validate_instance(instance, spec)
        second = validator.validate_instance(instance, spec)

        # two calls, two identical references — pass-through is byte-
        # identical by virtue of returning the same object.
        assert first is instance
        assert second is instance

    def test_two_validator_instances_yield_same_failure(self) -> None:
        v_a = LayeredProposalValidator()
        v_b = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result()

        result_a = v_a.validate_instance(
            instance,
            spec,
            schema_cls=_RaisingValueErrorAfter,
        )
        result_b = v_b.validate_instance(
            instance,
            spec,
            schema_cls=_RaisingValueErrorAfter,
        )

        assert isinstance(result_a, ValidationFailure)
        assert isinstance(result_b, ValidationFailure)
        assert result_a.model_dump(mode="json") == result_b.model_dump(mode="json")


# ---------------------------------------------------------------------------
# no reassignment / no mutation — `instance_key` unchanged on failure-path
# ---------------------------------------------------------------------------


class TestNoReassignmentAtLayer3:
    def test_failure_path_carries_input_instance_key_unchanged(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        key = _instance_key(ordinal=42)
        instance = _instance_result(instance_key=key)

        result = validator.validate_instance(
            instance,
            spec,
            schema_cls=_RaisingValueErrorAfter,
        )

        assert isinstance(result, ValidationFailure)
        # exact object equality — no rewriting, no re-bucketing.
        assert result.instance_key == key

    def test_validator_does_not_mutate_or_drop_evidence(self) -> None:
        # the validator never touches `evidence`. on failure it
        # returns a `ValidationFailure` (a separate typed object); the
        # original `Instance.evidence` are untouched.
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result(field_values={"x": 5, "y": 3})
        original_proposals = instance.evidence

        _ = validator.validate_instance(
            instance,
            spec,
            schema_cls=_RaisingValueErrorAfter,
        )

        # frozen pydantic + tuple identity → unchanged after the call.
        assert instance.evidence is original_proposals


# ---------------------------------------------------------------------------
# materialization independence — layer 3 doesn't use public `.to_pydantic()`
# ---------------------------------------------------------------------------


class TestMaterializationIndependence:
    def test_layer3_does_not_route_through_to_pydantic(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # if layer 3 ever called `instance_result.to_pydantic(...)`, this
        # test fails even though public materialization is now implemented.
        def _fail_to_pydantic(self: Instance, cls: type[Any]) -> Any:
            del self, cls
            raise AssertionError("layer 3 called public to_pydantic")

        monkeypatch.setattr(Instance, "to_pydantic", _fail_to_pydantic)
        validator = LayeredProposalValidator()
        spec = _build_manual_spec()
        instance = _instance_result(field_values={"x": 1, "y": 2})

        result = validator.validate_instance(instance, spec, schema_cls=_PassingAfter)

        assert result is instance


# ---------------------------------------------------------------------------
# layer-3 invocation order: each `mode="after"` decorator fires in order
# ---------------------------------------------------------------------------


# module-level recorder used by the ordering test below. a class
# attribute on a pydantic model is hijacked by `ModelPrivateAttr`
# when underscore-prefixed; a module-level list is the simplest way
# to record ordering without touching pydantic's private-attr
# machinery.
_ORDER_RECORDER: list[str] = []


class _OrderRecorderModel(BaseModel):
    x: int = 0

    @model_validator(mode="after")
    def alpha_validator(self) -> _OrderRecorderModel:
        _ORDER_RECORDER.append("alpha")
        return self

    @model_validator(mode="after")
    def beta_validator(self) -> _OrderRecorderModel:
        _ORDER_RECORDER.append("beta")
        return self


class TestModeAfterDeclarationOrder:
    def test_after_validators_fire_in_declaration_order(self) -> None:
        validator = LayeredProposalValidator()
        spec = _build_manual_spec(field_ids=("x",))
        instance = _instance_result(field_values={"x": 1})

        _ORDER_RECORDER.clear()
        result = validator.validate_instance(
            instance,
            spec,
            schema_cls=_OrderRecorderModel,
        )

        assert result is instance
        assert _ORDER_RECORDER == ["alpha", "beta"]
