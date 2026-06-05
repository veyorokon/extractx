"""`CandidateSet` construction helpers for seam C.

see docs/architecture.md §7 seam C and §9. canonical object shapes
(`Candidate`, `CandidateSet`) live in `extractx.core.objects`; this module
holds only the narrow, seam-C-local helpers that every `CandidateStrategy`
needs to honestly emit a canonical `CandidateSet`:

- `candidate_id_for(...)` composes the deterministic candidate id from
  `(strategy_id, source_span, evidence_spans, normalized_structural_payload)`
  per the §7 seam C invariant. this is the single site that knows how to
  hash a candidate's identifying content. keeping it out of core (and out
  of strategy impls) avoids the "every generator invents its own id rule"
  failure mode.
- `validate_source_span_against_view(...)` is the smallest seam-C-local
  span-validity check against a `DocumentView.anchor_map`, honoring ADR-0006
  coordinate-space discipline. this does **not** implement seam F layer 1;
  it only guards the generator's own output so strategies fail loudly at
  emission time rather than silently produce spans that later layers would
  reject.
- `build_candidate_set(...)` constructs a `CandidateSet` from a tuple of
  `Candidate`s, asserting id uniqueness and preserving the `instance_hint`
  the caller supplied. it does not dedup by normalized value, ever — the
  seam C invariant. empty-input is a valid canonical output (the selector's
  `NO_CANDIDATES` outcome lives at seam D, not here).

all helpers are pure and synchronous. they do not touch the filesystem,
the network, or any mutable cross-run state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from extractx.core.anchors import (
    SourceSpan,
    anchor_invert,
    check_normalized_text_span,
)
from extractx.core.objects import (
    Candidate,
    CandidateSet,
    DocumentView,
    FieldId,
    InstanceHint,
)
from extractx.core.versions import stable_hash

__all__ = [
    "build_candidate_set",
    "candidate_id_for",
    "validate_source_span_against_view",
]


def candidate_id_for(
    *,
    strategy_id: str,
    source_span: SourceSpan,
    evidence_spans: tuple[SourceSpan, ...] = (),
    normalized_structural_payload: Mapping[str, Any] | None = None,
) -> str:
    """compose a deterministic candidate id per docs/architecture.md §7 seam C.

    the id is a sha256 over the tuple `(strategy_id, source_span,
    evidence_spans, normalized_structural_payload)` — **never** a call
    counter and **never** a uuid4. this is the one helper every strategy
    calls; keeping the rule local avoids "each generator invents its own
    id" drift.

    `normalized_structural_payload` is strategy-specific. strategies that
    emit purely span-based candidates (like the phase-1 regex strategy)
    pass `None`. strategies that emit structural matches (tables, clauses)
    own the payload shape and feed it here.
    """

    if not strategy_id:
        raise ValueError("candidate_id_for: strategy_id must be non-empty")
    payload: list[Any] = [
        strategy_id,
        source_span.model_dump(mode="json"),
        [span.model_dump(mode="json") for span in evidence_spans],
        # `None` and empty-dict are deliberately kept distinct in the hash so
        # a payload-less strategy is not confused with an empty-payload one.
        None if normalized_structural_payload is None else dict(normalized_structural_payload),
    ]
    return stable_hash(payload)


def validate_source_span_against_view(
    span: SourceSpan,
    document_view: DocumentView,
) -> None:
    """assert `span` is valid against `document_view.anchor_map` per its
    `text_anchor_space`, per docs/architecture.md §7 seam C and ADR-0006.

    - `normalized_text`: offsets must be UTF-8-aligned and within
      `normalized_text.encode('utf-8')`. delegates to
      `check_normalized_text_span` so the rule stays shared with seam F
      layer 1.
    - `source_bytes`: the span must round-trip through `anchor_invert`
      against the view's `anchor_map`. this is the "recoverable from
      `anchor_map` by inversion" clause of the seam C invariant.

    raises `ValueError` when the span is not valid. seam C strategies call
    this before emitting; seam F layer 1 will re-check (the single-site
    rule for layer 1 is untouched — this is only seam-C-local defense).
    """

    if span.text_anchor_space == "normalized_text":
        check_normalized_text_span(span, document_view.normalized_text)
        return
    if span.text_anchor_space == "source_bytes":
        # `anchor_invert` is the inversion primitive seam A installed; if
        # the span is not round-trippable, it raises with a diagnostic
        # message. we do not catch-and-rewrap: the error message from
        # `anchor_invert` already names the failing segment.
        anchor_invert(document_view.anchor_map, span)
        return
    raise ValueError(
        f"validate_source_span_against_view: unknown text_anchor_space {span.text_anchor_space!r}",
    )


def build_candidate_set(
    *,
    field_id: FieldId,
    document_id: str,
    candidates: tuple[Candidate, ...],
    strategy_id: str,
    instance_hint: InstanceHint | None = None,
) -> CandidateSet:
    """build a `CandidateSet` with candidate-id uniqueness enforced.

    fails loudly with `ValueError` when two candidates in the same set
    share a `candidate_id` — that invariant comes directly from the §7
    seam C contract and is the single honest early signal that an id
    composition is wrong somewhere upstream. duplicate *text* is
    explicitly allowed (no dedup by normalized value, ever); only the
    deterministic id must be unique.

    an empty `candidates` tuple is a valid canonical output; the selector
    maps an empty set to `NO_CANDIDATES` at seam D. seam C never invents
    candidates to avoid emptiness.

    `instance_hint` is stored on the returned `CandidateSet` verbatim
    — strategies may ignore it for generation purposes, but must pass it
    through so downstream seams see the same hint they supplied.
    """

    seen: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_id in seen:
            raise ValueError(
                f"build_candidate_set: duplicate candidate_id "
                f"{candidate.candidate_id!r} in CandidateSet for field "
                f"{field_id!r}; seam C requires unique candidate ids",
            )
        seen.add(candidate.candidate_id)
    return CandidateSet(
        field_id=field_id,
        document_id=document_id,
        instance_hint=instance_hint,
        candidates=candidates,
        strategy_id=strategy_id,
    )
