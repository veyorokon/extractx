"""phase-1 deterministic structural `InstancePlanner`.

see docs/architecture.md §7 seam G.planner, §9 canonical objects
(`InstanceGroupingKey`, `InstancePlan`, `GroupingEvidence`, `GroupingPolicy`),
§11 iterative pseudocode (pre-plan C->D-only flow), §15 anti-patterns,
§17 proof table entries for G.planner, plus ADR-0003 (layer 3 stays out
of planner and resolver) and ADR-0006 (planner-produced anchors
preserve the `DocumentView`'s `text_anchor_space`).

phase-1 planning policy (from the task brief, fixed):

- non-empty `boundary_anchor_spans` -> dedup preserving input order;
  each distinct span becomes one tentative instance anchor.
- empty `boundary_anchor_spans` -> attempt one document-scope
  structural anchor derived deterministically from `document_view` in
  the adapter's declared `text_anchor_space`:
    - `normalized_text`:   byte_start=0,
                           byte_end=len(document_view.normalized_text
                                        .encode("utf-8"))
    - `source_bytes`:      byte_start=0,
                           byte_end=len(document_view.normalized_text
                                        .encode("utf-8"))
                           (phase-1 pins the upper bound to the UTF-8
                           byte length of the normalized text; seam A
                           already reflects `source_bytes` as the
                           adapter's declared anchor space. this keeps
                           the planner honest — it never invents a
                           source-bytes length it cannot see without a
                           richer `DocumentView` surface.)
- if `document_view.normalized_text` is empty, a document-scope anchor
  cannot be formed honestly and no advisory anchors were provided ->
  `NegativeOutcome("planning", "no_tentative_keys", ...)`.
- `tentative_keys` count > `spec.grouping_policy.max_instances` ->
  `NegativeOutcome("planning", "max_exceeded", ...)`.

planner output stays tentative; `G.resolver` owns final instance truth.

explicit non-goals for this phase-1 planner (all owned by later
threads):

- resolver behavior (merge, split, promotion to
  `Evidence`)
- layer-3 / `model_validator` / `InstanceValidator`
- execution / runtime / reporter / budget orchestration
- `UsageEvent` emission (algorithmic planners do not emit provider usage)
- soft / neural / graph planner behavior
- replay artifact writing, interview capture, materialization
- full iterative strategy orchestration (the C->D pre-plan loop stays
  out of scope here — the pure helpers in `instances/boundary.py` give
  that loop deterministic building blocks)
- planner-conditioned retries or `ExecutorPolicy` routing

`producer_version` composition follows the seam-C / seam-D / seam-F
pattern: `stable_hash("{cls.__module__}.{cls.__qualname__}")` fed to
`algorithmic_producer_version(...)`, producing a `code:{code_hash}`
string. no model id, no prompt-template hash, no timestamp.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from extractx.core.anchors import SourceSpan, TextAnchorSpace
from extractx.core.objects import GroupingEvidence, InstanceGroupingKey, InstancePlan
from extractx.core.outcomes import NegativeOutcome
from extractx.core.versions import algorithmic_producer_version, stable_hash

if TYPE_CHECKING:
    from extractx.core.objects import DocumentView, ExtractionSpec

__all__ = [
    "StructuralInstancePlanner",
    "algorithmic_code_hash",
]


# static clustering-signal mode labels; carried inside
# `GroupingEvidence.clustering_signals` so downstream diagnostics can
# tell the two phase-1 anchor sources apart without parsing prose.
_MODE_BOUNDARY_ANCHORS = "boundary_anchors"
_MODE_DOCUMENT_SCOPE_FALLBACK = "document_scope_fallback"


class StructuralInstancePlanner:
    """deterministic algorithmic `InstancePlanner` per phase-1 policy.

    structural `InstancePlanner` subtype — no base class required. the
    class deliberately holds no configurable state: identity is carried
    by `producer_version`, which is composed from the class's qualname
    so any subclass with different behavior produces a different
    `producer_version` automatically.
    """

    @property
    def producer_version(self) -> str:
        """the `code:{code_hash}` string attached to every emitted `InstancePlan`."""

        return algorithmic_code_hash()

    def plan(
        self,
        document_view: DocumentView,
        spec: ExtractionSpec,
        boundary_anchor_spans: tuple[SourceSpan, ...] = (),
    ) -> InstancePlan | NegativeOutcome:
        """run the phase-1 structural planner on the given inputs.

        see module docstring and docs/architecture.md §7 seam G.planner
        for the full policy; the dispatch is intentionally narrow.
        """

        producer_version = self.producer_version

        anchors, mode = self._select_anchors(
            document_view=document_view,
            boundary_anchor_spans=boundary_anchor_spans,
        )
        if not anchors:
            # no advisory anchors and no honest structural fallback.
            # canonical planner failure per §7 seam G.planner "no
            # tentative keys" bullet.
            return NegativeOutcome(
                category="planning",
                code="no_tentative_keys",
                field_id=None,
                instance_key=None,
                candidate_count=None,
                reason="no_tentative_keys",
            )

        max_instances = spec.grouping_policy.max_instances
        if max_instances is not None and len(anchors) > max_instances:
            # canonical planner failure per §17 proof table entry
            # "GroupingPolicy.max_instances violation emits
            # NegativeOutcome('planning', 'max_exceeded')".
            return NegativeOutcome(
                category="planning",
                code="max_exceeded",
                field_id=None,
                instance_key=None,
                candidate_count=None,
                reason="max_exceeded",
            )

        tentative_keys = tuple(
            self._build_instance_key(ordinal=ordinal, anchor=anchor)
            for ordinal, anchor in enumerate(anchors)
        )

        grouping_evidence = GroupingEvidence(
            stage="planned",
            anchor_spans=anchors,
            clustering_signals={
                "mode": mode,
                "anchor_count": len(anchors),
            },
            confidence=None,
            producer_version=producer_version,
        )

        return InstancePlan(
            tentative_keys=tentative_keys,
            grouping_evidence=grouping_evidence,
            producer_version=producer_version,
        )

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _select_anchors(
        self,
        *,
        document_view: DocumentView,
        boundary_anchor_spans: tuple[SourceSpan, ...],
    ) -> tuple[tuple[SourceSpan, ...], str]:
        """return the anchors to use and the clustering-signal mode label.

        - non-empty advisory anchors -> dedup preserving input order,
          mode="boundary_anchors".
        - empty advisory anchors -> one document-scope anchor (if a
          deterministic one can be formed), mode="document_scope_fallback".
        - no anchors available -> `((), "")`; the caller emits
          `NegativeOutcome("planning", "no_tentative_keys", ...)`.
        """

        if boundary_anchor_spans:
            deduped = _dedup_preserving_order(boundary_anchor_spans)
            return deduped, _MODE_BOUNDARY_ANCHORS

        fallback = _document_scope_anchor(document_view)
        if fallback is None:
            return (), ""
        return (fallback,), _MODE_DOCUMENT_SCOPE_FALLBACK

    def _build_instance_key(self, *, ordinal: int, anchor: SourceSpan) -> InstanceGroupingKey:
        """construct one tentative internal grouping key for the given ordinal + anchor.

        `group_id` is a deterministic content hash over
        `(group_anchors, group_key_material)` per §7 seam G.planner
        invariant:

            group_anchors       = (anchor,)
            group_key_material  = ("structural", ordinal)

        `group_key_material` is the small, deterministic, planner-owned
        tuple that distinguishes otherwise-identical anchors by
        ordinal. it carries a static `"structural"` tag so any future
        planner variant that computes a different material tuple
        automatically produces a different `group_id`.
        """

        group_anchors: tuple[SourceSpan, ...] = (anchor,)
        group_key_material: tuple[object, ...] = ("structural", ordinal)
        group_id = _compute_group_id(
            group_anchors=group_anchors,
            group_key_material=group_key_material,
        )
        return InstanceGroupingKey(
            group_id=group_id,
            ordinal=ordinal,
            group_anchors=group_anchors,
        )


def algorithmic_code_hash() -> str:
    """return the phase-1 planner's `producer_version` string.

    mirrors the pattern in
    `extractx.selection.algorithmic.singleton.algorithmic_code_hash`
    and `extractx.candidates.generators.regex.algorithmic_code_hash`
    so the algorithmic producer-version sites compose their
    `code_hash` the same way: `stable_hash("{cls.__module__}.
    {cls.__qualname__}")` wrapped through
    `algorithmic_producer_version`.

    exposed at module scope so downstream threads (replay, versioning)
    can consume it without instantiating the planner.
    """

    digest = stable_hash(
        f"{StructuralInstancePlanner.__module__}.{StructuralInstancePlanner.__qualname__}",
    )
    return algorithmic_producer_version(digest)


# ---------------------------------------------------------------------------
# module-private helpers
# ---------------------------------------------------------------------------


def _dedup_preserving_order(
    spans: tuple[SourceSpan, ...],
) -> tuple[SourceSpan, ...]:
    """dedup `spans` while preserving input order.

    equality is pydantic-`BaseModel` equality, which compares every
    field (including `source_ref`, `text_anchor_space`, `byte_start`,
    `byte_end`, `page_ref`, `bounding_region`). two spans that differ
    in any of those are distinct anchors.

    `SourceSpan` is frozen and hashable in principle, but pydantic v2
    does not emit `__hash__` unless a model opts in. we therefore walk
    the sequence linearly and compare with a list of seen spans; the
    input is small (one span per boundary_defining selection) so this
    is not a performance-sensitive site.
    """

    seen: list[SourceSpan] = []
    out: list[SourceSpan] = []
    for span in spans:
        if any(span == existing for existing in seen):
            continue
        seen.append(span)
        out.append(span)
    return tuple(out)


def _document_scope_anchor(document_view: DocumentView) -> SourceSpan | None:
    """return the phase-1 deterministic document-scope structural anchor.

    the fallback shape (for both `text_anchor_space` subcontracts) is
    pinned to the UTF-8 byte length of `document_view.normalized_text`.
    for `normalized_text`-space adapters this is exactly what seam F
    layer 1 validates spans against; for `source_bytes`-space adapters
    we use the same length because `DocumentView` itself does not
    expose a raw-source-bytes length — `source_ref.content_hash` is the
    only source-bytes authority on the view, and a content hash does
    not carry a length.

    when `document_view.normalized_text` is empty, no deterministic
    document-scope anchor can be formed honestly: a zero-length anchor
    carries no positional information, and the seam-A contract makes
    an empty view a legitimate shape rather than an error. in that
    case we return `None` and let the caller emit
    `NegativeOutcome("planning", "no_tentative_keys", ...)`.

    the adapter's declared `text_anchor_space` is inferred from the
    `anchor_map`'s entries when they exist (ADR-0006: an adapter must
    not mix subcontracts within a single `DocumentView`). if the map
    is empty, phase-1 defaults to `"normalized_text"` — this matches
    the most common plain-text / markdown adapter shape, and because
    an empty `anchor_map` combined with non-empty `normalized_text`
    would already be rejected by seam A's `anchor_validate_total`
    invariant.
    """

    normalized_text = document_view.normalized_text
    if not normalized_text:
        return None

    byte_end = len(normalized_text.encode("utf-8"))

    text_anchor_space = _infer_text_anchor_space(document_view)

    return SourceSpan(
        source_ref=document_view.source_ref,
        text_anchor_space=text_anchor_space,
        byte_start=0,
        byte_end=byte_end,
    )


def _infer_text_anchor_space(document_view: DocumentView) -> TextAnchorSpace:
    """return the `text_anchor_space` declared by `document_view`'s anchor_map.

    ADR-0006 requires an adapter to declare exactly one
    `text_anchor_space` per `DocumentView`; the invariant is enforced
    at seam A. we read the first entry's span to recover it. when the
    map is empty (legitimate only for empty `normalized_text`; that
    case is already handled by the caller), we default to
    `"normalized_text"` to keep the helper total.
    """

    entries = document_view.anchor_map.entries
    if entries:
        _offset, span = entries[0]
        return span.text_anchor_space
    return "normalized_text"


def _compute_group_id(
    *,
    group_anchors: tuple[SourceSpan, ...],
    group_key_material: tuple[object, ...],
) -> str:
    """compute `InstanceGroupingKey.group_id` per §7 seam G.planner invariant.

    shape: deterministic hash over
    `(group_anchors_serialized, group_key_material)` where each span is
    serialized via `model_dump(mode="json")` so the hash is stable
    across runs and insensitive to pydantic-internal representation.
    `text_anchor_space` is part of the dumped payload — spans with
    identical `byte_*` but different `text_anchor_space` produce
    different `group_id`s (ADR-0006).
    """

    serialized_anchors = [span.model_dump(mode="json") for span in group_anchors]
    # wrap in a single stable tuple so callers can evolve
    # `group_key_material` without changing the hash shape of
    # `group_anchors`.
    return stable_hash((serialized_anchors, list(group_key_material)))
