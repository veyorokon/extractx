"""Classification context strategy contract tests."""

from __future__ import annotations

from extractx import Cardinality, ValueKind
from extractx.core import FieldSpec, SourceRef
from extractx.selection import RegexWindowClassificationContextStrategy
from extractx.source import TextAdapter


def test_regex_window_classification_context_strategy_emits_grounded_windows() -> None:
    document = TextAdapter().adapt(
        b"Intro sentence. The customer uploaded service receipt. Closing sentence.",
        SourceRef(source_id="test", content_hash="sha256:test"),
    )
    field_spec = FieldSpec(
        field_id="verdict",
        description="classify document",
        value_kind=ValueKind.CATEGORY,
        cardinality=Cardinality.ONE,
        python_type=str,
        literal_values=("receipt", "irrelevant"),
    )
    strategy = RegexWindowClassificationContextStrategy(
        patterns=(r"service receipt",),
        before_chars=10,
        after_chars=10,
        max_window_chars=80,
        boundary_mode="punctuation",
    )

    context_set = strategy.generate(field_spec, document)

    assert context_set.field_id == "verdict"
    assert context_set.document_id == document.document_id
    assert context_set.strategy_id == "regex_window_classification_context:v1"
    assert context_set.overflow is None
    assert len(context_set.windows) == 1
    window = context_set.windows[0]
    assert window.window_id
    assert window.matched_terms == ("service receipt",)
    assert "service receipt" in window.text
    assert window.source_span.text_anchor_space == "normalized_text"
    assert document.normalized_text.encode("utf-8")[
        window.source_span.byte_start : window.source_span.byte_end
    ].decode("utf-8") == window.text
