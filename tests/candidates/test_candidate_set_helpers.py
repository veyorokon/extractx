"""focused tests for the seam-C helper surface.

proof targets:

- `candidate_id_for` is deterministic across calls for identical content
  and changes when any component changes (strategy_id, source_span,
  evidence_spans, normalized_structural_payload).
- `build_candidate_set` fails loudly on duplicate `candidate_id` and
  preserves the `instance_hint` the caller supplied; empty input is a
  valid output.
- `validate_source_span_against_view` rejects span coordinates that do
  not round-trip through `anchor_map` — seam C's local honesty gate
  (separate from seam F layer 1's canonical check).
"""

from __future__ import annotations

import pytest

from extractx.candidates import (
    build_candidate_set,
    candidate_id_for,
    validate_source_span_against_view,
)
from extractx.core import (
    Candidate,
    SourceRef,
    SourceSpan,
)
from extractx.source import TextAdapter


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _span(start: int, end: int, space: str = "source_bytes") -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space=space,  # type: ignore[arg-type]
        byte_start=start,
        byte_end=end,
    )


class TestCandidateIdFor:
    def test_deterministic_for_identical_inputs(self) -> None:
        a = candidate_id_for(
            strategy_id="regex:abc",
            source_span=_span(0, 3),
            evidence_spans=(),
            normalized_structural_payload=None,
        )
        b = candidate_id_for(
            strategy_id="regex:abc",
            source_span=_span(0, 3),
            evidence_spans=(),
            normalized_structural_payload=None,
        )
        assert a == b

    def test_changes_when_strategy_id_changes(self) -> None:
        a = candidate_id_for(
            strategy_id="regex:abc",
            source_span=_span(0, 3),
        )
        b = candidate_id_for(
            strategy_id="regex:def",
            source_span=_span(0, 3),
        )
        assert a != b

    def test_changes_when_source_span_changes(self) -> None:
        a = candidate_id_for(strategy_id="s", source_span=_span(0, 3))
        b = candidate_id_for(strategy_id="s", source_span=_span(0, 4))
        assert a != b

    def test_changes_when_evidence_spans_change(self) -> None:
        a = candidate_id_for(strategy_id="s", source_span=_span(0, 3))
        b = candidate_id_for(
            strategy_id="s",
            source_span=_span(0, 3),
            evidence_spans=(_span(4, 7),),
        )
        assert a != b

    def test_none_payload_distinct_from_empty_payload(self) -> None:
        # `None` and `{}` must hash differently so a payload-less
        # strategy is not confused with an empty-payload one.
        a = candidate_id_for(
            strategy_id="s",
            source_span=_span(0, 3),
            normalized_structural_payload=None,
        )
        b = candidate_id_for(
            strategy_id="s",
            source_span=_span(0, 3),
            normalized_structural_payload={},
        )
        assert a != b

    def test_empty_strategy_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="strategy_id"):
            candidate_id_for(strategy_id="", source_span=_span(0, 3))


class TestBuildCandidateSet:
    def _candidate(self, start: int, end: int, *, strategy_id: str = "s") -> Candidate:
        span = _span(start, end)
        cid = candidate_id_for(strategy_id=strategy_id, source_span=span)
        return Candidate(
            candidate_id=cid,
            text="x",
            source_span=span,
        )

    def test_empty_candidates_are_valid(self) -> None:
        cs = build_candidate_set(
            field_id="f",
            document_id="d",
            candidates=(),
            strategy_id="s",
        )
        assert cs.candidates == ()
        assert cs.strategy_id == "s"

    def test_duplicate_candidate_id_raises(self) -> None:
        c = self._candidate(0, 3)
        with pytest.raises(ValueError, match="duplicate candidate_id"):
            build_candidate_set(
                field_id="f",
                document_id="d",
                candidates=(c, c),
                strategy_id="s",
            )

    def test_instance_hint_passthrough(self) -> None:
        from extractx.core import InstanceGroupingKey

        hint = InstanceGroupingKey(group_id="g1", ordinal=0, group_anchors=(_span(0, 1),))
        cs = build_candidate_set(
            field_id="f",
            document_id="d",
            candidates=(),
            strategy_id="s",
            instance_hint=hint,
        )
        assert cs.instance_hint == hint

    def test_duplicate_text_distinct_ids_is_allowed(self) -> None:
        c1 = self._candidate(0, 3)
        c2 = self._candidate(10, 13)
        cs = build_candidate_set(
            field_id="f",
            document_id="d",
            candidates=(c1, c2),
            strategy_id="s",
        )
        assert len(cs.candidates) == 2


class TestValidateSourceSpanAgainstView:
    def test_valid_source_bytes_span_accepted(self) -> None:
        view = TextAdapter().adapt(b"hello world", _ref())
        span = SourceSpan(
            source_ref=view.source_ref,
            text_anchor_space="source_bytes",
            byte_start=0,
            byte_end=5,
        )
        validate_source_span_against_view(span, view)  # no raise

    def test_out_of_range_source_bytes_span_rejected(self) -> None:
        view = TextAdapter().adapt(b"hello", _ref())
        # 5 is the document length; 6 is beyond any segment image
        span = SourceSpan(
            source_ref=view.source_ref,
            text_anchor_space="source_bytes",
            byte_start=0,
            byte_end=6,
        )
        with pytest.raises(ValueError):
            validate_source_span_against_view(span, view)

    def test_normalized_text_span_dispatches_to_check_helper(self) -> None:
        view = TextAdapter().adapt(b"hello", _ref())
        # the strategy path emits source_bytes; this validator still
        # accepts well-formed normalized_text spans so seam F layer 1
        # can share the helper when paginated-visual adapters land.
        span = SourceSpan(
            source_ref=view.source_ref,
            text_anchor_space="normalized_text",
            byte_start=0,
            byte_end=5,
        )
        validate_source_span_against_view(span, view)  # no raise
