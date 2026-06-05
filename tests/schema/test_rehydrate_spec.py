"""focused proof for `rehydrate_spec` per M9 phase-2 §6.

covers:

- happy path: registry-resolved class produces a spec structurally
  equal to the original
- version-mismatch surface (`spec_rehydrate.version_mismatch: ...`)
- field-drift surface (`spec_rehydrate.field_drift: ...`)
- missing-class surface (`spec_rehydrate.missing_class: ...`)
- manual-spec rejection (`spec_rehydrate.manual_unsupported: ...`)
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import BaseModel

from extractx import ExtractionSpec, ValueKind, extract_field
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.cardinality import Cardinality
from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import (
    BudgetSpec,
    DistanceMetric,
    FieldSpec,
    GroupingPolicy,
    PromptPolicy,
    StrategyBinding,
    ValidationBinding,
    ValidationPolicy,
)
from extractx.core.versions import stable_hash
from extractx.schema._schema_cls_registry import lookup_schema_cls
from extractx.schema.rehydrate import rehydrate_spec
from extractx.schema.summary import summarize_spec


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


class _TwoFields(BaseModel):
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
    zip_code: Annotated[str, ValueKind.PERSON] = extract_field(
        description="zip",
        strategy_bindings=(
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params={"pattern": r"\d{5}"},
                kind="candidate",
            ),
        ),
    )


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


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_rehydrate_spec_round_trips_to_original_pydantic_spec() -> None:
    """`rehydrate_spec(summary, schema_cls)` produces a spec structurally
    equal to the original `from_pydantic` result."""

    original = ExtractionSpec.from_pydantic(_Phone)
    summary = summarize_spec(original)

    schema_cls = lookup_schema_cls(summary.spec_version)
    assert schema_cls is _Phone

    rehydrated = rehydrate_spec(summary, schema_cls=_Phone)

    assert rehydrated.version == summary.spec_version == original.version
    assert tuple(f.field_id for f in rehydrated.fields) == tuple(
        f.field_id for f in original.fields
    )
    # full structural equality holds — `from_pydantic` is deterministic.
    assert rehydrated == original


def test_rehydrate_spec_round_trips_multi_field() -> None:
    """multi-field pydantic-backed specs round-trip with field-id order
    preserved."""

    original = ExtractionSpec.from_pydantic(_TwoFields)
    summary = summarize_spec(original)

    rehydrated = rehydrate_spec(summary, schema_cls=_TwoFields)

    assert tuple(f.field_id for f in rehydrated.fields) == ("phone", "zip_code")
    assert rehydrated == original


# ---------------------------------------------------------------------------
# typed failure surfaces
# ---------------------------------------------------------------------------


def test_rehydrate_spec_manual_spec_raises_typed_error() -> None:
    """manual specs (`source_schema_ref is None`) raise the pinned
    `spec_rehydrate.manual_unsupported: ...` prefix."""

    manual_spec = _build_manual_spec()
    summary = summarize_spec(manual_spec)
    assert summary.source_schema_ref is None

    with pytest.raises(InfrastructureError) as exc_info:
        rehydrate_spec(summary, schema_cls=_Phone)
    assert str(exc_info.value).startswith("spec_rehydrate.manual_unsupported: ")


def test_rehydrate_spec_missing_class_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clearing the registered class for the spec_version surfaces
    `spec_rehydrate.missing_class: ...`."""

    original = ExtractionSpec.from_pydantic(_Phone)
    summary = summarize_spec(original)

    # remove the registered class for this spec_version. monkeypatch
    # restores it on teardown.
    from extractx.schema import _schema_cls_registry as reg

    monkeypatch.setitem(reg._SCHEMA_CLS_BY_SPEC_VERSION, summary.spec_version, None)
    monkeypatch.delitem(reg._SCHEMA_CLS_BY_SPEC_VERSION, summary.spec_version)

    with pytest.raises(InfrastructureError) as exc_info:
        rehydrate_spec(summary, schema_cls=_Phone)
    assert str(exc_info.value).startswith("spec_rehydrate.missing_class: ")


def test_rehydrate_spec_version_mismatch_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """monkey-patch `from_pydantic` to return a spec with a perturbed
    `version`; assert the `spec_rehydrate.version_mismatch: ...`
    pinned prefix fires."""

    original = ExtractionSpec.from_pydantic(_Phone)
    summary = summarize_spec(original)

    real_classmethod = ExtractionSpec.from_pydantic

    def _patched(cls_arg: Any) -> ExtractionSpec:
        real_spec = real_classmethod(cls_arg)
        return real_spec.model_copy(update={"version": "deliberately-perturbed"})

    monkeypatch.setattr(ExtractionSpec, "from_pydantic", _patched)

    with pytest.raises(InfrastructureError) as exc_info:
        rehydrate_spec(summary, schema_cls=_Phone)
    assert str(exc_info.value).startswith("spec_rehydrate.version_mismatch: ")


def test_rehydrate_spec_field_drift_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """monkey-patch `from_pydantic` to drop a field; assert
    `spec_rehydrate.field_drift: ...` pinned prefix fires.

    we keep the rehydrated `version` aligned with `summary.spec_version`
    so the field-drift surface fires *after* the version check (the
    field-drift error is the load-bearing assertion)."""

    original = ExtractionSpec.from_pydantic(_TwoFields)
    summary = summarize_spec(original)

    real_classmethod = ExtractionSpec.from_pydantic

    def _patched(cls_arg: Any) -> ExtractionSpec:
        real_spec = real_classmethod(cls_arg)
        # drop the second field, keep the persisted version aligned
        # so the version-mismatch check passes and field-drift fires
        return real_spec.model_copy(
            update={
                "fields": (real_spec.fields[0],),
                "version": summary.spec_version,
            },
        )

    monkeypatch.setattr(ExtractionSpec, "from_pydantic", _patched)

    with pytest.raises(InfrastructureError) as exc_info:
        rehydrate_spec(summary, schema_cls=_TwoFields)
    assert str(exc_info.value).startswith("spec_rehydrate.field_drift: ")


def test_rehydrate_spec_wrong_class_raises_missing_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """passing a `schema_cls` that does not match the registered class
    for `spec_version` surfaces `spec_rehydrate.missing_class: ...`."""

    original = ExtractionSpec.from_pydantic(_Phone)
    summary = summarize_spec(original)

    # smuggle a different class through. the registry has `_Phone`
    # under `spec_version`; we pass `_TwoFields`.
    with pytest.raises(InfrastructureError) as exc_info:
        rehydrate_spec(summary, schema_cls=_TwoFields)
    assert str(exc_info.value).startswith("spec_rehydrate.missing_class: ")
