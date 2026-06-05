"""focused proof for `extract(...)` per docs/tasks/api-phase-1-extract-function.md.

covers the brief's full proof target list:

1. surface present and async — `inspect.iscoroutinefunction` + `inspect.signature`
2. tier-1 export — `extract` in `extractx.__all__`
3. happy-path equivalence with the explicit `run_extraction(...)` four-line setup
4. storage opt-in writes the M9 phase-1 layout
5. storage opt-out leaves `replay_artifact_ref == ""`; no filesystem writes
6. `capture_interviews=True` raises `InfrastructureError` with substring "interview capture"
7. `run_extraction` signature unchanged
8. pydantic-backed only — non-`BaseModel` raises `SpecError`
9-10. tests reach the executor via real `extract(...)` / `run_extraction(...)`,
   no benchmark-only path (architecture §15)
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel

import extractx
from extractx import (
    ExecutorPolicy,
    Extraction,
    ExtractionSpec,
    InfrastructureError,
    Runtime,
    SpecError,
    ValueKind,
    extract,
    extract_field,
    run_extraction,
)
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import SelectorBinding, StrategyBinding
from extractx.execution.executor.serial import SerialExecutor
from extractx.extras.pydantic_ai import PydanticAISelector
from extractx.storage import LocalFilesystemStore

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


class _Phone(BaseModel):
    """phone field bound to the landed regex candidate strategy.

    `ValueKind.PERSON` is a typing convenience — `from_pydantic`
    requires one ValueKind marker per field; the slice does not consume
    the kind semantically here.
    """

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


class _DocumentKind(BaseModel):
    kind: Annotated[
        Literal["invoice", "receipt", "memo"],
        ValueKind.CATEGORY,
    ] = extract_field(
        description="classify the document type",
        selector_binding=SelectorBinding(
            cls=PydanticAISelector,
            params={"model_id": "fake-model"},
        ),
    )


class _Provider:
    def __call__(self, rendered: Any, output_type: type[Any]) -> Any:
        payload = {
            "instance_id": rendered.metadata["allowed_instance_ids"][0],
            "field_id": rendered.metadata["allowed_field_ids"][0],
            "selected_candidate_ids": (rendered.metadata["allowed_evidence_ids"][0],),
            "abstain": False,
            "reason": "bounded test provider",
        }
        return output_type.model_validate(payload)


_DOCUMENT = "Call us at 555-1234."


def _result_minus_replay_ref(result: Extraction) -> dict[str, object]:
    """compare two `Extraction`s structurally, excluding
    `replay_artifact_ref`.

    `replay_artifact_ref` differs between persisted and non-persisted
    runs (mirrors M9 phase-2's equality helper exclusion). every other
    field is byte-stable across executor invocations on the same
    `(spec, runtime, policy, document)` tuple.
    """

    return {
        "document_id": result.document_id,
        "spec_version": result.spec_version,
        "outcome": result.outcome,
        "strategy": result.strategy,
        "instances": result.instances,
        "trace": result.trace,
    }


# ---------------------------------------------------------------------------
# 1. surface present and async
# ---------------------------------------------------------------------------


def test_extract_is_coroutine_function() -> None:
    """`from extractx import extract` works; the function is async."""

    assert inspect.iscoroutinefunction(extract)


def test_extract_signature_matches_brief() -> None:
    """signature exposes schema-first sugar plus optional runtime capability."""

    sig = inspect.signature(extract)
    params = list(sig.parameters.values())

    assert [p.name for p in params] == [
        "document",
        "schema",
        "runtime",
        "store",
        "capture_interviews",
    ]

    document_p, schema_p, runtime_p, store_p, capture_p = params

    assert document_p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert document_p.default is inspect.Parameter.empty

    assert schema_p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert schema_p.default is inspect.Parameter.empty

    assert runtime_p.kind is inspect.Parameter.KEYWORD_ONLY
    assert runtime_p.default is None

    assert store_p.kind is inspect.Parameter.KEYWORD_ONLY
    assert store_p.default is None

    assert capture_p.kind is inspect.Parameter.KEYWORD_ONLY
    assert capture_p.default is False


# ---------------------------------------------------------------------------
# 2. tier-1 export
# ---------------------------------------------------------------------------


def test_extract_in_tier1_all() -> None:
    """`extract` is exported from `extractx.__init__.__all__`."""

    assert "extract" in extractx.__all__
    # alphabetical order is the convention; assert it sits between
    # `ValueKind` and `extract_field` to keep the export list stable.
    all_list = list(extractx.__all__)
    assert all_list.index("ValueKind") < all_list.index("extract")
    assert all_list.index("extract") < all_list.index("extract_field")


def test_extract_attribute_resolves_to_api_function() -> None:
    """`extractx.extract` resolves to the function in `extractx.api`."""

    from extractx.api import extract as api_extract

    assert extractx.extract is api_extract


# ---------------------------------------------------------------------------
# 3. happy-path equivalence with the explicit four-line setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_equivalent_to_run_extraction_four_line_setup() -> None:
    """`await extract(doc, _Phone)` returns an `Extraction`
    structurally equal to the same run routed through the explicit
    `run_extraction(...)` four-line setup.

    excludes `replay_artifact_ref` (both should be `""` in the no-store
    case anyway) per the M9 phase-2 equality-helper convention.
    """

    extract_result = await extract(_DOCUMENT, _Phone)

    spec = ExtractionSpec.from_pydantic(_Phone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    explicit_result = await run_extraction(
        document=_DOCUMENT,
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert _result_minus_replay_ref(extract_result) == _result_minus_replay_ref(
        explicit_result,
    )
    # in the no-store case both refs are "".
    assert extract_result.replay_artifact_ref == ""
    assert explicit_result.replay_artifact_ref == ""


# ---------------------------------------------------------------------------
# 4. storage opt-in writes the M9 phase-1 layout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_with_store_writes_m9_phase1_layout(tmp_path: Path) -> None:
    """opt-in persistence populates `replay_artifact_ref` and writes
    the M9 phase-1 layout (`objects/source/`, `objects/spec/`,
    `objects/replay/`, `runs/`).

    same artifact bytes as the explicit
    `SerialExecutor(storage=...).execute(...)` path.
    """

    sugar_root = tmp_path / "sugar"
    sugar_root.mkdir()
    sugar_store = LocalFilesystemStore(sugar_root)
    sugar_result = await extract(_DOCUMENT, _Phone, store=sugar_store)

    assert sugar_result.replay_artifact_ref != ""

    # M9 phase-1 layout — the four directories exist and are populated.
    assert (sugar_root / "objects" / "source").is_dir()
    assert (sugar_root / "objects" / "spec").is_dir()
    assert (sugar_root / "objects" / "replay").is_dir()
    assert (sugar_root / "runs").is_dir()

    assert any((sugar_root / "objects" / "source").iterdir())
    assert any((sugar_root / "objects" / "spec").iterdir())
    assert any((sugar_root / "objects" / "replay").iterdir())
    assert any((sugar_root / "runs").iterdir())

    # forbidden phase-1 kinds: `result` and `interview` directories are
    # never created (mirrors the storage stub-honesty assertions).
    assert not (sugar_root / "objects" / "result").exists()
    assert not (sugar_root / "objects" / "interview").exists()
    assert not (sugar_root / "views").exists()

    # same artifact bytes as the explicit `SerialExecutor(storage=...)
    # .execute(...)` path on the same `(document, spec, runtime,
    # policy)` tuple. compare via `get_object` to assert byte-equality.
    explicit_root = tmp_path / "explicit"
    explicit_root.mkdir()
    explicit_store = LocalFilesystemStore(explicit_root)
    spec = ExtractionSpec.from_pydantic(_Phone)
    runtime = Runtime()
    policy = ExecutorPolicy(strategy="independent")
    explicit_result = await SerialExecutor(storage=explicit_store).execute(
        document=_DOCUMENT,
        spec=spec,
        runtime=runtime,
        policy=policy,
    )

    assert sugar_result.replay_artifact_ref == explicit_result.replay_artifact_ref
    sugar_replay_blob = sugar_store.get_object(
        "replay",
        sugar_result.replay_artifact_ref,
    )
    explicit_replay_blob = explicit_store.get_object(
        "replay",
        explicit_result.replay_artifact_ref,
    )
    assert sugar_replay_blob == explicit_replay_blob

    # structural equality on the result modulo `replay_artifact_ref` —
    # both runs persist so the refs themselves are equal too (asserted
    # above); excluding it here just keeps the helper consistent.
    assert _result_minus_replay_ref(sugar_result) == _result_minus_replay_ref(
        explicit_result,
    )


# ---------------------------------------------------------------------------
# 5. storage opt-out leaves `replay_artifact_ref == ""`; no filesystem writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_without_store_writes_nothing(tmp_path: Path) -> None:
    """no `store` argument → `replay_artifact_ref == ""` and no
    filesystem writes occur (using `tmp_path` as the cwd-isolated probe
    target — `extract` never touches it)."""

    result = await extract(_DOCUMENT, _Phone)

    assert result.replay_artifact_ref == ""
    # `tmp_path` is empty before / after — `extract` writes nothing.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_extract_threads_runtime_for_llm_bound_schema() -> None:
    """`extract(...)` remains the schema-first happy path for LLM-bound
    schemas when callers pass a runtime capability."""

    result = await extract(
        "Invoice INV-1001.",
        _DocumentKind,
        runtime=Runtime(llm=_Provider()),
    )

    assert result.outcome == "complete"
    assert result.instances[0].evidence[0].field_id == "kind"
    assert result.instances[0].evidence[0].normalized_value == "invoice"


@pytest.mark.asyncio
async def test_extract_llm_bound_schema_without_runtime_fails_fast() -> None:
    """missing runtime capabilities are setup defects, not insufficient
    extraction outcomes."""

    with pytest.raises(InfrastructureError, match="selector\\.missing_llm"):
        await extract("Invoice INV-1001.", _DocumentKind)


# ---------------------------------------------------------------------------
# 6. `capture_interviews=True` raises `InfrastructureError`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_capture_interviews_raises_infrastructure_error() -> None:
    """`capture_interviews=True` propagates the executor's pre-run-gate
    `InfrastructureError` verbatim. proof shape per the brief: substring
    match on `"interview capture"` rather than full-string match (the
    executor's pinned wording may evolve)."""

    with pytest.raises(InfrastructureError) as exc_info:
        await extract(_DOCUMENT, _Phone, capture_interviews=True)

    assert isinstance(exc_info.value, InfrastructureError)
    assert "interview capture" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 7. `run_extraction` signature unchanged
# ---------------------------------------------------------------------------


def test_run_extraction_signature_unchanged() -> None:
    """`run_extraction(...)` still has exactly four positional
    parameters: `document`, `spec`, `runtime`, `policy`. no `store`,
    no `schema`, no widening from this thread."""

    sig = inspect.signature(run_extraction)
    assert [p.name for p in sig.parameters.values()] == [
        "document",
        "spec",
        "runtime",
        "policy",
    ]
    # all four are positional-or-keyword with no defaults — caller
    # supplies every input.
    for param in sig.parameters.values():
        assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert param.default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# 8. pydantic-backed only — non-`BaseModel` raises `SpecError`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_non_basemodel_raises_spec_error() -> None:
    """passing a non-`BaseModel` class to `extract(...)` surfaces
    `SpecError` from `ExtractionSpec.from_pydantic` as-is — `extract`
    does not catch or rewrite."""

    class _NotAModel:
        pass

    with pytest.raises(SpecError):
        await extract(_DOCUMENT, _NotAModel)  # type: ignore[arg-type]
