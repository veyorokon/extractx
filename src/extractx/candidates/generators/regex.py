"""regex-based `CandidateStrategy` for seam C phase 1.

see docs/architecture.md §7 seam C and
docs/tasks/seam-c-deterministic-candidate-generation.md.

this is the first landed `CandidateStrategy`. it is **explicit and opt-in**:
it runs only when a `FieldSpec.strategy_bindings` entry targets
`RegexCandidateStrategy`
(or a subclass) and its pattern comes from `StrategyBinding.params`. it
does not infer patterns from `FieldSpec.description`, `ValueKind`, or any
hidden default. there is no pattern library.

subcontract scope: phase 1 consumes `DocumentView`s whose spans carry
`text_anchor_space="source_bytes"` — the linearizable subcontract of seam
A (ADR-0006). all emitted spans also carry `text_anchor_space="source_bytes"`.
paginated-visual `DocumentView`s and `text_anchor_space="normalized_text"`
spans are out of scope here; adding them is a separate thread.

regex matches run against `DocumentView.normalized_text.encode("utf-8")`,
so match offsets are UTF-8 byte offsets into the same coordinate system as
`anchor_map`'s domain. each match is translated back to source-byte
coordinates by walking `anchor_map`'s segments and taking the source image
of the overlapping slice of each segment. when the source-byte coverage is
contiguous (the common case — the match lies inside one identity segment,
or across adjacent identity segments), a single `source_span` is emitted.
when it is not contiguous (e.g. a match crosses an HTML entity whose 5
source bytes collapse to 1 normalized byte, then resumes in a later
identity segment with a gap in source bytes), the first source slice
becomes the `source_span` and the remaining slices become `evidence_spans`
— preserving evidential distinctness without smuggling in fuzzy
reconstruction logic.

strategy discipline:
- no dedup by normalized value, ever. two matches with the same text but
  distinct spans both remain — distinct `candidate_id` by construction.
- empty match set → empty `CandidateSet`, not an exception. `NO_CANDIDATES`
  is a seam-D outcome.
- malformed / missing params fail loudly at the earliest honest seam: for
  phase 1, that's this strategy. we do not move validation into seam B
  because seam B currently has no per-strategy param schema registry, and
  adding one would widen scope.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from extractx.core.anchors import (
    AnchorMap,
    SourceRef,
    SourceSpan,
)
from extractx.core.exceptions import SpecError
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
from ._binding import binding_for_strategy

__all__ = [
    "REGEX_STRATEGY_ID_PREFIX",
    "RegexCandidateStrategy",
    "RegexStrategyParams",
]


REGEX_STRATEGY_ID_PREFIX = "regex"
"""human-readable prefix for the regex strategy's `strategy_id`.

the full `strategy_id` is `"{prefix}:{stable_hash(params)}"` so that two
equal `StrategyBinding.params` produce the same `strategy_id` and two
different ones produce different ids. this keeps `candidate_id` stable
for identical inputs without requiring users to name every binding.
"""


@dataclass(frozen=True, slots=True)
class RegexStrategyParams:
    """validated phase-1 `StrategyBinding.params` for the regex strategy.

    the intentionally narrow v1 shape:

    - `pattern` (str, required): a python `re` pattern. users declare it
      themselves; the strategy does not infer it.
    - `flags` (int, optional, default 0): the integer flags the user wants
      to pass to `re.compile`. only int-valued flags are accepted so that
      the content hash is stable across python versions (enum repr can
      change). typical use: `int(re.IGNORECASE)`.
    - `group` (int | str | None, optional, default None): when set, the
      named or numbered capture group whose span becomes `source_span`;
      the full match becomes `evidence_spans[0]`. when None, the full
      match is the `source_span` and there are no evidence spans.
    - `context_window_bytes` (int, optional, default 160): bounded
      normalized-text context on each side of the match when using the
      default byte-window context builder.
    - `entity_type` (str | None, optional, default None): semantic label
      attached to emitted candidates. when omitted, the field's
      `ValueKind.name` is used so field-level filters can refine regex
      output without strategy-specific policy.

    other params are forbidden — extra keys in `StrategyBinding.params`
    are rejected with `SpecError` so a typo is visible rather than silent.
    """

    pattern: str
    flags: int = 0
    group: int | str | None = None
    context_window_bytes: int = DEFAULT_CONTEXT_WINDOW_BYTES
    entity_type: str | None = None

    @classmethod
    def from_mapping(cls, params: Mapping[str, Any]) -> RegexStrategyParams:
        """validate a raw params mapping and return a typed instance.

        raises `SpecError` on missing required fields, wrong types, or
        unknown keys. we raise `SpecError` (not `ValueError`) because the
        earliest honest seam to surface these is spec construction — even
        though the validation lives in this file today, it governs
        declarative binding content, which is a spec-shape concern.
        """

        allowed = {"pattern", "flags", "group", "context_window_bytes", "entity_type"}
        unknown = set(params.keys()) - allowed
        if unknown:
            raise SpecError(
                "RegexStrategyParams: unknown param keys "
                f"{sorted(unknown)!r}; allowed keys are {sorted(allowed)!r}",
            )
        if "pattern" not in params:
            raise SpecError(
                "RegexStrategyParams: required param 'pattern' is missing",
            )
        pattern = params["pattern"]
        if not isinstance(pattern, str):
            raise SpecError(
                f"RegexStrategyParams: 'pattern' must be a str, got {type(pattern).__name__}",
            )
        if not pattern:
            raise SpecError("RegexStrategyParams: 'pattern' must be non-empty")
        flags_raw = params.get("flags", 0)
        # bool is a subclass of int; we reject it explicitly to avoid
        # `True`/`False` silently becoming flag values.
        if isinstance(flags_raw, bool) or not isinstance(flags_raw, int):
            raise SpecError(
                f"RegexStrategyParams: 'flags' must be an int, got {type(flags_raw).__name__}",
            )
        group_raw = params.get("group", None)
        if group_raw is not None and not isinstance(group_raw, int | str):
            raise SpecError(
                "RegexStrategyParams: 'group' must be int | str | None, "
                f"got {type(group_raw).__name__}",
            )
        if isinstance(group_raw, bool):
            # `bool` is int; reject for the same reason as flags above.
            raise SpecError(
                "RegexStrategyParams: 'group' must be int | str | None, got bool",
            )
        context_window_bytes_raw = params.get(
            "context_window_bytes",
            DEFAULT_CONTEXT_WINDOW_BYTES,
        )
        if isinstance(context_window_bytes_raw, bool) or not isinstance(
            context_window_bytes_raw,
            int,
        ):
            raise SpecError(
                "RegexStrategyParams: 'context_window_bytes' must be a non-negative int, "
                f"got {type(context_window_bytes_raw).__name__}",
            )
        if context_window_bytes_raw < 0:
            raise SpecError(
                "RegexStrategyParams: 'context_window_bytes' must be a non-negative int",
            )
        entity_type_raw = params.get("entity_type", None)
        if entity_type_raw is not None and not isinstance(entity_type_raw, str):
            raise SpecError(
                "RegexStrategyParams: 'entity_type' must be str | None, "
                f"got {type(entity_type_raw).__name__}",
            )
        if isinstance(entity_type_raw, str) and not entity_type_raw:
            raise SpecError("RegexStrategyParams: 'entity_type' must be non-empty")
        # compile eagerly so malformed patterns fail here rather than on
        # first document. the compiled regex is discarded; the strategy
        # recompiles lazily on first `generate` to keep this class cheap
        # and hashable. we compile against bytes because the strategy
        # matches on UTF-8 bytes (ADR-0006 coordinate discipline).
        try:
            re.compile(pattern.encode("utf-8"), flags_raw)
        except re.error as exc:
            raise SpecError(
                f"RegexStrategyParams: 'pattern' failed to compile: {exc}",
            ) from exc
        return cls(
            pattern=pattern,
            flags=flags_raw,
            group=group_raw,
            context_window_bytes=context_window_bytes_raw,
            entity_type=entity_type_raw,
        )

    def canonical_hash_payload(self) -> Any:
        """return a json-stable payload used in the strategy id hash.

        kept separate from `__repr__` / `__eq__` so the payload shape is
        the single load-bearing definition used by `stable_hash`.
        """

        return {
            "pattern": self.pattern,
            "flags": int(self.flags),
            "group": self.group,
            "context_window_bytes": self.context_window_bytes,
            "entity_type": self.entity_type,
        }


@dataclass(frozen=True, slots=True)
class _SegmentView:
    """snapshot of one `AnchorMap` segment pre-computed for inversion.

    carries both the normalized-byte range `[norm_start, norm_end)` the
    segment covers and the source-bytes image `[src_start, src_end)`. an
    **identity** segment has `norm_end - norm_start == src_end - src_start`;
    a non-identity segment (e.g. html entity `&amp;`) does not.
    """

    norm_start: int
    norm_end: int
    src_start: int
    src_end: int
    source_ref: SourceRef

    @property
    def is_identity(self) -> bool:
        return (self.norm_end - self.norm_start) == (self.src_end - self.src_start)


def _segment_views(anchor_map: AnchorMap, normalized_byte_length: int) -> list[_SegmentView]:
    """project `anchor_map.entries` into pre-computed `_SegmentView`s.

    segments are ordered by `norm_start`; the final segment runs to the
    normalized-text byte length. every entry must already be a
    `source_bytes` span — phase 1 only consumes linearizable adapters.
    """

    views: list[_SegmentView] = []
    entries = anchor_map.entries
    n = len(entries)
    for i in range(n):
        norm_start, span = entries[i]
        if span.text_anchor_space != "source_bytes":
            raise ValueError(
                "regex strategy requires linearizable DocumentView "
                "(text_anchor_space='source_bytes'); got segment with "
                f"text_anchor_space={span.text_anchor_space!r}",
            )
        norm_end = entries[i + 1][0] if i + 1 < n else normalized_byte_length
        views.append(
            _SegmentView(
                norm_start=norm_start,
                norm_end=norm_end,
                src_start=span.byte_start,
                src_end=span.byte_end,
                source_ref=span.source_ref,
            ),
        )
    return views


@dataclass(frozen=True, slots=True)
class _SourceSlice:
    """one contiguous source-bytes slice that covers part of a regex match.

    produced by `_match_to_source_slices`; fed into
    `_slices_to_spans` which emits the final `(source_span, evidence_spans)`
    pair.
    """

    src_start: int
    src_end: int
    source_ref: SourceRef


def _match_to_source_slices(
    *,
    match_start: int,
    match_end: int,
    segments: list[_SegmentView],
) -> list[_SourceSlice]:
    """translate a normalized-byte match range into source-bytes slices.

    for each segment the match overlaps:
    - identity segments contribute a **linearly-mapped** source slice.
    - non-identity segments contribute the **whole image** of the segment —
      per-offset interpolation would invent a mapping the adapter did not
      declare (same rule as `anchor_lookup` for non-identity segments).

    consecutive slices that touch in source-bytes are merged so the common
    case (match fully inside one segment, or across adjacent identity
    segments with no gap) produces one slice.

    raises `ValueError` on an empty / out-of-domain match (a zero-length
    match is allowed when the segments contain it, but matches that fall
    outside the segment partition indicate an upstream bug in the caller
    — the regex ran on a different text than this `AnchorMap` covers).
    """

    if match_start < 0 or match_end < match_start:
        raise ValueError(
            f"_match_to_source_slices: invalid match range ({match_start}, {match_end})",
        )
    if not segments:
        if match_start == 0 and match_end == 0:
            return []
        raise ValueError(
            f"_match_to_source_slices: match range "
            f"({match_start}, {match_end}) is outside an empty anchor map",
        )
    # find overlapping segments. we do a linear scan — phase 1 adapter
    # anchor maps are small and per-document; a bisect is unnecessary.
    raw_slices: list[_SourceSlice] = []
    for seg in segments:
        if seg.norm_end <= match_start:
            continue
        if seg.norm_start >= match_end:
            break
        overlap_start = max(match_start, seg.norm_start)
        overlap_end = min(match_end, seg.norm_end)
        if overlap_start > overlap_end:
            continue
        if seg.is_identity:
            delta_start = overlap_start - seg.norm_start
            delta_end = overlap_end - seg.norm_start
            raw_slices.append(
                _SourceSlice(
                    src_start=seg.src_start + delta_start,
                    src_end=seg.src_start + delta_end,
                    source_ref=seg.source_ref,
                ),
            )
        else:
            # whole-image for non-identity segments. skip if we just took
            # the same segment's image — a zero-length overlap that lands
            # on a segment start should not produce a duplicate image.
            raw_slices.append(
                _SourceSlice(
                    src_start=seg.src_start,
                    src_end=seg.src_end,
                    source_ref=seg.source_ref,
                ),
            )
    # merge touching slices (same source_ref and s[i].src_end ==
    # s[i+1].src_start). this collapses the common case where a match
    # crossed an identity-segment boundary that was purely a cut in the
    # normalized-text partition — no real source-byte gap.
    if not raw_slices:
        return []
    merged: list[_SourceSlice] = [raw_slices[0]]
    for s in raw_slices[1:]:
        last = merged[-1]
        if s.source_ref == last.source_ref and s.src_start == last.src_end:
            merged[-1] = _SourceSlice(
                src_start=last.src_start,
                src_end=s.src_end,
                source_ref=last.source_ref,
            )
        else:
            merged.append(s)
    return merged


def _slice_to_span(s: _SourceSlice) -> SourceSpan:
    """wrap a source-bytes slice in a `SourceSpan`.

    `text_anchor_space` is hardcoded to `"source_bytes"` — phase 1 only
    emits source-bytes spans (linearizable subcontract).
    """

    return SourceSpan(
        source_ref=s.source_ref,
        text_anchor_space="source_bytes",
        byte_start=s.src_start,
        byte_end=s.src_end,
    )


def _slices_to_spans(
    slices: list[_SourceSlice],
) -> tuple[SourceSpan, tuple[SourceSpan, ...]]:
    """pick the `source_span` and `evidence_spans` pair for a match.

    - one slice → that slice is `source_span`, `evidence_spans=()`.
    - multiple slices → first is `source_span`; the remainder are
      `evidence_spans` so non-contiguous source-byte coverage remains
      honest (no fuzzy envelope). the brief forbids smear here.
    - zero slices (zero-length match against a non-empty doc) → treated
      as an invalid input; the caller guards by skipping zero-length
      matches before calling this helper.
    """

    if not slices:
        raise ValueError(
            "_slices_to_spans: no slices to convert (zero-length match?)",
        )
    primary = _slice_to_span(slices[0])
    evidence = tuple(_slice_to_span(s) for s in slices[1:])
    return primary, evidence


class RegexCandidateStrategy:
    """phase-1 deterministic regex `CandidateStrategy`.

    satisfies the `CandidateStrategy` protocol in `extractx.core.contracts`.
    stateless and safe to share; `generate(...)` is pure over
    `(field_spec, document_view, instance_hint)`.
    """

    def __init__(self, *, context_builder: CandidateContextBuilder | None = None) -> None:
        self._context_builder = context_builder

    def generate(
        self,
        field_spec: FieldSpec,
        document_view: DocumentView,
        instance_hint: InstanceHint | None = None,
    ) -> CandidateSet:
        """enumerate regex matches against `document_view.normalized_text`
        and emit a canonical `CandidateSet`.

        raises `SpecError` when no `FieldSpec.strategy_bindings` entry targets
        the regex strategy,
        refers to a different strategy class, or carries malformed params.
        raises `ValueError` only on upstream-invariant violations (e.g.,
        an anchor map that does not cover the regex's match range).
        """

        binding = binding_for_strategy(
            field_spec,
            RegexCandidateStrategy,
            "RegexCandidateStrategy",
        )
        self._assert_binding_targets_self(binding, field_spec)
        params = RegexStrategyParams.from_mapping(binding.params)
        strategy_id = self._strategy_id_for(params)
        context_builder = self._context_builder or ByteWindowCandidateContextBuilder(
            window_bytes=params.context_window_bytes,
        )

        normalized_bytes = document_view.normalized_text.encode("utf-8")
        segments = _segment_views(document_view.anchor_map, len(normalized_bytes))

        # compile against bytes: the match offsets are therefore UTF-8 byte
        # offsets into `normalized_bytes`, which is the same coordinate
        # system as `anchor_map`'s domain. compiling against str would
        # yield code-point offsets and re-introduce the unit mismatch
        # ADR-0006 forbids.
        pattern = re.compile(params.pattern.encode("utf-8"), params.flags)
        candidates: list[Candidate] = []
        for match in pattern.finditer(normalized_bytes):
            span_pair = self._match_span(match, params.group)
            if span_pair is None:
                # named group did not participate in this match. skip
                # honestly — fabricating a span would violate the "spans
                # emitted match the adapter's declared subcontract" rule.
                continue
            start, end, match_full_bytes = span_pair
            slices = _match_to_source_slices(
                match_start=start,
                match_end=end,
                segments=segments,
            )
            if not slices:
                # zero-length match that did not align with any segment;
                # we drop it rather than emit a zero-length candidate
                # with invented provenance.
                continue
            source_span, evidence_spans = _slices_to_spans(slices)
            # note: when a capture group drove the primary span, we do NOT
            # emit the full-match envelope as extra evidence. evidence is
            # used exclusively to preserve non-contiguous source-byte
            # coverage of the selected span (group or full match) — it is
            # not a general "surrounding context" carrier. bounded
            # normalized-text context is carried separately on
            # `Candidate.context` for LLM classification.

            # seam-C-local honesty check: primary and every evidence span
            # must round-trip through `anchor_map`. we do this before id
            # composition so a bad span fails at the earliest honest seam
            # rather than becoming an unrecoverable `candidate_id`.
            validate_source_span_against_view(source_span, document_view)
            for es in evidence_spans:
                validate_source_span_against_view(es, document_view)

            text = match_full_bytes.decode("utf-8", errors="strict")
            candidate_id = candidate_id_for(
                strategy_id=strategy_id,
                source_span=source_span,
                evidence_spans=evidence_spans,
                normalized_structural_payload=None,
            )
            candidates.append(
                Candidate(
                    candidate_id=candidate_id,
                    text=text,
                    source_kind="text",
                    source_id=strategy_id,
                    source_span=source_span,
                    evidence_spans=evidence_spans,
                    context=context_builder.build(
                        normalized_bytes=normalized_bytes,
                        match_start=start,
                        match_end=end,
                    ),
                    context_span=context_builder.span(
                        normalized_bytes=normalized_bytes,
                        match_start=start,
                        match_end=end,
                        source_ref=document_view.source_ref,
                    )
                    if isinstance(context_builder, ByteWindowCandidateContextBuilder)
                    else None,
                    normalized_span=normalized_match_span(
                        source_ref=document_view.source_ref,
                        match_start=start,
                        match_end=end,
                    ),
                    entity_type=params.entity_type or field_spec.value_kind.name,
                    normalized_hint=None,
                    structured_payload=None,
                ),
            )

        return build_candidate_set(
            field_id=field_spec.field_id,
            document_id=document_view.document_id,
            candidates=tuple(candidates),
            strategy_id=strategy_id,
            instance_hint=instance_hint,
        )

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _assert_binding_targets_self(
        self,
        binding: StrategyBinding,
        field_spec: FieldSpec,
    ) -> None:
        """fail loudly if the binding does not point at this strategy.

        dispatch between strategies is an executor concern in general;
        still, the strategy must refuse to run on a binding that names a
        different class — silently accepting would let a spec authored
        for a later strategy smuggle through here and produce wrong
        provenance.
        """

        if binding.kind != "candidate":
            raise SpecError(
                "RegexCandidateStrategy: StrategyBinding.kind must be "
                f"'candidate', got {binding.kind!r} for field "
                f"{field_spec.field_id!r}",
            )
        cls = binding.cls
        # `StrategyBinding.cls` is typed `type`; no runtime `isinstance`
        # check needed here. accept the exact class or any subclass.
        if cls is not RegexCandidateStrategy and not issubclass(cls, RegexCandidateStrategy):
            raise SpecError(
                "RegexCandidateStrategy: StrategyBinding.cls names "
                f"{cls!r}, not RegexCandidateStrategy, for field "
                f"{field_spec.field_id!r}",
            )

    def _strategy_id_for(self, params: RegexStrategyParams) -> str:
        """compose a strategy id that identifies *this* strategy + params.

        `producer_version` composition for algorithmic producers uses
        `stable_hash` over code-identifying material; the strategy_id on
        `CandidateSet.strategy_id` plays a narrower role: it must be
        deterministic over equal params and participate in `candidate_id`
        composition. we take the sha256 of the class's qualname plus the
        params payload, then wrap it with the `REGEX_STRATEGY_ID_PREFIX`
        so humans reading logs see the strategy kind at a glance.
        """

        payload: dict[str, Any] = {
            "cls": f"{RegexCandidateStrategy.__module__}.{RegexCandidateStrategy.__qualname__}",
            "params": params.canonical_hash_payload(),
        }
        digest = stable_hash(payload)
        return f"{REGEX_STRATEGY_ID_PREFIX}:{digest}"

    def _match_span(
        self,
        match: re.Match[bytes],
        group: int | str | None,
    ) -> tuple[int, int, bytes] | None:
        """return the (start, end, matched_bytes) triple for the selected group.

        when a named / numbered group does not participate in the match,
        returns `None` so the caller skips this match honestly rather than
        emitting a candidate with fabricated provenance.
        """

        if group is None:
            return match.start(), match.end(), match.group(0)
        try:
            start = match.start(group)
            end = match.end(group)
        except (IndexError, re.error):
            # `re.error` surfaces for named groups that do not exist at
            # all in the pattern; `IndexError` for numbered groups out
            # of range. we escalate both to `SpecError` because they
            # indicate bad user params, not a per-match miss.
            raise SpecError(
                f"RegexCandidateStrategy: group {group!r} is not a valid "
                "group reference for the supplied pattern",
            ) from None
        if start < 0 or end < 0:
            # group exists in the pattern but did not participate in this
            # match (e.g. an optional alternation). skip cleanly.
            return None
        matched = match.group(group)
        # bytes pattern → bytes match. when the group participated,
        # `matched` is bytes; pyright cannot narrow across the runtime
        # guard above, so we assert the type for clarity.
        assert isinstance(matched, bytes)
        return start, end, matched


def algorithmic_code_hash() -> str:
    """return the algorithmic `code_hash` for this strategy.

    kept as a module-level helper so future producer-version composition
    (once a versioning thread lands) can consume it without reaching
    inside the strategy class. `algorithmic_producer_version` is imported
    here for its import side — the composition surface — and to document
    that this strategy is algorithmic (§8: `code:{code_hash}` shape).
    """

    digest = stable_hash(
        f"{RegexCandidateStrategy.__module__}.{RegexCandidateStrategy.__qualname__}",
    )
    return algorithmic_producer_version(digest)
