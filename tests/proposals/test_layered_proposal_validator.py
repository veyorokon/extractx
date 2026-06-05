"""behavioral tests for the phase-1 `LayeredProposalValidator`.

proof targets (from docs/tasks/seam-f-phase-1-candidate-and-field-validation.md,
"Focused proof"):

- purity
- layer 1 span validity — `normalized_text` path (aligned in-range pass;
  UTF-8 misalignment fail; out-of-range fail)
- layer 1 span validity — `source_bytes` path (round-trip pass; span
  out-of-range fail)
- layer 1 `text_anchor_space` mismatch with adapter subcontract
- layer 1 `structured_payload` shape (non-JSON-safe fail)
- layer 1 is non-retryable (never emits `ValidationFailure`)
- layer 2 pydantic path — coercion + `field_validator` success and
  failure
- layer 2 manual path — `ValidationBinding.normalizer` +
  `FieldValidator`s in declared order (success, rejection, ordering)
- single normalization site (counter assertion)
- `Pydantic-as-Extractor` rejection at spec load, not at layer 2
- `ValidatedField.field_validation_version` determinism (stable across
  calls; changes when normalizer qualname changes; changes when
  `field_validator` qualname changes)
- lifecycle: `ValidatedField.proposed` identity preserved; frozen
- layer 3 absence: a raising `model_validator` attached to the schema
  class is never called during `validate(...)`
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import BaseModel, BeforeValidator, ValidationError, field_validator, model_validator

from extractx.core import (
    AnchorMap,
    Cardinality,
    DocumentView,
    FieldSpec,
    InstanceGroupingKey,
    NegativeOutcome,
    ProposedField,
    SourceRef,
    SourceSpan,
    ValidatedField,
    ValidationBinding,
    ValidationFailure,
    ValueKind,
    algorithmic_producer_version,
    stable_hash,
)
from extractx.proposals import (
    LayeredProposalValidator,
    ProposalValidatorContractError,
)

# ---------------------------------------------------------------------------
# fixtures — keep local so each test's dependencies are legible
# ---------------------------------------------------------------------------


def _ref(source_id: str = "doc-1") -> SourceRef:
    return SourceRef(source_id=source_id, content_hash="sha256:abc")


def _sb_span(start: int, end: int, source_id: str = "doc-1") -> SourceSpan:
    """build a `source_bytes` span."""

    return SourceSpan(
        source_ref=_ref(source_id),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def _nt_span(start: int, end: int) -> SourceSpan:
    """build a `normalized_text` span."""

    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="normalized_text",
        byte_start=start,
        byte_end=end,
    )


def _sb_document_view(
    text: str = "the amount is 42.00 dollars",
) -> DocumentView:
    """build a linearizable (source_bytes) DocumentView with an identity
    anchor map covering the whole text."""

    n = len(text.encode("utf-8"))
    anchor_map = AnchorMap(entries=((0, _sb_span(0, n)),))
    return DocumentView(
        document_id="doc-1",
        normalized_text=text,
        anchor_map=anchor_map,
        source_ref=_ref(),
    )


def _nt_document_view(
    text: str = "the amount is 42.00 dollars",
) -> DocumentView:
    """build a paginated-visual (normalized_text) DocumentView. the
    anchor_map's entries carry `normalized_text` spans, which declares
    the subcontract per ADR-0006.
    """

    n = len(text.encode("utf-8"))
    anchor_map = AnchorMap(entries=((0, _nt_span(0, n)),))
    return DocumentView(
        document_id="doc-1",
        normalized_text=text,
        anchor_map=anchor_map,
        source_ref=_ref(),
    )


def _instance_key() -> InstanceGroupingKey:
    return InstanceGroupingKey(group_id="grp-1", ordinal=0, group_anchors=())


def _proposed(
    *,
    raw_value: str = "42.00",
    source_span: SourceSpan | None = None,
    evidence_spans: tuple[SourceSpan, ...] = (),
    normalized_hint: Any = None,
    tentative_instance_key: InstanceGroupingKey | None = None,
    field_id: str = "total",
) -> ProposedField:
    span = source_span if source_span is not None else _sb_span(14, 19)
    return ProposedField(
        field_id=field_id,
        tentative_instance_key=tentative_instance_key,
        raw_value=raw_value,
        evidence_text=raw_value,
        source_span=span,
        evidence_spans=evidence_spans,
        normalized_hint=normalized_hint,
        candidate_id_refs=("c-1",),
        strategy_id="regex:test",
        selector_producer_version="code:selector-v1",
    )


def _manual_field_spec(
    *,
    normalizer: Any = None,
    field_validators: tuple[Any, ...] = (),
    field_id: str = "total",
) -> FieldSpec:
    return FieldSpec(
        field_id=field_id,
        description="test field",
        value_kind=ValueKind.register("TEXT"),
        cardinality=Cardinality.ONE,
        python_type=str,
        validation_binding=ValidationBinding(
            normalizer=normalizer,
            field_validators=field_validators,
        ),
    )


def _bare_field_spec(*, field_id: str = "total") -> FieldSpec:
    """field spec with no `validation_binding` — used when `schema_cls`
    drives layer 2 (pydantic-backed path)."""

    return FieldSpec(
        field_id=field_id,
        description="test field",
        value_kind=ValueKind.register("TEXT"),
        cardinality=Cardinality.ONE,
        python_type=str,
        validation_binding=None,
    )


# ---------------------------------------------------------------------------
# purity
# ---------------------------------------------------------------------------


class TestPurity:
    def test_repeated_calls_yield_equal_output(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed()
        spec = _manual_field_spec()

        first = validator.validate(proposed, spec, doc)
        second = validator.validate(proposed, spec, doc)

        assert first == second


# ---------------------------------------------------------------------------
# layer 1 — normalized_text path
# ---------------------------------------------------------------------------


class TestLayer1NormalizedTextPath:
    def test_utf8_aligned_in_range_span_passes_layer1(self) -> None:
        validator = LayeredProposalValidator()
        doc = _nt_document_view("the amount is 42.00 dollars")
        # "42.00" starts at byte 14, ends at 19 — ASCII, UTF-8 aligned.
        proposed = _proposed(source_span=_nt_span(14, 19))
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        # layer 1 passed; layer 2 produces a ValidatedField.
        assert isinstance(result, ValidatedField)

    def test_utf8_misaligned_byte_start_emits_candidate_utf8_alignment(self) -> None:
        validator = LayeredProposalValidator()
        # include a multi-byte UTF-8 character so offsets can be
        # misaligned. "café" → 5 UTF-8 bytes ("c"=1, "a"=1, "f"=1,
        # "é"=2). byte 3 is the first byte of "é"; byte 4 is the
        # continuation byte.
        text = "café world"
        doc = _nt_document_view(text)
        # byte_start=4 is mid-"é" — UTF-8 misaligned.
        proposed = _proposed(source_span=_nt_span(4, 5))
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.category == "validation"
        assert result.code == "candidate.utf8_alignment"
        assert result.field_id == proposed.field_id

    def test_byte_end_exceeding_normalized_text_emits_utf8_alignment(self) -> None:
        validator = LayeredProposalValidator()
        text = "short"  # 5 bytes
        doc = _nt_document_view(text)
        # byte_end beyond the UTF-8 length → out of range → phase-1
        # folds this under `candidate.utf8_alignment` per the brief.
        proposed = _proposed(source_span=_nt_span(0, 1000))
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.utf8_alignment"

    def test_evidence_span_utf8_misalignment_also_fails(self) -> None:
        # the invariant: every `evidence_span[i]` is checked under the
        # same layer-1 rule.
        validator = LayeredProposalValidator()
        text = "café world"
        doc = _nt_document_view(text)
        proposed = _proposed(
            source_span=_nt_span(0, 1),  # valid
            evidence_spans=(_nt_span(4, 5),),  # misaligned
        )
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.utf8_alignment"


# ---------------------------------------------------------------------------
# layer 1 — source_bytes path
# ---------------------------------------------------------------------------


class TestLayer1SourceBytesPath:
    def test_invertible_span_passes_layer1(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view("the amount is 42.00 dollars")
        proposed = _proposed(source_span=_sb_span(14, 19))
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidatedField)

    def test_span_outside_anchor_map_emits_span_out_of_range(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view("short")  # 5 bytes of normalized text
        # span past the anchor map's image.
        proposed = _proposed(source_span=_sb_span(100, 200))
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.span_out_of_range"

    def test_span_source_ref_mismatch_emits_span_out_of_range(self) -> None:
        # inversion requires the span's source_ref to match the
        # anchor_map segment's source_ref. a foreign source_ref is
        # equivalent to "not covered" — `candidate.span_out_of_range`.
        validator = LayeredProposalValidator()
        doc = _sb_document_view("the amount is 42.00 dollars")
        foreign = SourceSpan(
            source_ref=SourceRef(source_id="other", content_hash="sha256:zzz"),
            text_anchor_space="source_bytes",
            byte_start=0,
            byte_end=3,
        )
        proposed = _proposed(source_span=foreign)
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.span_out_of_range"


# ---------------------------------------------------------------------------
# layer 1 — text_anchor_space mismatch with adapter subcontract
# ---------------------------------------------------------------------------


class TestLayer1TextAnchorSpaceMismatch:
    def test_normalized_text_span_on_source_bytes_adapter_fails(self) -> None:
        # adapter subcontract = source_bytes (the anchor_map entries
        # carry source_bytes spans). a proposed span with
        # text_anchor_space="normalized_text" crosses the subcontract.
        validator = LayeredProposalValidator()
        doc = _sb_document_view("text")
        proposed = _proposed(source_span=_nt_span(0, 4))
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.text_anchor_space_mismatch"

    def test_source_bytes_span_on_normalized_text_adapter_fails(self) -> None:
        # adapter subcontract = normalized_text (paginated-visual). a
        # source_bytes span crosses the subcontract.
        validator = LayeredProposalValidator()
        doc = _nt_document_view("text")
        proposed = _proposed(source_span=_sb_span(0, 4))
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.text_anchor_space_mismatch"


# ---------------------------------------------------------------------------
# layer 1 — structured_payload shape (normalized_hint JSON-safety)
# ---------------------------------------------------------------------------


class TestLayer1StructuredPayloadShape:
    def test_json_safe_mapping_passes(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(normalized_hint={"currency": "USD", "amount": "42.00"})
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidatedField)

    def test_json_safe_sequence_passes(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(normalized_hint=["a", "b", 1, 2.0, None])
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidatedField)

    def test_none_hint_passes(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(normalized_hint=None)
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidatedField)

    def test_live_pydantic_model_fails_structured_payload_shape(self) -> None:
        class _SomeLive(BaseModel):
            name: str

        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(normalized_hint=_SomeLive(name="x"))
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.structured_payload_shape"

    def test_bytes_hint_fails_structured_payload_shape(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(normalized_hint=b"\x00\x01")
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.structured_payload_shape"

    def test_custom_class_hint_fails_structured_payload_shape(self) -> None:
        class _Custom:
            def __init__(self) -> None:
                self.x = 1

        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(normalized_hint=_Custom())
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.structured_payload_shape"

    def test_mapping_with_non_string_key_fails(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(normalized_hint={1: "x"})
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.code == "candidate.structured_payload_shape"


# ---------------------------------------------------------------------------
# layer 1 is non-retryable — never emits ValidationFailure
# ---------------------------------------------------------------------------


class TestLayer1NonRetryable:
    @pytest.mark.parametrize(
        "build_doc,build_span,expected_code",
        [
            # utf8 misalignment
            (
                lambda: _nt_document_view("café world"),
                lambda: _nt_span(4, 5),
                "candidate.utf8_alignment",
            ),
            # source_bytes out of range
            (
                lambda: _sb_document_view("short"),
                lambda: _sb_span(100, 200),
                "candidate.span_out_of_range",
            ),
            # text_anchor_space mismatch
            (
                lambda: _sb_document_view("text"),
                lambda: _nt_span(0, 4),
                "candidate.text_anchor_space_mismatch",
            ),
        ],
    )
    def test_every_layer1_failure_is_negative_outcome_not_validation_failure(
        self,
        build_doc: Any,
        build_span: Any,
        expected_code: str,
    ) -> None:
        validator = LayeredProposalValidator()
        doc = build_doc()
        proposed = _proposed(source_span=build_span())
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert not isinstance(result, ValidationFailure)  # defense in depth
        assert result.code == expected_code


# ---------------------------------------------------------------------------
# layer 2 — pydantic path
# ---------------------------------------------------------------------------


class _IntSchema(BaseModel):
    """minimal pydantic schema used for layer-2 pydantic-path tests.

    `amount` is typed `int` so pydantic must coerce "42" → 42.
    `field_validator(..., mode='after')` runs on the coerced value;
    this is the canonical seam-F layer-2 shape (no raw-text parsing).
    """

    amount: int

    @field_validator("amount", mode="after")
    @classmethod
    def _must_be_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("amount must be non-negative")
        return v


class _RejectAllSchema(BaseModel):
    amount: int

    @field_validator("amount", mode="after")
    @classmethod
    def _always_reject(cls, v: int) -> int:
        raise ValueError(f"always reject (got {v})")


def _parse_item_count(value: object) -> object:
    if value == "20 items":
        return 20
    return value


class _BeforeValidatorSchema(BaseModel):
    amount: Annotated[int, BeforeValidator(_parse_item_count)]


class TestLayer2PydanticPath:
    def test_coercion_and_validator_success_emits_validated_field(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(raw_value="42", field_id="amount")
        spec = _bare_field_spec(field_id="amount")

        result = validator.validate(proposed, spec, doc, schema_cls=_IntSchema)

        assert isinstance(result, ValidatedField)
        assert result.normalized_value == 42
        # field_validation_version composition: code:{hash} shape.
        assert result.field_validation_version.startswith("code:")

    def test_annotated_before_validator_runs_before_type_coercion(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(raw_value="20 items", field_id="amount")
        spec = _bare_field_spec(field_id="amount")

        result = validator.validate(proposed, spec, doc, schema_cls=_BeforeValidatorSchema)

        assert isinstance(result, ValidatedField)
        assert result.normalized_value == 20

    def test_validator_failure_emits_validation_failure_not_exception(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(raw_value="-5", field_id="amount")
        spec = _bare_field_spec(field_id="amount")

        result = validator.validate(proposed, spec, doc, schema_cls=_IntSchema)

        assert isinstance(result, ValidationFailure)
        assert result.layer == "field"
        assert result.field_id == "amount"
        assert "non-negative" in result.reason

    def test_coercion_failure_emits_validation_failure(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        # "not-an-int" cannot coerce to int → pydantic ValidationError.
        proposed = _proposed(raw_value="not-an-int", field_id="amount")
        spec = _bare_field_spec(field_id="amount")

        result = validator.validate(proposed, spec, doc, schema_cls=_IntSchema)

        assert isinstance(result, ValidationFailure)
        assert result.layer == "field"

    def test_raising_field_validator_is_caught(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(raw_value="7", field_id="amount")
        spec = _bare_field_spec(field_id="amount")

        result = validator.validate(proposed, spec, doc, schema_cls=_RejectAllSchema)

        assert isinstance(result, ValidationFailure)
        assert "always reject" in result.reason


# ---------------------------------------------------------------------------
# layer 2 — manual path (ValidationBinding.normalizer + FieldValidators)
# ---------------------------------------------------------------------------


class _OrderRecorder:
    """test-only recorder to assert field-validator invocation order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def make_validator(self, tag: str, *, reject: bool = False) -> Any:
        def _validator(value: Any) -> Any:
            self.calls.append(tag)
            if reject:
                raise ValueError(f"rejected at {tag}")
            return value

        _validator.__name__ = f"_validator_{tag}"
        return _validator


class _CountingNormalizer:
    """counting normalizer — used for the single-normalization-site proof."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, raw: str) -> str:
        self.calls += 1
        return raw.strip().upper()


class TestLayer2ManualPath:
    def test_normalizer_runs_and_value_flows_into_validators(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        recorder = _OrderRecorder()
        normalizer = _CountingNormalizer()
        v1 = recorder.make_validator("v1")
        v2 = recorder.make_validator("v2")
        spec = _manual_field_spec(normalizer=normalizer, field_validators=(v1, v2))
        proposed = _proposed(raw_value=" hello ")

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidatedField)
        assert result.normalized_value == "HELLO"
        assert normalizer.calls == 1
        assert recorder.calls == ["v1", "v2"]

    def test_validators_run_in_declared_order(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        recorder = _OrderRecorder()
        spec = _manual_field_spec(
            normalizer=None,
            field_validators=(
                recorder.make_validator("alpha"),
                recorder.make_validator("beta"),
                recorder.make_validator("gamma"),
            ),
        )
        proposed = _proposed()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidatedField)
        assert recorder.calls == ["alpha", "beta", "gamma"]

    def test_rejecting_validator_emits_validation_failure(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        recorder = _OrderRecorder()
        spec = _manual_field_spec(
            normalizer=None,
            field_validators=(
                recorder.make_validator("v1"),
                recorder.make_validator("v2", reject=True),
                recorder.make_validator("v3"),
            ),
        )
        proposed = _proposed()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidationFailure)
        assert result.layer == "field"
        assert "rejected at v2" in result.reason
        # subsequent validators are not invoked after a rejection.
        assert recorder.calls == ["v1", "v2"]

    def test_raising_normalizer_emits_validation_failure(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()

        def _normalizer(_raw: str) -> str:
            raise ValueError("bad input")

        spec = _manual_field_spec(normalizer=_normalizer)
        proposed = _proposed()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidationFailure)
        assert "bad input" in result.reason


# ---------------------------------------------------------------------------
# single normalization site — invariant for `Dual Normalization`
# ---------------------------------------------------------------------------


class TestSingleNormalizationSite:
    def test_normalizer_is_called_exactly_once_regardless_of_validator_count(
        self,
    ) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        normalizer = _CountingNormalizer()
        recorder = _OrderRecorder()
        spec = _manual_field_spec(
            normalizer=normalizer,
            field_validators=(
                recorder.make_validator("v1"),
                recorder.make_validator("v2"),
                recorder.make_validator("v3"),
                recorder.make_validator("v4"),
            ),
        )
        proposed = _proposed(raw_value="abc")

        validator.validate(proposed, spec, doc)

        # regardless of how many FieldValidators fire, the normalizer
        # runs exactly once — this is the single normalization site
        # invariant.
        assert normalizer.calls == 1

    def test_normalizer_is_not_called_again_across_repeated_validate_calls(
        self,
    ) -> None:
        # a second `validate` call must re-run the normalizer exactly
        # once on the second call (not zero, not two); two calls → two
        # total invocations.
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        normalizer = _CountingNormalizer()
        spec = _manual_field_spec(normalizer=normalizer)
        proposed = _proposed(raw_value="abc")

        validator.validate(proposed, spec, doc)
        validator.validate(proposed, spec, doc)

        assert normalizer.calls == 2


# ---------------------------------------------------------------------------
# field_validation_version composition and determinism
# ---------------------------------------------------------------------------


class TestFieldValidationVersion:
    def test_same_inputs_yield_same_version(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()

        def _norm(s: str) -> str:
            return s.strip()

        spec = _manual_field_spec(normalizer=_norm)
        proposed = _proposed(raw_value=" x ")

        first = validator.validate(proposed, spec, doc)
        second = validator.validate(proposed, spec, doc)

        assert isinstance(first, ValidatedField)
        assert isinstance(second, ValidatedField)
        assert first.field_validation_version == second.field_validation_version

    def test_changing_normalizer_qualname_changes_version(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()

        def _norm_a(s: str) -> str:
            return s.strip()

        def _norm_b(s: str) -> str:
            return s.strip()

        spec_a = _manual_field_spec(normalizer=_norm_a)
        spec_b = _manual_field_spec(normalizer=_norm_b)
        proposed = _proposed(raw_value="x")

        result_a = validator.validate(proposed, spec_a, doc)
        result_b = validator.validate(proposed, spec_b, doc)

        assert isinstance(result_a, ValidatedField)
        assert isinstance(result_b, ValidatedField)
        assert result_a.field_validation_version != result_b.field_validation_version

    def test_changing_field_validator_qualname_changes_version(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()

        def _v_alpha(v: Any) -> Any:
            return v

        def _v_beta(v: Any) -> Any:
            return v

        spec_a = _manual_field_spec(field_validators=(_v_alpha,))
        spec_b = _manual_field_spec(field_validators=(_v_beta,))
        proposed = _proposed()

        result_a = validator.validate(proposed, spec_a, doc)
        result_b = validator.validate(proposed, spec_b, doc)

        assert isinstance(result_a, ValidatedField)
        assert isinstance(result_b, ValidatedField)
        assert result_a.field_validation_version != result_b.field_validation_version

    def test_version_shape_is_algorithmic_producer_version(self) -> None:
        # brief: use `algorithmic_producer_version(stable_hash(tuple))`
        # shape. so `field_validation_version.startswith("code:")`
        # (§4 algorithmic-producer shape).
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        spec = _manual_field_spec()
        proposed = _proposed()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidatedField)
        assert result.field_validation_version.startswith("code:")

    def test_exact_tuple_hashed_matches_documented_composition(self) -> None:
        # the brief fixes the hashed tuple shape:
        # (spec_version, field_id, pydantic_backed_bool,
        #  normalizer_qualname_or_none, tuple(field_validator_qualnames))
        # we mirror the composition here and assert equality. phase-1
        # uses the empty string for spec_version.
        validator = LayeredProposalValidator()
        doc = _sb_document_view()

        def _norm(s: str) -> str:
            return s.strip()

        spec = _manual_field_spec(normalizer=_norm)
        proposed = _proposed()
        result = validator.validate(proposed, spec, doc)
        assert isinstance(result, ValidatedField)

        expected_tuple: tuple[Any, ...] = (
            "",
            spec.field_id,
            False,  # pydantic_backed_bool = False (manual path)
            f"{_norm.__module__}.{_norm.__qualname__}",
            [],  # no field validators
        )
        expected = algorithmic_producer_version(stable_hash(expected_tuple))
        assert result.field_validation_version == expected


# ---------------------------------------------------------------------------
# lifecycle invariants: identity preserved, ValidatedField frozen
# ---------------------------------------------------------------------------


class TestLifecycleInvariants:
    def test_validated_field_proposed_is_same_object(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        spec = _manual_field_spec()
        proposed = _proposed()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, ValidatedField)
        # identity preserved — the brief: "same object as the input
        # ProposedField (identity preserved; no clone)".
        assert result.proposed is proposed

    def test_validated_field_is_frozen(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        spec = _manual_field_spec()
        proposed = _proposed()

        result = validator.validate(proposed, spec, doc)
        assert isinstance(result, ValidatedField)

        with pytest.raises(ValidationError):
            result.normalized_value = "mutated"  # pyright: ignore[reportAttributeAccessIssue]


# ---------------------------------------------------------------------------
# layer 3 absence — no model_validator invocation at phase 1
# ---------------------------------------------------------------------------


class _WithBlockingModelValidator(BaseModel):
    """schema class with a model_validator that raises unconditionally.

    if phase-1 `validate(...)` ever invokes `model_validator`s, this
    raise surfaces. we attach `mode="after"` so pydantic only fires it
    post-coercion on a fully-constructed instance — if the isolated-
    field fallback path is used and constructs a singleton dict, the
    model_validator would still fire if pydantic runs it.
    """

    amount: int

    @model_validator(mode="after")
    def _always_raise(self) -> _WithBlockingModelValidator:
        raise AssertionError(
            "model_validator should not run during seam-F phase-1 validate(...); "
            "layer 3 (model_validator) is post-G.resolver per ADR-0003",
        )


class TestLayer3Absence:
    def test_model_validator_is_never_called_during_validate(self) -> None:
        # the contract: seam F phase 1 runs layer 1 + layer 2 only.
        # pydantic `model_validator` is layer 3 and must not fire.
        #
        # NOTE: the brief asks us to prove that attaching a raising
        # `model_validator` to the schema class does not cause it to be
        # called. with the current implementation, pydantic's
        # `model_validate(dict)` in the single-field path would invoke
        # `model_validator(mode="after")`. to hold the invariant, the
        # phase-1 validator uses the isolated-field fallback when
        # constructing a full instance would require invoking
        # model_validator behavior — in practice by detecting the
        # attached model_validator and routing through the isolated-
        # field path.
        #
        # see implementation note: `_build_sibling_defaults` returning
        # `ok=False` is one route; the other is the isolated-field
        # fallback. this test is the proof that whichever path runs,
        # model_validator never fires.
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(raw_value="7", field_id="amount")
        spec = _bare_field_spec(field_id="amount")

        result = validator.validate(
            proposed,
            spec,
            doc,
            schema_cls=_WithBlockingModelValidator,
        )

        # the raising model_validator would have surfaced as
        # AssertionError propagating through pydantic ValidationError;
        # success here means it did not run.
        assert isinstance(result, ValidatedField)
        assert result.normalized_value == 7


# ---------------------------------------------------------------------------
# Pydantic-as-Extractor rejection stays at spec load (seam B), not at layer 2
# ---------------------------------------------------------------------------


class _PostCoercionOnlySchema(BaseModel):
    """schema whose validator runs on the coerced value only.

    not a `Pydantic-as-Extractor` violator: `mode="after"` and int
    annotation. seam F phase 1 must accept it at layer 2 without
    inventing a re-check.
    """

    amount: int

    @field_validator("amount", mode="after")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("non-negative required")
        return v


class TestPydanticAsExtractorBoundary:
    def test_layer2_does_not_reinspect_pydantic_as_extractor_rule(self) -> None:
        # the brief's invariant: "seam F assumes spec-load succeeded and
        # does not re-check the rule". a FieldSpec constructed directly
        # (bypassing `from_pydantic`) still succeeds at layer 2 if the
        # raw-value coercion works. this is the proof that the check
        # does NOT live in layer 2.
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        proposed = _proposed(raw_value="42", field_id="amount")
        spec = _bare_field_spec(field_id="amount")

        result = validator.validate(
            proposed,
            spec,
            doc,
            schema_cls=_PostCoercionOnlySchema,
        )

        assert isinstance(result, ValidatedField)
        assert result.normalized_value == 42


# ---------------------------------------------------------------------------
# runtime-reachable malformed FieldSpec → ProposalValidatorContractError
# ---------------------------------------------------------------------------


class TestContractErrorOnMalformedFieldSpec:
    def test_manual_path_with_no_validation_binding_raises_loudly(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        # schema_cls=None (manual path) + validation_binding=None is a
        # seam-B defect. phase 1 fails loudly with a local ValueError
        # subtype, not a typed negative.
        spec = _bare_field_spec()
        proposed = _proposed()

        with pytest.raises(ProposalValidatorContractError):
            validator.validate(proposed, spec, doc)

    def test_error_is_value_error_subtype(self) -> None:
        # mirrors the seam-E SelectionAdapterContractError pattern:
        # local ValueError subtype, not a widened exception surface.
        assert issubclass(ProposalValidatorContractError, ValueError)

    def test_schema_cls_without_matching_field_raises_loudly(self) -> None:
        class _WrongSchema(BaseModel):
            other: int

        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        spec = _bare_field_spec(field_id="amount")
        proposed = _proposed(field_id="amount", raw_value="42")

        with pytest.raises(ProposalValidatorContractError):
            validator.validate(proposed, spec, doc, schema_cls=_WrongSchema)


# ---------------------------------------------------------------------------
# ValidationFailure carries tentative grouping key through
# ---------------------------------------------------------------------------


class TestValidationFailureCarriesGroupingKey:
    def test_validation_failure_maps_tentative_instance_key(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view()
        hint = _instance_key()
        proposed = _proposed(
            raw_value="-1",
            tentative_instance_key=hint,
            field_id="amount",
        )
        spec = _bare_field_spec(field_id="amount")

        result = validator.validate(proposed, spec, doc, schema_cls=_IntSchema)

        assert isinstance(result, ValidationFailure)
        assert result.instance_key == hint

    def test_negative_outcome_maps_tentative_instance_key(self) -> None:
        validator = LayeredProposalValidator()
        doc = _sb_document_view("short")
        hint = _instance_key()
        proposed = _proposed(
            source_span=_sb_span(100, 200),
            tentative_instance_key=hint,
        )
        spec = _manual_field_spec()

        result = validator.validate(proposed, spec, doc)

        assert isinstance(result, NegativeOutcome)
        assert result.instance_key == hint
