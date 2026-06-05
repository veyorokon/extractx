"""behavioral tests for the phase-1 `StructuralInstancePlanner`.

proof targets (from
docs/tasks/seam-g-planner-phase-1-structural-instance-planner.md,
"Focused proof"):

- non-empty advisory `boundary_anchor_spans` produce tentative keys
  anchored to those spans.
- duplicate advisory anchor spans are deduplicated by the planner
  while preserving stable order.
- zero boundary-defining anchors with a valid structural fallback
  produce exactly one tentative key.
- zero boundary-defining anchors and no structural fallback produce
  `NegativeOutcome(category="planning", code="no_tentative_keys", ...)`.
- `GroupingPolicy.max_instances` violation produces
  `NegativeOutcome(category="planning", code="max_exceeded", ...)`.
- `GroupingEvidence.stage == "planned"` and its `producer_version`
  matches the planner's `producer_version`.
- planner-produced `InstanceGroupingKey.group_anchors` share a single
  `text_anchor_space` matching the `DocumentView`'s adapter
  subcontract.
- same `(document_view, spec, boundary_anchor_spans)` yields byte-
  identical output across repeated calls (purity).
- no resolver behavior is smuggled into the planner.
"""

from __future__ import annotations

from extractx.core import (
    AnchorMap,
    DistanceMetric,
    DocumentView,
    ExtractionSpec,
    GroupingPolicy,
    InstancePlan,
    NegativeOutcome,
    PromptPolicy,
    SourceRef,
    SourceSpan,
    ValidationPolicy,
)
from extractx.core.objects import BudgetSpec
from extractx.instances import StructuralInstancePlanner, algorithmic_code_hash

# ---------------------------------------------------------------------------
# fixtures — small helpers, not shared conftest, so the dependencies each
# test exercises remain legible at the call site.
# ---------------------------------------------------------------------------


def _ref() -> SourceRef:
    return SourceRef(source_id="doc-1", content_hash="sha256:abc")


def _normalized_span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="normalized_text",
        byte_start=start,
        byte_end=end,
    )


def _source_bytes_span(start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_ref=_ref(),
        text_anchor_space="source_bytes",
        byte_start=start,
        byte_end=end,
    )


def _document_view(
    *,
    normalized_text: str = "hello world",
    anchor_space: str = "normalized_text",
) -> DocumentView:
    # the anchor_map carries the declared text_anchor_space so
    # `_infer_text_anchor_space` can recover it. a total anchor_map is
    # not required for these tests: we only need the first entry's
    # span.text_anchor_space to be the adapter's declared space. when
    # the text is empty we use an empty anchor_map (legitimate per
    # seam A).
    if not normalized_text:
        anchor_map = AnchorMap(entries=())
    else:
        encoded_len = len(normalized_text.encode("utf-8"))
        segment_span: SourceSpan
        if anchor_space == "normalized_text":
            segment_span = _normalized_span(0, encoded_len)
        else:
            segment_span = _source_bytes_span(0, encoded_len)
        anchor_map = AnchorMap(entries=((0, segment_span),))
    return DocumentView(
        document_id="doc-1",
        normalized_text=normalized_text,
        anchor_map=anchor_map,
        source_ref=_ref(),
    )


def _spec(
    *,
    max_instances: int | None = None,
) -> ExtractionSpec:
    grouping_policy = GroupingPolicy(
        default_distance_metric=DistanceMetric(name="noop", params={}),
        allow_parallel_instances=False,
        max_instances=max_instances,
    )
    return ExtractionSpec(
        fields=(),
        prompt_policy=PromptPolicy(),
        validation_policy=ValidationPolicy(),
        grouping_policy=grouping_policy,
        budget=BudgetSpec(),
        version="spec-version-1",
    )


# ---------------------------------------------------------------------------
# advisory-anchor flow
# ---------------------------------------------------------------------------


class TestAdvisoryAnchors:
    def test_non_empty_advisory_anchors_produce_tentative_keys(self) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(normalized_text="hello world")
        spec = _spec()
        spans = (_normalized_span(0, 5), _normalized_span(6, 11))

        result = planner.plan(doc, spec, spans)

        assert isinstance(result, InstancePlan)
        assert len(result.tentative_keys) == 2
        # each tentative key anchors to the matching advisory span, in
        # input order. ordinal is stable from planner output order.
        for ordinal, (key, span) in enumerate(
            zip(result.tentative_keys, spans, strict=True),
        ):
            assert key.ordinal == ordinal
            assert key.group_anchors == (span,)

    def test_duplicate_advisory_anchors_are_deduplicated_stably(self) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(normalized_text="hello world")
        spec = _spec()
        first = _normalized_span(0, 5)
        second = _normalized_span(6, 11)
        # intentional duplicate in the middle to assert ordering
        # stability; `second` appears after the `first`-duplicate and
        # before the `second`-duplicate.
        spans = (first, second, first, second)

        result = planner.plan(doc, spec, spans)

        assert isinstance(result, InstancePlan)
        assert len(result.tentative_keys) == 2
        assert result.tentative_keys[0].group_anchors == (first,)
        assert result.tentative_keys[1].group_anchors == (second,)

    def test_advisory_anchors_populate_grouping_evidence(self) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(normalized_text="hello world")
        spec = _spec()
        spans = (_normalized_span(0, 5), _normalized_span(6, 11))

        result = planner.plan(doc, spec, spans)

        assert isinstance(result, InstancePlan)
        evidence = result.grouping_evidence
        assert evidence.stage == "planned"
        assert evidence.anchor_spans == spans
        assert evidence.producer_version == planner.producer_version
        assert evidence.clustering_signals["mode"] == "boundary_anchors"
        assert evidence.clustering_signals["anchor_count"] == 2


# ---------------------------------------------------------------------------
# structural document-scope fallback
# ---------------------------------------------------------------------------


class TestDocumentScopeFallback:
    def test_empty_advisory_with_normalized_text_adapter_yields_one_key(
        self,
    ) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(
            normalized_text="hello world",
            anchor_space="normalized_text",
        )
        spec = _spec()

        result = planner.plan(doc, spec, ())

        assert isinstance(result, InstancePlan)
        assert len(result.tentative_keys) == 1
        key = result.tentative_keys[0]
        assert key.ordinal == 0
        assert len(key.group_anchors) == 1
        fallback = key.group_anchors[0]
        assert fallback.text_anchor_space == "normalized_text"
        assert fallback.byte_start == 0
        assert fallback.byte_end == len(b"hello world")
        assert fallback.source_ref == doc.source_ref
        assert result.grouping_evidence.clustering_signals["mode"] == ("document_scope_fallback")

    def test_empty_advisory_with_source_bytes_adapter_yields_one_key(self) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(
            normalized_text="hello world",
            anchor_space="source_bytes",
        )
        spec = _spec()

        result = planner.plan(doc, spec, ())

        assert isinstance(result, InstancePlan)
        assert len(result.tentative_keys) == 1
        fallback = result.tentative_keys[0].group_anchors[0]
        assert fallback.text_anchor_space == "source_bytes"
        assert fallback.byte_start == 0
        assert fallback.byte_end == len(b"hello world")

    def test_empty_advisory_with_empty_normalized_text_is_no_tentative_keys(
        self,
    ) -> None:
        planner = StructuralInstancePlanner()
        # empty normalized_text: seam A allows the view, but a
        # zero-length document-scope anchor carries no positional
        # information, so the planner emits a typed negative rather
        # than inventing one.
        doc = _document_view(normalized_text="")
        spec = _spec()

        result = planner.plan(doc, spec, ())

        assert isinstance(result, NegativeOutcome)
        assert result.category == "planning"
        assert result.code == "no_tentative_keys"
        assert result.field_id is None
        assert result.instance_key is None
        assert result.candidate_count is None


# ---------------------------------------------------------------------------
# max_instances policy
# ---------------------------------------------------------------------------


class TestMaxInstancesPolicy:
    def test_advisory_anchor_count_at_limit_is_allowed(self) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(normalized_text="hello world")
        spec = _spec(max_instances=2)
        spans = (_normalized_span(0, 5), _normalized_span(6, 11))

        result = planner.plan(doc, spec, spans)

        assert isinstance(result, InstancePlan)
        assert len(result.tentative_keys) == 2

    def test_advisory_anchor_count_above_limit_emits_max_exceeded(self) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(normalized_text="hello world")
        spec = _spec(max_instances=1)
        spans = (_normalized_span(0, 5), _normalized_span(6, 11))

        result = planner.plan(doc, spec, spans)

        assert isinstance(result, NegativeOutcome)
        assert result.category == "planning"
        assert result.code == "max_exceeded"
        assert result.field_id is None
        assert result.instance_key is None
        assert result.candidate_count is None

    def test_max_exceeded_is_checked_after_dedup(self) -> None:
        # dedup brings the effective count down to 1; limit=1 is OK.
        planner = StructuralInstancePlanner()
        doc = _document_view(normalized_text="hello world")
        spec = _spec(max_instances=1)
        first = _normalized_span(0, 5)
        spans = (first, first, first)

        result = planner.plan(doc, spec, spans)

        assert isinstance(result, InstancePlan)
        assert len(result.tentative_keys) == 1


# ---------------------------------------------------------------------------
# determinism and text_anchor_space consistency
# ---------------------------------------------------------------------------


class TestDeterminismAndAnchorSpace:
    def test_same_inputs_produce_byte_identical_output(self) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(normalized_text="hello world")
        spec = _spec()
        spans = (_normalized_span(0, 5), _normalized_span(6, 11))

        first = planner.plan(doc, spec, spans)
        second = planner.plan(doc, spec, spans)

        assert isinstance(first, InstancePlan)
        assert isinstance(second, InstancePlan)
        # pydantic model equality compares every field deeply; byte-
        # identical determinism is the proof the planner is pure.
        assert first == second
        # also assert group_id is stable at the hash layer (the
        # architecture invariant is "stable across runs for the same
        # inputs and the same pinned planner").
        assert [k.group_id for k in first.tentative_keys] == [
            k.group_id for k in second.tentative_keys
        ]

    def test_planner_produced_anchors_share_one_text_anchor_space(self) -> None:
        # per ADR-0006 and the task brief: all planner-produced
        # `InstanceGroupingKey.group_anchors` share a single
        # `text_anchor_space` matching the `DocumentView`'s adapter
        # subcontract. verified for both advisory and fallback flows.
        planner = StructuralInstancePlanner()

        # fallback flow — normalized_text adapter
        doc_norm = _document_view(anchor_space="normalized_text")
        fallback_plan = planner.plan(doc_norm, _spec(), ())
        assert isinstance(fallback_plan, InstancePlan)
        spaces_fallback = {
            span.text_anchor_space
            for key in fallback_plan.tentative_keys
            for span in key.group_anchors
        }
        assert spaces_fallback == {"normalized_text"}

        # fallback flow — source_bytes adapter
        doc_bytes = _document_view(anchor_space="source_bytes")
        fallback_bytes_plan = planner.plan(doc_bytes, _spec(), ())
        assert isinstance(fallback_bytes_plan, InstancePlan)
        spaces_fallback_bytes = {
            span.text_anchor_space
            for key in fallback_bytes_plan.tentative_keys
            for span in key.group_anchors
        }
        assert spaces_fallback_bytes == {"source_bytes"}

        # advisory flow — normalized_text adapter, advisory spans are
        # normalized_text. in phase-1 the caller is responsible for
        # ensuring advisory spans come from the declared subcontract
        # (they are sourced from seam C candidates, which obey seam A).
        # we verify the planner preserves rather than coerces.
        advisory = (_normalized_span(0, 5), _normalized_span(6, 11))
        advisory_plan = planner.plan(doc_norm, _spec(), advisory)
        assert isinstance(advisory_plan, InstancePlan)
        spaces_advisory = {
            span.text_anchor_space
            for key in advisory_plan.tentative_keys
            for span in key.group_anchors
        }
        assert spaces_advisory == {"normalized_text"}


# ---------------------------------------------------------------------------
# producer_version / grouping_evidence
# ---------------------------------------------------------------------------


class TestProducerVersionAndGroupingEvidence:
    def test_instance_plan_producer_version_matches_core_helper(self) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(normalized_text="hello world")
        spec = _spec()
        spans = (_normalized_span(0, 5),)

        result = planner.plan(doc, spec, spans)

        assert isinstance(result, InstancePlan)
        # §17 proof table: "algorithmic producer emits
        # `producer_version = 'code:{code_hash}'` using the core helper".
        assert result.producer_version == algorithmic_code_hash()

    def test_grouping_evidence_stage_and_producer_version(self) -> None:
        planner = StructuralInstancePlanner()
        doc = _document_view(normalized_text="hello world")
        spec = _spec()

        result = planner.plan(doc, spec, ())

        assert isinstance(result, InstancePlan)
        evidence = result.grouping_evidence
        assert evidence.stage == "planned"
        assert evidence.producer_version == planner.producer_version
        # fallback flow anchor is the only anchor; evidence.anchor_spans
        # must equal the planner anchors that informed the plan.
        assert evidence.anchor_spans == result.tentative_keys[0].group_anchors


# ---------------------------------------------------------------------------
# anti-smuggling: no resolver / no layer-3 / no execution behavior
# ---------------------------------------------------------------------------


class TestNoResolverSmuggling:
    def test_planner_has_no_resolve_or_promote_methods(self) -> None:
        # the planner surface is `plan` + `producer_version` property.
        # any method hinting at resolver / promotion / final-assignment
        # behavior is a smuggling signal.
        planner = StructuralInstancePlanner()
        public_attrs = {name for name in dir(planner) if not name.startswith("_")}
        # no resolver-flavored names
        forbidden = {
            "resolve",
            "resolve_instances",
            "promote",
            "merge",
            "split",
            "finalize",
            "assign",
        }
        assert public_attrs.isdisjoint(forbidden)

    def test_planner_does_not_invoke_seams_c_d_e_f(self) -> None:
        # the planner's `plan` signature takes only document_view,
        # spec, and advisory anchors — no CandidateStrategy, no
        # Selector, no SelectionAdapter, no ProposalValidator are
        # carried in its surface. a change to that surface shape is
        # caught here.
        import inspect

        sig = inspect.signature(StructuralInstancePlanner.plan)
        param_names = set(sig.parameters.keys())
        # phase-1 pseudocode in §11 calls
        # `G.planner(doc, spec, tuple(boundary_defining_spans))`, so
        # the planner does not carry the upstream seams as inputs.
        forbidden_names = {
            "candidate_strategy",
            "selector",
            "selection_adapter",
            "proposal_validator",
            "reporter",
            "budget",
            "context_pack",
        }
        assert param_names.isdisjoint(forbidden_names)
