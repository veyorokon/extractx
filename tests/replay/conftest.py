"""shared fixtures for the replay proof tests.

builds the supported phase-1 spec / runtime / policy combinations once
and exposes them to the proof tests as reusable fixtures. fixtures
must be honest about the M9 phase-1 hard pin #13: "no benchmark-only
execution path" — every persisted artifact reaches the executor via
the real `run_extraction(...)` (or a `SerialExecutor.execute(...)`
call mirroring it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel

from extractx import (
    ExecutorPolicy,
    ExtractionSpec,
    Runtime,
    ValueKind,
    extract_field,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.cardinality import Cardinality
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
from extractx.execution.executor.serial import SerialExecutor
from extractx.storage import LocalFilesystemStore

# --------------------------------------------------------------------------
# spec fixtures — spans the M8-supported pydantic + manual paths
# --------------------------------------------------------------------------


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


@pytest.fixture
def pydantic_spec() -> ExtractionSpec:
    return ExtractionSpec.from_pydantic(_Phone)


@pytest.fixture
def manual_spec() -> ExtractionSpec:
    return _build_manual_spec()


@pytest.fixture
def runtime() -> Runtime:
    return Runtime()


@pytest.fixture
def policy() -> ExecutorPolicy:
    return ExecutorPolicy(strategy="independent")


# --------------------------------------------------------------------------
# document fixtures — complete / partial / failed outcomes
# --------------------------------------------------------------------------


@pytest.fixture
def doc_complete() -> str:
    """document that produces a `complete` outcome."""

    return "Call us at 555-1234."


@pytest.fixture
def doc_failed() -> str:
    """document that produces a `failed` outcome (no regex match)."""

    return "no digits here at all"


# --------------------------------------------------------------------------
# storage + executor fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> LocalFilesystemStore:
    return LocalFilesystemStore(tmp_path)


@pytest.fixture
def executor_with_storage(store: LocalFilesystemStore) -> SerialExecutor:
    return SerialExecutor(storage=store)


@pytest.fixture
def executor_no_storage() -> SerialExecutor:
    return SerialExecutor()
