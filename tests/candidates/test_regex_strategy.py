"""focused tests for the phase-1 `RegexCandidateStrategy`.

proof targets (from docs/tasks/seam-c-deterministic-candidate-generation.md,
"Focused proof"):

- same `(FieldSpec, DocumentView, InstanceHint)` yields the same `CandidateSet`.
- `candidate_id` is deterministic for identical candidate content.
- candidate ids are unique within one `CandidateSet`.
- regex patterns come from explicit binding params; no hidden inference
  from description or `ValueKind`.
- all emitted spans carry `text_anchor_space="source_bytes"` and are
  recoverable through `anchor_map` inversion.
- `CandidateSet.instance_hint` faithfully carries the supplied hint.
- repeated equal-text matches are NOT deduplicated when their evidential
  identity differs.
- empty regex match set yields an empty `CandidateSet`, not an exception.
- malformed or incomplete regex strategy params fail loudly.

seam-local discipline: these tests consume real `DocumentView`s produced
by the seam-A phase-1 adapters (`TextAdapter`, `HtmlAdapter`). we do not
hand-construct `AnchorMap`s here — the seam-A output surface is already
covered by its own tests, and reusing it catches integration drift.
"""

from __future__ import annotations

import re

import pytest

from extractx.candidates import RegexCandidateStrategy
from extractx.candidates.generators.regex import (
    RegexStrategyParams,
    _match_to_source_slices,
    _segment_views,
)
from extractx.core import (
    Candidate,
    Cardinality,
    DocumentView,
    FieldSpec,
    InstanceGroupingKey,
    SourceRef,
    SourceSpan,
    StrategyBinding,
    ValueKind,
    anchor_invert,
)
from extractx.core.exceptions import SpecError
from extractx.source import HtmlAdapter, TextAdapter

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ref(source_id: str = "doc-1", content_hash: str = "sha256:abc") -> SourceRef:
    return SourceRef(source_id=source_id, content_hash=content_hash)


def _field_spec(
    *,
    pattern: str | None = "\\d{3}-\\d{4}",
    flags: int = 0,
    group: int | str | None = None,
    description: str = "phone numbers",
    with_binding: bool = True,
) -> FieldSpec:
    """build a narrow `FieldSpec` bound to the regex strategy.

    most tests only care about `strategy_bindings[0].params`; we keep the
    other fields at their minimal valid shape.
    """

    bindings: tuple[StrategyBinding, ...] = ()
    if with_binding:
        params: dict[str, object] = {}
        if pattern is not None:
            params["pattern"] = pattern
        if flags:
            params["flags"] = flags
        if group is not None:
            params["group"] = group
        bindings = (
            StrategyBinding(
                cls=RegexCandidateStrategy,
                params=params,
                kind="candidate",
            ),
        )
    return FieldSpec(
        field_id="phone",
        description=description,
        value_kind=ValueKind.register("PHONE"),
        cardinality=Cardinality.MANY,
        python_type=str,
        strategy_bindings=bindings,
    )


def _text_view(raw: bytes) -> DocumentView:
    return TextAdapter().adapt(raw, _ref())


def _html_view(raw: bytes) -> DocumentView:
    return HtmlAdapter().adapt(raw, _ref())


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_inputs_produce_equal_candidate_sets(self) -> None:
        view = _text_view(b"call 555-1234 or 555-9876 for info")
        spec = _field_spec()
        s = RegexCandidateStrategy()
        a = s.generate(spec, view)
        b = s.generate(spec, view)
        assert a == b

    def test_candidate_id_deterministic_across_independent_runs(self) -> None:
        view = _text_view(b"555-1234 again 555-1234")
        spec = _field_spec()
        s1 = RegexCandidateStrategy()
        s2 = RegexCandidateStrategy()
        r1 = s1.generate(spec, view)
        r2 = s2.generate(spec, view)
        assert tuple(c.candidate_id for c in r1.candidates) == tuple(
            c.candidate_id for c in r2.candidates
        )

    def test_duplicate_text_distinct_spans_are_not_dedup_ed(self) -> None:
        # same literal match appears at two different source offsets;
        # both candidates must survive with distinct ids.
        view = _text_view(b"555-1234 ... 555-1234")
        spec = _field_spec()
        result = RegexCandidateStrategy().generate(spec, view)
        texts = [c.text for c in result.candidates]
        ids = [c.candidate_id for c in result.candidates]
        assert texts == ["555-1234", "555-1234"]
        assert len(set(ids)) == 2

    def test_candidate_ids_unique_within_set(self) -> None:
        view = _text_view(b"a1 b2 c3 d4 e5 f6")
        spec = _field_spec(pattern="[a-z]\\d", description="tokens")
        result = RegexCandidateStrategy().generate(spec, view)
        ids = [c.candidate_id for c in result.candidates]
        assert len(ids) == len(set(ids))

    def test_candidate_context_carries_bounded_surrounding_text(self) -> None:
        view = _text_view(b"For customer support, call 555-1234.")
        spec = _field_spec()

        result = RegexCandidateStrategy().generate(spec, view)

        assert len(result.candidates) == 1
        assert result.candidates[0].context == "For customer support, call 555-1234."

    def test_candidate_context_window_is_strategy_param(self) -> None:
        view = _text_view(b"aaaa call 555-1234 now bbbb")
        spec = _field_spec()
        assert spec.strategy_bindings
        binding = spec.strategy_bindings[0]
        spec = spec.model_copy(
            update={
                "strategy_bindings": (
                    binding.model_copy(
                        update={
                            "params": {
                                **dict(binding.params),
                                "context_window_bytes": 5,
                            },
                        },
                    ),
                ),
            }
        )

        result = RegexCandidateStrategy().generate(spec, view)

        assert result.candidates[0].context == "call 555-1234 now "

    def test_regex_strategy_uses_injected_context_builder(self) -> None:
        class StaticContextBuilder:
            def build(
                self,
                *,
                normalized_bytes: bytes,
                match_start: int,
                match_end: int,
            ) -> str:
                assert normalized_bytes
                assert match_start < match_end
                return "custom context"

        view = _text_view(b"For customer support, call 555-1234.")
        spec = _field_spec()

        result = RegexCandidateStrategy(
            context_builder=StaticContextBuilder(),
        ).generate(spec, view)

        assert result.candidates[0].context == "custom context"


# ---------------------------------------------------------------------------
# empty / no-match
# ---------------------------------------------------------------------------


class TestEmptyOutput:
    def test_no_matches_yields_empty_candidate_set_not_exception(self) -> None:
        view = _text_view(b"plain prose with no digits")
        spec = _field_spec()
        result = RegexCandidateStrategy().generate(spec, view)
        assert result.candidates == ()
        # canonical shape preserved even when empty — instance_hint,
        # strategy_id, document_id, field_id all present.
        assert result.field_id == spec.field_id
        assert result.document_id == view.document_id
        assert result.strategy_id.startswith("regex:")

    def test_empty_document_yields_empty_candidate_set(self) -> None:
        view = _text_view(b"")
        spec = _field_spec()
        result = RegexCandidateStrategy().generate(spec, view)
        assert result.candidates == ()


# ---------------------------------------------------------------------------
# span honesty — source-bytes / anchor-map round-trip
# ---------------------------------------------------------------------------


class TestSpanHonesty:
    def test_text_adapter_spans_are_source_bytes_and_invertible(self) -> None:
        view = _text_view(b"call 555-1234 plz")
        spec = _field_spec()
        result = RegexCandidateStrategy().generate(spec, view)
        assert len(result.candidates) == 1
        cand = result.candidates[0]
        assert cand.source_span.text_anchor_space == "source_bytes"
        # the span must be recoverable through `anchor_invert`. if it
        # were not, seam F layer 1 would reject the candidate later.
        invert = anchor_invert(view.anchor_map, cand.source_span)
        assert isinstance(invert, tuple) and len(invert) == 2
        # the recovered normalized range must slice to the matched text
        norm_start, norm_end = invert
        assert view.normalized_text.encode("utf-8")[norm_start:norm_end] == b"555-1234"

    def test_html_adapter_spans_are_source_bytes_and_invertible(self) -> None:
        # match sits inside a run of identity segments (no entities
        # crossed), so a contiguous single source-bytes span is honest.
        raw = b"<p>call 555-1234 plz</p>"
        view = _html_view(raw)
        # sanity: the html adapter stripped tags but kept text content
        assert "555-1234" in view.normalized_text
        spec = _field_spec()
        result = RegexCandidateStrategy().generate(spec, view)
        assert len(result.candidates) == 1
        cand = result.candidates[0]
        assert cand.source_span.text_anchor_space == "source_bytes"
        # source bytes should point back to the same literal bytes in raw
        raw_start, raw_end = cand.source_span.byte_start, cand.source_span.byte_end
        assert raw[raw_start:raw_end] == b"555-1234"

    def test_all_emitted_spans_share_the_views_text_anchor_space(self) -> None:
        view = _text_view(b"a1 b2 c3 d4 e5 f6 g7 h8 i9")
        spec = _field_spec(pattern="[a-z]\\d", description="tokens")
        result = RegexCandidateStrategy().generate(spec, view)
        assert result.candidates, "expected matches against this fixture"
        spaces = {c.source_span.text_anchor_space for c in result.candidates}
        assert spaces == {"source_bytes"}
        for c in result.candidates:
            for es in c.evidence_spans:
                assert es.text_anchor_space == "source_bytes"

    def test_candidates_carry_normalized_context_and_match_spans(self) -> None:
        view = _text_view(b"aaaa call 555-1234 now bbbb")
        spec = _field_spec()
        assert spec.strategy_bindings
        binding = spec.strategy_bindings[0]
        spec = spec.model_copy(
            update={
                "strategy_bindings": (
                    binding.model_copy(
                        update={
                            "params": {
                                **dict(binding.params),
                                "context_window_bytes": 5,
                            },
                        },
                    ),
                ),
            },
        )

        result = RegexCandidateStrategy().generate(spec, view)

        cand = result.candidates[0]
        assert cand.context == "call 555-1234 now "
        assert cand.context_span is not None
        assert cand.context_span.text_anchor_space == "normalized_text"
        assert cand.context_span.byte_start == 5
        assert cand.context_span.byte_end == 23
        assert cand.normalized_span is not None
        assert cand.normalized_span.text_anchor_space == "normalized_text"
        assert cand.normalized_span.byte_start == 10
        assert cand.normalized_span.byte_end == 18

    def test_multibyte_match_round_trips(self) -> None:
        # é is two UTF-8 bytes (0xC3 0xA9); the match range must lie on
        # UTF-8 code-point boundaries so `anchor_invert` succeeds.
        view = _text_view("café 123".encode())
        spec = _field_spec(pattern="\\d+", description="number")
        result = RegexCandidateStrategy().generate(spec, view)
        assert len(result.candidates) == 1
        cand = result.candidates[0]
        norm_start, norm_end = anchor_invert(view.anchor_map, cand.source_span)
        assert view.normalized_text.encode("utf-8")[norm_start:norm_end] == b"123"

    def test_source_bytes_span_slices_original_bytes_not_python_string_indices(self) -> None:
        text = "Préface ééé. Invoice total is $42.50."
        view = _text_view(text.encode())
        spec = _field_spec(pattern=r"\$42\.50", description="amount")

        result = RegexCandidateStrategy().generate(spec, view)

        cand = result.candidates[0]
        char_start = text.index("$42.50")
        byte_start = len(text[:char_start].encode("utf-8"))
        assert byte_start != char_start
        assert cand.source_span.text_anchor_space == "source_bytes"
        assert cand.source_span.byte_start == byte_start
        assert cand.source_span.byte_end == byte_start + len(b"$42.50")
        assert text.encode()[cand.source_span.byte_start : cand.source_span.byte_end] == b"$42.50"


# ---------------------------------------------------------------------------
# html non-identity segment discipline
# ---------------------------------------------------------------------------


class TestHtmlEntitySpans:
    def test_regex_match_fully_inside_one_entity_is_whole_image(self) -> None:
        # `&amp;` is a non-identity segment: 1 normalized byte (`&`) from
        # 5 source bytes. a regex matching the decoded `&` must produce
        # a source_span covering the full `&amp;` source image — that
        # is what `anchor_lookup` would return for an offset inside a
        # non-identity segment, and this strategy obeys the same rule.
        raw = b"<p>a &amp; b</p>"
        view = _html_view(raw)
        # normalized_text should contain the decoded "&"
        assert "&" in view.normalized_text
        spec = _field_spec(pattern="&", description="ampersand")
        result = RegexCandidateStrategy().generate(spec, view)
        assert len(result.candidates) == 1
        cand = result.candidates[0]
        assert cand.source_span.text_anchor_space == "source_bytes"
        # the source image of the entity in the raw document is "&amp;"
        raw_start = cand.source_span.byte_start
        raw_end = cand.source_span.byte_end
        assert raw[raw_start:raw_end] == b"&amp;"


# ---------------------------------------------------------------------------
# instance_hint passthrough
# ---------------------------------------------------------------------------


class TestInstanceHintPassthrough:
    def _key(self) -> InstanceGroupingKey:
        view = _text_view(b"abc")
        span = next(iter(view.anchor_map.entries))[1]
        return InstanceGroupingKey(group_id="g1", ordinal=0, group_anchors=(span,))

    def test_hint_is_attached_to_candidate_set(self) -> None:
        hint = self._key()
        view = _text_view(b"555-1234 and 555-9876")
        spec = _field_spec()
        result = RegexCandidateStrategy().generate(spec, view, hint)
        assert result.instance_hint == hint

    def test_absent_hint_is_none(self) -> None:
        view = _text_view(b"555-1234")
        spec = _field_spec()
        result = RegexCandidateStrategy().generate(spec, view)
        assert result.instance_hint is None


# ---------------------------------------------------------------------------
# explicit-params discipline (no hidden inference)
# ---------------------------------------------------------------------------


class TestExplicitParamsOnly:
    def test_without_strategy_binding_fails_loudly(self) -> None:
        spec = _field_spec(with_binding=False)
        view = _text_view(b"555-1234")
        with pytest.raises(SpecError):
            RegexCandidateStrategy().generate(spec, view)

    def test_without_pattern_param_fails_loudly(self) -> None:
        spec = _field_spec(pattern=None)
        view = _text_view(b"555-1234")
        with pytest.raises(SpecError):
            RegexCandidateStrategy().generate(spec, view)

    def test_non_string_pattern_fails_loudly(self) -> None:
        binding = StrategyBinding(
            cls=RegexCandidateStrategy,
            params={"pattern": 123},
            kind="candidate",
        )
        spec = FieldSpec(
            field_id="x",
            description="",
            value_kind=ValueKind.register("PHONE"),
            cardinality=Cardinality.MANY,
            python_type=str,
            strategy_bindings=(binding,),
        )
        view = _text_view(b"555-1234")
        with pytest.raises(SpecError):
            RegexCandidateStrategy().generate(spec, view)

    def test_malformed_pattern_fails_loudly_at_params_build(self) -> None:
        with pytest.raises(SpecError):
            RegexStrategyParams.from_mapping({"pattern": "("})

    def test_unknown_param_key_fails_loudly(self) -> None:
        with pytest.raises(SpecError):
            RegexStrategyParams.from_mapping(
                {"pattern": "x", "candidates": "hidden"},
            )

    def test_bool_flags_rejected(self) -> None:
        with pytest.raises(SpecError):
            RegexStrategyParams.from_mapping({"pattern": "x", "flags": True})

    def test_binding_for_wrong_class_fails_loudly(self) -> None:
        class OtherStrategy:
            pass

        binding = StrategyBinding(
            cls=OtherStrategy,
            params={"pattern": "x"},
            kind="candidate",
        )
        spec = FieldSpec(
            field_id="x",
            description="",
            value_kind=ValueKind.register("PHONE"),
            cardinality=Cardinality.MANY,
            python_type=str,
            strategy_bindings=(binding,),
        )
        view = _text_view(b"x")
        with pytest.raises(SpecError):
            RegexCandidateStrategy().generate(spec, view)

    def test_description_is_never_a_pattern_source(self) -> None:
        # description says "phone numbers" — that is not a regex, and
        # must not be inferred as one. absence of a pattern param with
        # only a description must fail, not succeed with zero matches.
        binding = StrategyBinding(
            cls=RegexCandidateStrategy,
            params={},  # no pattern
            kind="candidate",
        )
        spec = FieldSpec(
            field_id="x",
            description="phone numbers",
            value_kind=ValueKind.register("PHONE"),
            cardinality=Cardinality.MANY,
            python_type=str,
            strategy_bindings=(binding,),
        )
        view = _text_view(b"555-1234")
        with pytest.raises(SpecError):
            RegexCandidateStrategy().generate(spec, view)

    def test_value_kind_does_not_supply_default_patterns(self) -> None:
        # two specs with the same binding shape but different ValueKinds
        # must produce the same `CandidateSet` candidates — ValueKind is
        # not a pattern source. (strategy_id and ids match because the
        # id depends on params + spans, not ValueKind.)
        view = _text_view(b"555-1234")
        a = RegexCandidateStrategy().generate(_field_spec(), view)
        # rebuild with a different value_kind
        binding = StrategyBinding(
            cls=RegexCandidateStrategy,
            params={"pattern": "\\d{3}-\\d{4}"},
            kind="candidate",
        )
        other_spec = FieldSpec(
            field_id="phone",
            description="",
            value_kind=ValueKind.register("MONEY"),  # different
            cardinality=Cardinality.MANY,
            python_type=str,
            strategy_bindings=(binding,),
        )
        b = RegexCandidateStrategy().generate(other_spec, view)
        assert [c.candidate_id for c in a.candidates] == [c.candidate_id for c in b.candidates]


# ---------------------------------------------------------------------------
# capture-group support
# ---------------------------------------------------------------------------


class TestCaptureGroups:
    def test_named_group_drives_primary_span(self) -> None:
        view = _text_view(b"call (555) 123-4567 today")
        binding = StrategyBinding(
            cls=RegexCandidateStrategy,
            params={
                "pattern": r"\((?P<area>\d{3})\) \d{3}-\d{4}",
                "group": "area",
            },
            kind="candidate",
        )
        spec = FieldSpec(
            field_id="area",
            description="",
            value_kind=ValueKind.register("AREA_CODE"),
            cardinality=Cardinality.MANY,
            python_type=str,
            strategy_bindings=(binding,),
        )
        result = RegexCandidateStrategy().generate(spec, view)
        assert len(result.candidates) == 1
        cand = result.candidates[0]
        assert cand.text == "555"
        # source_span points at the "555" bytes, not the entire match
        src = cand.source_span
        assert (
            view.normalized_text.encode("utf-8").find(b"555")
            == (anchor_invert(view.anchor_map, src)[0])
        )

    def test_nonexistent_group_raises(self) -> None:
        view = _text_view(b"ab")
        binding = StrategyBinding(
            cls=RegexCandidateStrategy,
            params={"pattern": r"(?P<x>a)(?P<y>b)", "group": "z"},
            kind="candidate",
        )
        spec = FieldSpec(
            field_id="x",
            description="",
            value_kind=ValueKind.register("PHONE"),
            cardinality=Cardinality.MANY,
            python_type=str,
            strategy_bindings=(binding,),
        )
        with pytest.raises(SpecError):
            RegexCandidateStrategy().generate(spec, view)

    def test_optional_group_that_does_not_participate_is_skipped(self) -> None:
        # `a(b)?c` matches "ac" without participating `b`. the selected
        # group's start/end are -1; our strategy skips cleanly rather
        # than emitting a fabricated span.
        view = _text_view(b"ac")
        binding = StrategyBinding(
            cls=RegexCandidateStrategy,
            params={"pattern": "a(b)?c", "group": 1},
            kind="candidate",
        )
        spec = FieldSpec(
            field_id="x",
            description="",
            value_kind=ValueKind.register("PHONE"),
            cardinality=Cardinality.MANY,
            python_type=str,
            strategy_bindings=(binding,),
        )
        result = RegexCandidateStrategy().generate(spec, view)
        assert result.candidates == ()


# ---------------------------------------------------------------------------
# flags passthrough
# ---------------------------------------------------------------------------


class TestFlags:
    def test_ignorecase_flag_applied(self) -> None:
        view = _text_view(b"HELLO hello HeLLo")
        spec_sensitive = _field_spec(
            pattern="hello",
            description="greeting",
        )
        spec_insensitive = _field_spec(
            pattern="hello",
            flags=int(re.IGNORECASE),
            description="greeting",
        )
        s = RegexCandidateStrategy()
        sensitive = s.generate(spec_sensitive, view)
        insensitive = s.generate(spec_insensitive, view)
        assert len(sensitive.candidates) == 1
        assert len(insensitive.candidates) == 3


# ---------------------------------------------------------------------------
# internal helpers — span translation corners
# ---------------------------------------------------------------------------


class TestMatchToSourceSlicesHelper:
    def test_zero_length_match_on_empty_doc(self) -> None:
        view = _text_view(b"")
        segments = _segment_views(view.anchor_map, len(view.normalized_text.encode()))
        assert (
            _match_to_source_slices(
                match_start=0,
                match_end=0,
                segments=segments,
            )
            == []
        )

    def test_contiguous_identity_segments_merge_to_one_slice(self) -> None:
        # the html adapter emits a new segment for each text event; a
        # regex crossing a segment boundary that has no source-byte gap
        # must collapse to one source slice.
        view = _html_view(b"<p>abcdef</p>")
        segments = _segment_views(view.anchor_map, len(view.normalized_text.encode()))
        # match "bcde" which should be one identity run inside one segment
        slices = _match_to_source_slices(match_start=1, match_end=5, segments=segments)
        assert len(slices) == 1


# ---------------------------------------------------------------------------
# candidate type invariants
# ---------------------------------------------------------------------------


class TestCandidateShapeInvariants:
    def test_candidates_are_frozen_candidate_instances(self) -> None:
        view = _text_view(b"555-1234")
        spec = _field_spec()
        result = RegexCandidateStrategy().generate(spec, view)
        assert all(isinstance(c, Candidate) for c in result.candidates)

    def test_every_source_span_is_a_source_span(self) -> None:
        view = _text_view(b"555-1234")
        spec = _field_spec()
        result = RegexCandidateStrategy().generate(spec, view)
        for c in result.candidates:
            assert isinstance(c.source_span, SourceSpan)
