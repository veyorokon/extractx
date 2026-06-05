"""spaCy NER candidate strategy tests."""

from __future__ import annotations

from typing import Annotated

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
from extractx.candidates.generators.ner import (
    NerCandidateStrategy,
    NerEntityRulerConfig,
    NerStrategyParams,
)
from extractx.core import SourceRef, StrategyBinding
from extractx.core.exceptions import InfrastructureError, SpecError
from extractx.source import TextAdapter

pytest.importorskip("spacy")


def _document(text: str):
    return TextAdapter().adapt(
        text.encode("utf-8"),
        SourceRef(source_id="test", content_hash="sha256:test"),
    )


def test_entity_ruler_emits_doc_ents_as_candidates() -> None:
    field_spec = ExtractionSpec.from_pydantic(_InvoiceAmount).fields[0]

    candidate_set = NerCandidateStrategy().generate(
        field_spec=field_spec,
        document_view=_document("Invoice total is $42.50. Ignore $250.00."),
    )

    assert [c.text for c in candidate_set.candidates] == ["$42.50", "$250.00"]
    assert [c.entity_type for c in candidate_set.candidates] == ["MONEY", "MONEY"]
    assert all(c.source_span.text_anchor_space == "source_bytes" for c in candidate_set.candidates)
    assert candidate_set.strategy_id.startswith("ner:")


def test_entity_filter_limits_labels() -> None:
    field_spec = ExtractionSpec.from_pydantic(_FilteredEntity).fields[0]

    candidate_set = NerCandidateStrategy().generate(
        field_spec=field_spec,
        document_view=_document("Ship on Friday. Invoice total is $42.50."),
    )

    assert [c.text for c in candidate_set.candidates] == ["$42.50"]
    assert [c.entity_type for c in candidate_set.candidates] == ["MONEY"]


def test_money_entities_emit_decimal_normalized_hints() -> None:
    field_spec = ExtractionSpec.from_pydantic(_PhrasalMoney).fields[0]

    candidate_set = NerCandidateStrategy().generate(
        field_spec=field_spec,
        document_view=_document(
            "The receipt total is approximately $116.18. "
            "Service agreement amount was about $42.1 million.",
        ),
    )

    assert [(c.text, c.normalized_hint) for c in candidate_set.candidates] == [
        ("approximately $116.18", "116.18"),
        ("about $42.1 million", "42100000"),
    ]


def test_utf8_char_offsets_become_normalized_text_byte_offsets() -> None:
    field_spec = ExtractionSpec.from_pydantic(_CafeName).fields[0]

    candidate_set = NerCandidateStrategy().generate(
        field_spec=field_spec,
        document_view=_document("Café paid $12."),
    )

    candidate = candidate_set.candidates[0]
    assert candidate.text == "Café"
    assert candidate.source_span.byte_start == 0
    assert candidate.source_span.byte_end == len("Café".encode())


def test_source_bytes_span_slices_original_bytes_not_python_string_indices() -> None:
    field_spec = ExtractionSpec.from_pydantic(_InvoiceAmount).fields[0]
    text = "Préface ééé. Invoice total is $42.50."
    document = _document(text)

    candidate_set = NerCandidateStrategy().generate(
        field_spec=field_spec,
        document_view=document,
    )

    candidate = candidate_set.candidates[0]
    source_bytes = text.encode("utf-8")
    char_start = text.index("$42.50")
    byte_start = len(text[:char_start].encode("utf-8"))
    assert byte_start != char_start
    assert candidate.source_span.text_anchor_space == "source_bytes"
    assert candidate.source_span.byte_start == byte_start
    assert candidate.source_span.byte_end == byte_start + len(b"$42.50")
    assert (
        source_bytes[candidate.source_span.byte_start : candidate.source_span.byte_end] == b"$42.50"
    )


def test_missing_registered_component_fails_loudly() -> None:
    with pytest.raises(InfrastructureError, match="ner\\.missing_component"):
        NerCandidateStrategy().generate(
            field_spec=ExtractionSpec.from_pydantic(_MissingComponent).fields[0],
            document_view=_document("Invoice total is $42.50."),
        )


def test_invalid_params_raise_spec_error() -> None:
    with pytest.raises(SpecError, match="NerStrategyParams"):
        NerStrategyParams.from_mapping({"unknown": True})


def test_long_document_chunking_finds_entities_after_first_chunk() -> None:
    field_spec = ExtractionSpec.from_pydantic(_ChunkedMoney).fields[0]
    document = _document(("x" * 70) + " $250.00")

    candidate_set = NerCandidateStrategy().generate(
        field_spec=field_spec,
        document_view=document,
    )

    assert [c.text for c in candidate_set.candidates] == ["$250.00"]
    assert candidate_set.candidates[0].source_span.byte_start == 71


def test_long_document_chunking_dedupes_overlap_by_candidate_id() -> None:
    field_spec = ExtractionSpec.from_pydantic(_ChunkedMoney).fields[0]
    document = _document(("x" * 29) + " $42.50 " + ("y" * 30))

    candidate_set = NerCandidateStrategy().generate(
        field_spec=field_spec,
        document_view=document,
    )

    assert [c.text for c in candidate_set.candidates] == ["$42.50"]


def test_long_document_chunking_maps_utf8_offsets_to_original_document() -> None:
    field_spec = ExtractionSpec.from_pydantic(_ChunkedMoney).fields[0]
    text = ("é" * 29) + " $42.50"
    document = _document(text)

    candidate_set = NerCandidateStrategy().generate(
        field_spec=field_spec,
        document_view=document,
    )

    candidate = candidate_set.candidates[0]
    assert candidate.source_span.byte_start == len((("é" * 29) + " ").encode())
    assert candidate.source_span.byte_end == len(text.encode())


def test_oversize_fail_policy_raises_typed_infrastructure_error() -> None:
    field_spec = ExtractionSpec.from_pydantic(_FailOnOversizeMoney).fields[0]

    with pytest.raises(InfrastructureError, match="ner\\.document_too_long"):
        NerCandidateStrategy().generate(
            field_spec=field_spec,
            document_view=_document("x" * 1_000_001),
        )


def test_default_chunk_policy_handles_text_above_spacy_max_length() -> None:
    field_spec = ExtractionSpec.from_pydantic(_LargeChunkedMoney).fields[0]

    candidate_set = NerCandidateStrategy().generate(
        field_spec=field_spec,
        document_view=_document(("x" * 1_000_001) + " $250.00"),
    )

    assert [c.text for c in candidate_set.candidates] == ["$250.00"]


@pytest.mark.asyncio
async def test_ner_strategy_runs_through_extraction() -> None:
    result = await run_extraction(
        document="Invoice total is $42.50.",
        spec=ExtractionSpec.from_pydantic(_InvoiceAmount),
        runtime=Runtime(),
        policy=ExecutorPolicy(strategy="independent"),
    )

    assert result.outcome == "complete"
    assert result.instances[0].evidence[0].normalized_value == "$42.50"


class _InvoiceAmount(BaseModel):
    amount: Annotated[str, ValueKind.MONEY] = extract_field(
        description="invoice amount",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                params={
                    "model_id": "en",
                    "entity_rulers": (
                        NerEntityRulerConfig(
                            name="amount_ruler",
                            patterns=({"label": "MONEY", "pattern": "$42.50"},),
                        ).model_dump(mode="json"),
                        NerEntityRulerConfig(
                            name="other_amount_ruler",
                            patterns=({"label": "MONEY", "pattern": "$250.00"},),
                        ).model_dump(mode="json"),
                    ),
                },
                kind="candidate",
            ),
        ),
    )


class _FilteredEntity(BaseModel):
    entity: Annotated[str, ValueKind.MONEY] = extract_field(
        description="money entity",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                params={
                    "model_id": "en",
                    "entity_rulers": (
                        {
                            "name": "mixed_ruler",
                            "patterns": (
                                {"label": "DATE", "pattern": "Friday"},
                                {"label": "MONEY", "pattern": "$42.50"},
                            ),
                        },
                    ),
                    "entity_filter": ("MONEY",),
                },
                kind="candidate",
            ),
        ),
    )


class _PhrasalMoney(BaseModel):
    amount: Annotated[str, ValueKind.MONEY] = extract_field(
        description="phrasal money entity",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                params={
                    "model_id": "en",
                    "entity_rulers": (
                        {
                            "name": "phrasal_money_ruler",
                            "patterns": (
                                {"label": "MONEY", "pattern": "approximately $116.18"},
                                {"label": "MONEY", "pattern": "about $42.1 million"},
                            ),
                        },
                    ),
                },
                kind="candidate",
            ),
        ),
    )


class _ChunkedMoney(BaseModel):
    amount: Annotated[str, ValueKind.MONEY] = extract_field(
        description="chunked money entity",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                params={
                    "model_id": "en",
                    "max_chars_per_chunk": 40,
                    "chunk_overlap_chars": 20,
                    "entity_rulers": (
                        {
                            "name": "chunked_money_ruler",
                            "patterns": (
                                {"label": "MONEY", "pattern": "$42.50"},
                                {"label": "MONEY", "pattern": "$250.00"},
                            ),
                        },
                    ),
                },
                kind="candidate",
            ),
        ),
    )


class _FailOnOversizeMoney(BaseModel):
    amount: Annotated[str, ValueKind.MONEY] = extract_field(
        description="oversize money entity",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                params={
                    "model_id": "en",
                    "oversize_policy": "fail",
                    "entity_rulers": (
                        {
                            "name": "fail_money_ruler",
                            "patterns": ({"label": "MONEY", "pattern": "$42.50"},),
                        },
                    ),
                },
                kind="candidate",
            ),
        ),
    )


class _LargeChunkedMoney(BaseModel):
    amount: Annotated[str, ValueKind.MONEY] = extract_field(
        description="large chunked money entity",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                params={
                    "model_id": "en",
                    "entity_rulers": (
                        {
                            "name": "large_chunked_money_ruler",
                            "patterns": ({"label": "MONEY", "pattern": "$250.00"},),
                        },
                    ),
                },
                kind="candidate",
            ),
        ),
    )


class _CafeName(BaseModel):
    name: Annotated[str, ValueKind.ORG] = extract_field(
        description="cafe name",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                params={
                    "model_id": "en",
                    "entity_rulers": (
                        {
                            "name": "org_ruler",
                            "patterns": ({"label": "ORG", "pattern": "Café"},),
                        },
                    ),
                },
                kind="candidate",
            ),
        ),
    )


class _MissingComponent(BaseModel):
    amount: Annotated[str, ValueKind.MONEY] = extract_field(
        description="invoice amount",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                params={"model_id": "en", "filter_components": ("not_registered",)},
                kind="candidate",
            ),
        ),
    )
