"""Rule-based CATEGORY selector contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel

from extractx import (
    ExecutorPolicy,
    ExtractionSpec,
    Runtime,
    ValueKind,
    extract_field,
    run_extraction,
)
from extractx.candidates.generators.literal_set import LiteralSetCandidateStrategy
from extractx.core import (
    Candidate,
    CandidateSet,
    Cardinality,
    ContextPack,
    FieldSpec,
    SelectorBinding,
    SourceRef,
    SourceSpan,
    StrategyBinding,
    StructuralStatus,
)
from extractx.core.exceptions import InfrastructureError
from extractx.execution.executor.serial import SerialExecutor
from extractx.replay import read_replay
from extractx.selection import CategoryRule, RuleBasedCategorySelector
from extractx.storage import LocalFilesystemStore


def _source_ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:test")


def _span() -> SourceSpan:
    return SourceSpan(
        source_ref=_source_ref(),
        text_anchor_space="normalized_text",
        byte_start=0,
        byte_end=0,
    )


def _category_candidate(candidate_id: str, literal: str) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        text=literal,
        source_kind="structured",
        source_id="literal_set",
        source_span=_span(),
        normalized_hint=literal,
        structured_payload={"literal": literal},
        structural_status=StructuralStatus(
            passed=True,
            contract_id="literal_set_strategy_v1",
        ),
    )


def _candidate_set(literals: tuple[str, ...]) -> CandidateSet:
    return CandidateSet(
        field_id="verdict",
        document_id="doc-1",
        strategy_id="literal_set:test",
        candidates=tuple(
            _category_candidate(f"literal-{literal}", literal) for literal in literals
        ),
    )


def _field_spec(
    *,
    cardinality: Cardinality = Cardinality.ONE,
    literal_values: tuple[str, ...] = ("receipt", "review", "irrelevant"),
) -> FieldSpec:
    return FieldSpec(
        field_id="verdict",
        description="document verdict",
        value_kind=ValueKind.CATEGORY,
        cardinality=cardinality,
        python_type=str,
        literal_values=literal_values,
        strategy_bindings=(
            StrategyBinding(
                cls=LiteralSetCandidateStrategy,
                kind="candidate",
            ),
        ),
    )


def _context(text: str) -> ContextPack:
    return ContextPack(
        schema_description="",
        document_summary=text,
    )


def _selector(*, rules: tuple[CategoryRule, ...]) -> RuleBasedCategorySelector:
    return RuleBasedCategorySelector(rules=rules)


def test_rule_based_category_selector_selects_single_positive_literal() -> None:
    selector = _selector(
        rules=(
            CategoryRule(
                rule_id="receipt-detected",
                candidate_literal="receipt",
                pattern=r"submitted .* receipts",
            ),
        ),
    )

    observation = selector.select(
        _field_spec(),
        _candidate_set(("receipt", "review", "irrelevant")),
        _context("Customer submitted $300 of receipts."),
    )

    assert observation.outcome == "SELECTED"
    assert observation.selected_candidate_ids == ("literal-receipt",)
    assert observation.reason == "rule_based_category.single_match"
    diagnostic = selector.last_call_diagnostic
    assert diagnostic is not None
    signals = diagnostic["category_signals"]
    assert isinstance(signals, tuple)
    assert signals[0]["rule_id"] == "receipt-detected"
    assert signals[0]["text"] == "submitted $300 of receipts"
    assert signals[0]["source_span"]["byte_start"] == len(b"Customer ")


def test_rule_based_category_selector_abstains_on_no_signal() -> None:
    selector = _selector(
        rules=(
            CategoryRule(
                rule_id="receipt-detected",
                candidate_literal="receipt",
                pattern=r"submitted .* receipts",
            ),
        ),
    )

    observation = selector.select(
        _field_spec(),
        _candidate_set(("receipt", "review", "irrelevant")),
        _context("Customer sent an unrelated message."),
    )

    assert observation.outcome == "ABSTAINED"
    assert observation.abstain is True
    assert observation.selected_candidate_ids == ()
    assert observation.reason == "rule_based_category.no_signal"


def test_rule_based_category_selector_abstains_on_conflicting_single_label_signals() -> None:
    selector = _selector(
        rules=(
            CategoryRule(
                rule_id="receipt-detected",
                candidate_literal="receipt",
                pattern=r"receipts",
            ),
            CategoryRule(
                rule_id="ordinary-invoice",
                candidate_literal="irrelevant",
                pattern=r"ordinary invoice",
            ),
        ),
    )

    observation = selector.select(
        _field_spec(),
        _candidate_set(("receipt", "review", "irrelevant")),
        _context("Customer mentions receipts and an ordinary invoice."),
    )

    assert observation.outcome == "ABSTAINED"
    assert observation.reason == "rule_based_category.conflicting_positive_signals"


def test_rule_based_category_selector_selects_uncertain_literal_when_configured() -> None:
    selector = RuleBasedCategorySelector(
        rules=(
            CategoryRule(
                rule_id="ambiguous-language",
                candidate_literal="receipt",
                pattern=r"may submit",
                polarity="ambiguous",
            ),
        ),
        uncertain_literal="uncertain",
    )

    observation = selector.select(
        _field_spec(literal_values=("receipt", "uncertain", "irrelevant")),
        _candidate_set(("receipt", "uncertain", "irrelevant")),
        _context("Customer may submit receipts in the future."),
    )

    assert observation.outcome == "SELECTED"
    assert observation.selected_candidate_ids == ("literal-uncertain",)
    assert observation.reason == "rule_based_category.ambiguous_signal"


def test_rule_based_category_selector_many_selects_positives_in_candidate_order() -> None:
    selector = RuleBasedCategorySelector(
        rules=(
            CategoryRule(
                rule_id="second-rule",
                candidate_literal="second",
                pattern=r"second signal",
            ),
            CategoryRule(
                rule_id="first-rule",
                candidate_literal="first",
                pattern=r"first signal",
            ),
        ),
    )

    observation = selector.select(
        _field_spec(
            cardinality=Cardinality.MANY,
            literal_values=("first", "second", "third"),
        ),
        _candidate_set(("first", "second", "third")),
        _context("second signal appears before first signal."),
    )

    assert observation.outcome == "SELECTED"
    assert observation.abstain is False
    assert observation.selected_candidate_ids == ("literal-first", "literal-second")


def test_rule_based_category_selector_many_empty_selection_is_selected_empty_set() -> None:
    selector = RuleBasedCategorySelector(
        rules=(
            CategoryRule(
                rule_id="first-rule",
                candidate_literal="first",
                pattern=r"first signal",
            ),
        ),
    )

    observation = selector.select(
        _field_spec(cardinality=Cardinality.MANY, literal_values=("first", "second")),
        _candidate_set(("first", "second")),
        _context("no matching signal"),
    )

    assert observation.outcome == "SELECTED"
    assert observation.abstain is False
    assert observation.selected_candidate_ids == ()


def test_rule_based_category_selector_rejects_rules_for_unknown_literals() -> None:
    selector = RuleBasedCategorySelector(
        rules=(
            CategoryRule(
                rule_id="ghost-rule",
                candidate_literal="ghost",
                pattern=r"anything",
            ),
        ),
    )

    with pytest.raises(InfrastructureError, match="unknown_literal"):
        selector.select(
            _field_spec(),
            _candidate_set(("receipt", "irrelevant")),
            _context("anything"),
        )


class _RuleBackedVerdict(BaseModel):
    verdict: Annotated[
        Literal["receipt", "review", "irrelevant"],
        ValueKind.CATEGORY,
    ] = extract_field(
        description="document verdict",
        selector_binding=SelectorBinding(
            cls=RuleBasedCategorySelector,
            params={
                "rules": (
                    {
                        "rule_id": "receipt-detected",
                        "candidate_literal": "receipt",
                        "pattern": r"submitted .* receipts",
                    },
                ),
            },
        ),
    )


@pytest.mark.asyncio
async def test_rule_based_category_selector_runs_through_extraction_strategy() -> None:
    spec = ExtractionSpec.from_pydantic(_RuleBackedVerdict)

    result = await run_extraction(
        document="Customer submitted $300 of receipts.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result.outcome == "complete"
    assert result.to_pydantic(_RuleBackedVerdict)[0].verdict == "receipt"
    diagnostic = result.trace.events
    assert diagnostic == ()


@pytest.mark.asyncio
async def test_rule_based_category_selector_signals_persist_in_replay(
    tmp_path: Path,
) -> None:
    spec = ExtractionSpec.from_pydantic(_RuleBackedVerdict)
    store = LocalFilesystemStore(tmp_path)

    result = await SerialExecutor(storage=store).execute(
        document="Customer submitted $300 of receipts.",
        spec=spec,
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )

    artifact = read_replay(store, result.replay_artifact_ref)
    diagnostic = artifact.selector_call_diagnostics[0]

    assert diagnostic.category_signals != ()
    assert diagnostic.category_signals[0]["rule_id"] == "receipt-detected"
    assert diagnostic.category_signals[0]["candidate_literal"] == "receipt"
    assert diagnostic.model_metadata["selector_backend"] == "rule_based_category"
