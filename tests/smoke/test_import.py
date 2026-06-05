"""smoke test — minimal end-to-end proof of the M8 phase-1 supported slice.

per the brief's "Focused proof — smoke": replace the old "exposed but
unimplemented" expectation with a real supported-slice smoke test that
proves `run_extraction(...)` is callable and actually returns an
`Extraction` on the supported slice.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel

import extractx
from extractx import (
    ExecutorPolicy,
    Extraction,
    ExtractionSpec,
    Runtime,
    ValueKind,
    extract_field,
    extract_one,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import StrategyBinding


def test_package_imports() -> None:
    """the package loads without errors and exposes a version."""

    assert hasattr(extractx, "__version__")
    assert extractx.__version__ == "0.1.0"


def test_run_extraction_is_callable() -> None:
    """`run_extraction` is exposed at the public surface and callable."""

    assert callable(run_extraction)


def test_extract_one_is_callable() -> None:
    """`extract_one` is exposed at the public surface and callable."""

    assert callable(extract_one)


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


@pytest.mark.asyncio
async def test_run_extraction_returns_extraction_result_on_supported_slice() -> None:
    spec = ExtractionSpec.from_pydantic(_Phone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")

    result = await run_extraction(
        document="Call us at 555-1234.",
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert isinstance(result, Extraction)
    assert result.outcome == "complete"
    assert result.strategy == "independent"
    assert len(result.instances) == 1
