"""spaCy-backed NER `CandidateStrategy` for seam C.

The strategy is explicit and opt-in. It reads only `StrategyBinding.params`,
runs on `DocumentView.normalized_text`, emits candidates from `doc.ents`, and
translates spaCy character offsets through the document anchor map.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from extractx.core.anchors import SourceSpan, anchor_lookup
from extractx.core.exceptions import InfrastructureError, SpecError
from extractx.core.objects import (
    Candidate,
    CandidateSet,
    DocumentView,
    FieldSpec,
    InstanceHint,
    StrategyBinding,
)
from extractx.core.versions import algorithmic_producer_version, stable_hash

from ..candidate_set import (
    build_candidate_set,
    candidate_id_for,
    validate_source_span_against_view,
)
from ..context import (
    DEFAULT_CONTEXT_WINDOW_BYTES,
    ByteWindowCandidateContextBuilder,
    CandidateContextBuilder,
    normalized_match_span,
)
from ..scalars import normalized_decimal_hint
from ._binding import binding_for_strategy

__all__ = [
    "NER_STRATEGY_ID_PREFIX",
    "NerCandidateStrategy",
    "NerEntityRulerConfig",
    "NerStrategyParams",
    "algorithmic_code_hash",
]


NER_STRATEGY_ID_PREFIX = "ner"
_NORMALIZED_HINT_LABELS = frozenset({"MONEY"})
DEFAULT_NER_MAX_CHARS_PER_CHUNK = 250_000
DEFAULT_NER_CHUNK_OVERLAP_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class _TextChunk:
    start_char: int
    text: str


class NerEntityRulerConfig(BaseModel):
    """typed, JSON-safe EntityRuler configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    patterns: tuple[Mapping[str, Any], ...]
    overwrite_ents: bool = False

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("name must be non-empty")
        return value

    @field_validator("patterns")
    @classmethod
    def _patterns_non_empty_and_json_safe(
        cls,
        value: tuple[Mapping[str, Any], ...],
    ) -> tuple[Mapping[str, Any], ...]:
        if not value:
            raise ValueError("patterns must be non-empty")
        for pattern in value:
            if not _is_json_safe(pattern):
                raise ValueError("patterns must be JSON-safe")
        return value


class NerStrategyParams(BaseModel):
    """validated `StrategyBinding.params` for `NerCandidateStrategy`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = "en"
    entity_rulers: tuple[NerEntityRulerConfig, ...] = ()
    filter_components: tuple[str, ...] = ()
    entity_filter: tuple[str, ...] | None = None
    context_window_bytes: int = DEFAULT_CONTEXT_WINDOW_BYTES
    max_chars_per_chunk: int = DEFAULT_NER_MAX_CHARS_PER_CHUNK
    chunk_overlap_chars: int = DEFAULT_NER_CHUNK_OVERLAP_CHARS
    oversize_policy: Literal["chunk", "fail"] = "chunk"

    @field_validator("model_id")
    @classmethod
    def _model_id_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("model_id must be non-empty")
        return value

    @field_validator("filter_components")
    @classmethod
    def _filter_components_non_empty(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not component for component in value):
            raise ValueError("filter_components entries must be non-empty")
        return value

    @field_validator("entity_filter")
    @classmethod
    def _entity_filter_non_empty(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is not None and (not value or any(not label for label in value)):
            raise ValueError("entity_filter entries must be non-empty")
        return value

    @field_validator("context_window_bytes")
    @classmethod
    def _context_window_non_negative(cls, value: int) -> int:
        if isinstance(value, bool) or value < 0:
            raise ValueError("context_window_bytes must be a non-negative int")
        return value

    @field_validator("max_chars_per_chunk")
    @classmethod
    def _max_chars_per_chunk_positive(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("max_chars_per_chunk must be a positive int")
        return value

    @field_validator("chunk_overlap_chars")
    @classmethod
    def _chunk_overlap_non_negative(cls, value: int) -> int:
        if isinstance(value, bool) or value < 0:
            raise ValueError("chunk_overlap_chars must be a non-negative int")
        return value

    @model_validator(mode="after")
    def _chunk_overlap_smaller_than_chunk(self) -> NerStrategyParams:
        if self.chunk_overlap_chars >= self.max_chars_per_chunk:
            raise ValueError("chunk_overlap_chars must be smaller than max_chars_per_chunk")
        return self

    @classmethod
    def from_mapping(cls, params: Mapping[str, Any]) -> NerStrategyParams:
        try:
            return cls.model_validate(dict(params))
        except ValidationError as exc:
            raise SpecError(f"NerStrategyParams: invalid params: {exc}") from exc

    def canonical_hash_payload(self) -> Mapping[str, Any]:
        return self.model_dump(mode="json")


class NerCandidateStrategy:
    """explicit opt-in spaCy NER candidate source."""

    def __init__(self, *, context_builder: CandidateContextBuilder | None = None) -> None:
        self._context_builder = context_builder

    def generate(
        self,
        field_spec: FieldSpec,
        document_view: DocumentView,
        instance_hint: InstanceHint | None = None,
    ) -> CandidateSet:
        binding = binding_for_strategy(
            field_spec,
            NerCandidateStrategy,
            "NerCandidateStrategy",
        )
        self._assert_binding_targets_self(binding, field_spec)
        params = NerStrategyParams.from_mapping(binding.params)
        strategy_id = self._strategy_id_for(params)
        context_builder = self._context_builder or ByteWindowCandidateContextBuilder(
            window_bytes=params.context_window_bytes,
        )

        nlp = _build_nlp(params)
        if params.oversize_policy == "fail" and len(document_view.normalized_text) > nlp.max_length:
            raise InfrastructureError(
                "ner.document_too_long: "
                f"text length {len(document_view.normalized_text)} exceeds "
                f"spaCy max_length {nlp.max_length}",
            )
        if params.oversize_policy == "chunk":
            nlp.max_length = max(nlp.max_length, params.max_chars_per_chunk)

        allowed_labels = None if params.entity_filter is None else set(params.entity_filter)
        normalized_bytes = document_view.normalized_text.encode("utf-8")

        candidates_by_id: dict[str, Candidate] = {}
        for chunk in _iter_text_chunks(
            document_view.normalized_text,
            max_chars_per_chunk=params.max_chars_per_chunk,
            chunk_overlap_chars=params.chunk_overlap_chars,
        ):
            try:
                doc = nlp(chunk.text)
            except ValueError as exc:
                if "[E088]" in str(exc):
                    raise InfrastructureError(
                        "ner.document_too_long: "
                        f"text chunk length {len(chunk.text)} exceeds "
                        f"spaCy max_length {nlp.max_length}",
                    ) from exc
                raise

            for ent in doc.ents:
                if allowed_labels is not None and ent.label_ not in allowed_labels:
                    continue
                global_start_char = chunk.start_char + ent.start_char
                global_end_char = chunk.start_char + ent.end_char
                start_byte = _char_offset_to_utf8_byte(
                    document_view.normalized_text,
                    global_start_char,
                )
                end_byte = _char_offset_to_utf8_byte(
                    document_view.normalized_text,
                    global_end_char,
                )
                if start_byte == end_byte:
                    continue
                span = _normalized_range_to_source_span(
                    document_view=document_view,
                    start_byte=start_byte,
                    end_byte=end_byte,
                )
                validate_source_span_against_view(span, document_view)
                candidate_id = candidate_id_for(
                    strategy_id=strategy_id,
                    source_span=span,
                    normalized_structural_payload={"entity_type": ent.label_},
                )
                candidates_by_id.setdefault(
                    candidate_id,
                    Candidate(
                        candidate_id=candidate_id,
                        text=ent.text,
                        source_kind="text",
                        source_id=strategy_id,
                        source_span=span,
                        context=context_builder.build(
                            normalized_bytes=normalized_bytes,
                            match_start=start_byte,
                            match_end=end_byte,
                        ),
                        context_span=context_builder.span(
                            normalized_bytes=normalized_bytes,
                            match_start=start_byte,
                            match_end=end_byte,
                            source_ref=document_view.source_ref,
                        )
                        if isinstance(context_builder, ByteWindowCandidateContextBuilder)
                        else None,
                        normalized_span=normalized_match_span(
                            source_ref=document_view.source_ref,
                            match_start=start_byte,
                            match_end=end_byte,
                        ),
                        entity_type=ent.label_,
                        normalized_hint=_normalized_hint_for_entity(
                            label=ent.label_,
                            text=ent.text,
                        ),
                        structured_payload=None,
                    ),
                )

        return build_candidate_set(
            field_id=field_spec.field_id,
            document_id=document_view.document_id,
            candidates=tuple(candidates_by_id.values()),
            strategy_id=strategy_id,
            instance_hint=instance_hint,
        )

    def _assert_binding_targets_self(
        self,
        binding: StrategyBinding,
        field_spec: FieldSpec,
    ) -> None:
        if binding.kind != "candidate":
            raise SpecError(
                "NerCandidateStrategy: StrategyBinding.kind must be "
                f"'candidate', got {binding.kind!r} for field {field_spec.field_id!r}",
            )
        cls = binding.cls
        if cls is not NerCandidateStrategy and not issubclass(cls, NerCandidateStrategy):
            raise SpecError(
                "NerCandidateStrategy: StrategyBinding.cls names "
                f"{cls!r}, not NerCandidateStrategy, for field {field_spec.field_id!r}",
            )

    def _strategy_id_for(self, params: NerStrategyParams) -> str:
        digest = stable_hash(
            {
                "cls": f"{NerCandidateStrategy.__module__}.{NerCandidateStrategy.__qualname__}",
                "params": params.canonical_hash_payload(),
            },
        )
        return f"{NER_STRATEGY_ID_PREFIX}:{digest}"


def _build_nlp(params: NerStrategyParams) -> Any:
    try:
        import spacy
    except ImportError as exc:
        raise InfrastructureError(
            "ner.missing_spacy: spaCy is not installed; install extractx[spacy]",
        ) from exc

    if params.model_id == "en":
        nlp = spacy.blank("en")
    else:
        try:
            nlp = spacy.load(params.model_id)
        except Exception as exc:
            raise InfrastructureError(
                f"ner.model_unavailable: failed to load spaCy model {params.model_id!r}: {exc}",
            ) from exc

    for ruler_config in params.entity_rulers:
        if ruler_config.name in nlp.pipe_names:
            raise InfrastructureError(
                f"ner.entity_ruler_conflict: component {ruler_config.name!r} already exists",
            )
        ruler = cast(
            "Any",
            nlp.add_pipe(
                "entity_ruler",
                name=ruler_config.name,
                config={"overwrite_ents": ruler_config.overwrite_ents},
            ),
        )
        ruler.add_patterns([dict(pattern) for pattern in ruler_config.patterns])

    for component in params.filter_components:
        if component not in nlp.pipe_names:
            raise InfrastructureError(
                f"ner.missing_component: filter component {component!r} is not registered",
            )
    return nlp


def _char_offset_to_utf8_byte(text: str, char_offset: int) -> int:
    return len(text[:char_offset].encode("utf-8"))


def _iter_text_chunks(
    text: str,
    *,
    max_chars_per_chunk: int,
    chunk_overlap_chars: int,
) -> tuple[_TextChunk, ...]:
    if len(text) <= max_chars_per_chunk:
        return (_TextChunk(start_char=0, text=text),)

    chunks: list[_TextChunk] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars_per_chunk)
        chunks.append(_TextChunk(start_char=start, text=text[start:end]))
        if end == len(text):
            break
        next_start = max(0, end - chunk_overlap_chars)
        if next_start <= start:
            next_start = end
        start = next_start
    return tuple(chunks)


def _normalized_range_to_source_span(
    *,
    document_view: DocumentView,
    start_byte: int,
    end_byte: int,
) -> SourceSpan:
    start = anchor_lookup(document_view.anchor_map, start_byte, document_view.normalized_text)
    end = anchor_lookup(document_view.anchor_map, end_byte, document_view.normalized_text)
    if start.source_ref != end.source_ref or start.text_anchor_space != end.text_anchor_space:
        return SourceSpan(
            source_ref=document_view.source_ref,
            text_anchor_space="normalized_text",
            byte_start=start_byte,
            byte_end=end_byte,
        )
    if start.text_anchor_space == "source_bytes":
        return SourceSpan(
            source_ref=start.source_ref,
            text_anchor_space="source_bytes",
            byte_start=start.byte_start,
            byte_end=end.byte_start,
            page_ref=start.page_ref,
            bounding_region=start.bounding_region,
        )
    return SourceSpan(
        source_ref=document_view.source_ref,
        text_anchor_space="normalized_text",
        byte_start=start_byte,
        byte_end=end_byte,
        page_ref=start.page_ref,
        bounding_region=start.bounding_region,
    )


def _normalized_hint_for_entity(*, label: str, text: str) -> str | None:
    if label not in _NORMALIZED_HINT_LABELS:
        return None
    return normalized_decimal_hint(text)


def _is_json_safe(value: Any) -> bool:
    if value is None or isinstance(value, bool | int | float | str):
        return True
    if isinstance(value, Mapping):
        mapping_value = cast("Mapping[Any, Any]", value)
        return all(isinstance(key, str) and _is_json_safe(v) for key, v in mapping_value.items())
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        sequence_value = cast("Sequence[Any]", value)
        return all(_is_json_safe(v) for v in sequence_value)
    return False


def algorithmic_code_hash() -> str:
    digest = stable_hash(
        f"{NerCandidateStrategy.__module__}.{NerCandidateStrategy.__qualname__}",
    )
    return algorithmic_producer_version(digest)
