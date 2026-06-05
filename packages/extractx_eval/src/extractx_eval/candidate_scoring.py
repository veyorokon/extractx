"""Deterministic candidate-stage benchmark scoring."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from extractx.candidates.candidate_set import build_candidate_set
from extractx.candidates.filters import apply_filter_binding
from extractx.core.anchors import SourceRef, SourceSpan
from extractx.core.contracts import CandidateStrategy
from extractx.core.filters import (
    And,
    ContainedBy,
    Contains,
    ContextContains,
    FilterExpr,
    LabelIn,
    LabelNotIn,
    NumericRange,
    Or,
)
from extractx.core.objects import Candidate, CandidateSet, ExtractionSpec, FieldSpec
from extractx.core.versions import stable_hash
from extractx.source import TextAdapter
from pydantic import BaseModel, ConfigDict, Field

from .fixtures import BenchmarkFixture, FixturePack, GoldEvidence, GoldField
from .reports import (
    BenchmarkAggregate,
    BenchmarkCaseRow,
    BenchmarkFieldRow,
    BenchmarkReport,
    MissAttribution,
    ScorerMetadata,
)

__all__ = [
    "SpanMatchConfig",
    "SpanMatchMode",
    "score_candidates",
]

type SpanMatchMode = Literal["exact", "overlap", "contains", "contained_by", "iou"]
type _MatchVia = Literal["span", "text", "context"]

_SCORER_VERSION = "candidate_scoring.v1"


class SpanMatchConfig(BaseModel):
    """Span comparison policy for gold evidence and observed candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: SpanMatchMode = "overlap"
    min_iou: float = Field(default=0.5, ge=0.0, le=1.0)
    allow_context_text_match: bool = True


class _EvidenceMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gold_index: int
    candidate_id: str
    candidate_text: str
    strategy_id: str
    matched_via: _MatchVia


class FieldGoldExpectation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str
    expected_values: tuple[Any, ...] = ()
    evidence: tuple[GoldEvidence, ...] = ()
    expected_absent: bool = False


def score_candidates(
    schema: type[BaseModel],
    fixtures: FixturePack | Iterable[BenchmarkFixture],
    *,
    span_match: SpanMatchConfig | None = None,
) -> BenchmarkReport:
    """Score deterministic candidate generation and filtering against fixtures.

    This scorer does not call a selector or provider. It exercises seam C
    strategy bindings and the candidate-filter seam, then reports recall and
    precision as derived benchmark rows.
    """

    match_config = span_match or SpanMatchConfig()
    fixture_rows = tuple(fixtures.fixtures if isinstance(fixtures, FixturePack) else fixtures)
    input_refs = (fixtures.pack_id,) if isinstance(fixtures, FixturePack) else ()
    spec = ExtractionSpec.from_pydantic(schema)
    spec_fields = {field.field_id: field for field in spec.fields}

    case_rows: list[BenchmarkCaseRow] = []
    field_rows: list[BenchmarkFieldRow] = []
    recall_candidates_count = 0
    recall_candidates_total = 0
    precision_candidates_count = 0
    precision_candidates_total = 0
    recall_filtered_count = 0
    recall_filtered_total = 0
    precision_filtered_count = 0
    precision_filtered_total = 0

    for fixture in fixture_rows:
        case_status: Literal["passed", "failed", "setup_failed", "comparability_failed"] = "passed"
        expected_fields = expected_fields_by_id(fixture)
        document_view = TextAdapter().adapt(
            fixture.document.encode("utf-8"),
            SourceRef(
                source_id=fixture.document_path or fixture.case_id,
                content_hash=stable_hash(fixture.document),
            ),
        )

        for field_id, gold in expected_fields.items():
            field_spec = spec_fields.get(field_id)
            if field_spec is None:
                case_status = _worse_status(case_status, "comparability_failed")
                field_rows.append(
                    BenchmarkFieldRow(
                        case_id=fixture.case_id,
                        field_id=field_id,
                        stage="candidates",
                        gap_kind="comparability_failure",
                        expected=gold_summary(gold),
                        attributions=(
                            MissAttribution(
                                stage="comparability",
                                kind="fixture_schema_mismatch",
                                field_id=field_id,
                                reason=f"fixture field {field_id!r} is not present in schema",
                                details={"fixture_value": gold.expected_values},
                            ),
                        ),
                        message=f"fixture field {field_id!r} is not present in schema",
                    ),
                )
                continue
            if gold.expected_absent:
                try:
                    candidate_set = _generate_candidate_set(field_spec, document_view)
                except Exception as exc:  # noqa: BLE001 - benchmark report owns setup failures.
                    case_status = _worse_status(case_status, "setup_failed")
                    field_rows.append(
                        BenchmarkFieldRow(
                            case_id=fixture.case_id,
                            field_id=field_id,
                            stage="candidates",
                            gap_kind="setup_failure",
                            expected=gold_summary(gold),
                            attributions=(
                                MissAttribution(
                                    stage="candidates",
                                    kind="setup_failure",
                                    field_id=field_id,
                                    reason=f"{type(exc).__name__}: {exc}",
                                    details={"error_type": type(exc).__name__},
                                ),
                            ),
                            message=f"{type(exc).__name__}: {exc}",
                        ),
                    )
                    continue
                filtered_set = (
                    apply_filter_binding(candidate_set, field_spec.filter_binding)
                    if field_spec.filter_binding is not None
                    else candidate_set
                )
                if candidate_set.candidates:
                    case_status = _worse_status(case_status, "failed")
                if filtered_set.candidates:
                    case_status = _worse_status(case_status, "failed")
                precision_candidates_total += len(candidate_set.candidates)
                precision_filtered_total += len(filtered_set.candidates)
                filter_drops = _filter_drops(
                    raw_set=candidate_set,
                    filtered_set=filtered_set,
                    expr=(
                        field_spec.filter_binding.expr
                        if field_spec.filter_binding is not None
                        else None
                    ),
                )
                field_rows.append(
                    BenchmarkFieldRow(
                        case_id=fixture.case_id,
                        field_id=field_id,
                        stage="candidates",
                        gap_kind=("unexpected_observed" if candidate_set.candidates else None),
                        expected=gold_summary(gold),
                        observed={
                            "metrics": {
                                "correct_negative": not candidate_set.candidates,
                                "false_positive_count": len(candidate_set.candidates),
                            },
                            "candidates": candidate_summaries(candidate_set),
                        },
                    ),
                )
                field_rows.append(
                    BenchmarkFieldRow(
                        case_id=fixture.case_id,
                        field_id=field_id,
                        stage="filtered_candidates",
                        gap_kind=("unexpected_observed" if filtered_set.candidates else None),
                        expected=gold_summary(gold),
                        observed={
                            "metrics": {
                                "correct_negative": not filtered_set.candidates,
                                "false_positive_count": len(filtered_set.candidates),
                                "precision_count": 0,
                                "precision_total": len(filtered_set.candidates),
                                "precision_rate": rate(0, len(filtered_set.candidates)),
                            },
                            "candidates": candidate_summaries(filtered_set),
                            "filter_drops": filter_drops,
                        },
                    ),
                )
                continue

            if not gold.evidence:
                case_status = _worse_status(case_status, "comparability_failed")
                field_rows.append(
                    BenchmarkFieldRow(
                        case_id=fixture.case_id,
                        field_id=field_id,
                        stage="candidates",
                        gap_kind="comparability_failure",
                        expected=gold_summary(gold),
                        attributions=(
                            MissAttribution(
                                stage="comparability",
                                kind="fixture_missing_evidence",
                                field_id=field_id,
                                reason=(
                                    "candidate scoring requires at least one gold "
                                    "evidence item or expected_absent=True"
                                ),
                                details={"fixture_value": gold.expected_values},
                            ),
                        ),
                        message="candidate scoring requires at least one gold evidence item",
                    ),
                )
                continue

            try:
                candidate_set = _generate_candidate_set(field_spec, document_view)
            except Exception as exc:  # noqa: BLE001 - benchmark report owns setup failures.
                case_status = _worse_status(case_status, "setup_failed")
                field_rows.append(
                    BenchmarkFieldRow(
                        case_id=fixture.case_id,
                        field_id=field_id,
                        stage="candidates",
                        gap_kind="setup_failure",
                        expected=gold_summary(gold),
                        attributions=(
                            MissAttribution(
                                stage="candidates",
                                kind="setup_failure",
                                field_id=field_id,
                                reason=f"{type(exc).__name__}: {exc}",
                                details={"error_type": type(exc).__name__},
                            ),
                        ),
                        message=f"{type(exc).__name__}: {exc}",
                    ),
                )
                continue

            filtered_set = (
                apply_filter_binding(candidate_set, field_spec.filter_binding)
                if field_spec.filter_binding is not None
                else candidate_set
            )
            candidate_matches = matches_by_gold(
                gold.evidence,
                candidate_set,
                match_config,
            )
            filtered_matches = matches_by_gold(
                gold.evidence,
                filtered_set,
                match_config,
            )
            candidate_hits = sum(1 for matches in candidate_matches.values() if matches)
            filtered_hits = sum(1 for matches in filtered_matches.values() if matches)
            total_gold = len(gold.evidence)
            recall_candidates_count += candidate_hits
            recall_candidates_total += total_gold
            recall_filtered_count += filtered_hits
            recall_filtered_total += total_gold

            candidate_precision_hits = sum(
                1
                for candidate in candidate_set.candidates
                if _candidate_matches_any(candidate, gold.evidence, match_config)
            )
            filtered_precision_hits = sum(
                1
                for candidate in filtered_set.candidates
                if _candidate_matches_any(candidate, gold.evidence, match_config)
            )
            precision_candidates_count += candidate_precision_hits
            precision_candidates_total += len(candidate_set.candidates)
            precision_filtered_count += filtered_precision_hits
            precision_filtered_total += len(filtered_set.candidates)

            if candidate_hits < total_gold or filtered_hits < total_gold:
                case_status = _worse_status(case_status, "failed")

            filter_drops = _filter_drops(
                raw_set=candidate_set,
                filtered_set=filtered_set,
                expr=(
                    field_spec.filter_binding.expr
                    if field_spec.filter_binding is not None
                    else None
                ),
            )
            field_rows.append(
                BenchmarkFieldRow(
                    case_id=fixture.case_id,
                    field_id=field_id,
                    stage="candidates",
                    gap_kind="missing_expected" if candidate_hits < total_gold else None,
                    expected=gold_summary(gold),
                    attributions=_candidate_stage_attributions(
                        field_id=field_id,
                        gold=gold,
                        candidate_set=candidate_set,
                        matches=candidate_matches,
                        match_config=match_config,
                    ),
                    observed={
                        "metrics": {
                            "recall_count": candidate_hits,
                            "recall_total": total_gold,
                            "recall_rate": rate(candidate_hits, total_gold),
                            "precision_count": candidate_precision_hits,
                            "precision_total": len(candidate_set.candidates),
                            "precision_rate": rate(
                                candidate_precision_hits,
                                len(candidate_set.candidates),
                            ),
                        },
                        "matches": match_summary(candidate_matches),
                        "candidates": candidate_summaries(candidate_set),
                    },
                ),
            )
            field_rows.append(
                BenchmarkFieldRow(
                    case_id=fixture.case_id,
                    field_id=field_id,
                    stage="filtered_candidates",
                    gap_kind="missing_expected" if filtered_hits < total_gold else None,
                    expected=gold_summary(gold),
                    attributions=_filtered_stage_attributions(
                        field_id=field_id,
                        gold=gold,
                        raw_set=candidate_set,
                        candidate_matches=candidate_matches,
                        filtered_matches=filtered_matches,
                        filter_drops=filter_drops,
                    ),
                    observed={
                        "metrics": {
                            "recall_count": filtered_hits,
                            "recall_total": total_gold,
                            "recall_rate": rate(filtered_hits, total_gold),
                            "precision_count": filtered_precision_hits,
                            "precision_total": len(filtered_set.candidates),
                            "precision_rate": rate(
                                filtered_precision_hits,
                                len(filtered_set.candidates),
                            ),
                        },
                        "matches": match_summary(filtered_matches),
                        "candidates": candidate_summaries(filtered_set),
                        "filter_drops": filter_drops,
                    },
                ),
            )

        case_rows.append(BenchmarkCaseRow(case_id=fixture.case_id, status=case_status))

    return BenchmarkReport(
        metadata=ScorerMetadata(
            scorer_name="score_candidates",
            scorer_version=_SCORER_VERSION,
            input_refs=input_refs,
            parameters={
                "span_match": match_config.model_dump(mode="json"),
            },
        ),
        case_rows=tuple(case_rows),
        field_rows=tuple(field_rows),
        aggregates=(
            BenchmarkAggregate(
                name="recall_at_candidates",
                count=recall_candidates_count,
                total=recall_candidates_total,
                rate=rate(recall_candidates_count, recall_candidates_total),
            ),
            BenchmarkAggregate(
                name="recall_at_filtered",
                count=recall_filtered_count,
                total=recall_filtered_total,
                rate=rate(recall_filtered_count, recall_filtered_total),
            ),
            BenchmarkAggregate(
                name="precision_at_candidates",
                count=precision_candidates_count,
                total=precision_candidates_total,
                rate=rate(precision_candidates_count, precision_candidates_total),
            ),
            BenchmarkAggregate(
                name="precision_at_filtered",
                count=precision_filtered_count,
                total=precision_filtered_total,
                rate=rate(precision_filtered_count, precision_filtered_total),
            ),
            BenchmarkAggregate(
                name="true_positive_at_candidates",
                count=recall_candidates_count,
                total=recall_candidates_total,
                rate=rate(recall_candidates_count, recall_candidates_total),
            ),
            BenchmarkAggregate(
                name="false_negative_at_candidates",
                count=recall_candidates_total - recall_candidates_count,
                total=recall_candidates_total,
                rate=rate(
                    recall_candidates_total - recall_candidates_count,
                    recall_candidates_total,
                ),
            ),
            BenchmarkAggregate(
                name="false_positive_at_candidates",
                count=precision_candidates_total - precision_candidates_count,
                total=precision_candidates_total,
                rate=rate(
                    precision_candidates_total - precision_candidates_count,
                    precision_candidates_total,
                ),
            ),
            BenchmarkAggregate(
                name="true_positive_at_filtered",
                count=recall_filtered_count,
                total=recall_filtered_total,
                rate=rate(recall_filtered_count, recall_filtered_total),
            ),
            BenchmarkAggregate(
                name="false_negative_at_filtered",
                count=recall_filtered_total - recall_filtered_count,
                total=recall_filtered_total,
                rate=rate(
                    recall_filtered_total - recall_filtered_count,
                    recall_filtered_total,
                ),
            ),
            BenchmarkAggregate(
                name="false_positive_at_filtered",
                count=precision_filtered_total - precision_filtered_count,
                total=precision_filtered_total,
                rate=rate(
                    precision_filtered_total - precision_filtered_count,
                    precision_filtered_total,
                ),
            ),
        ),
    )


def _generate_candidate_set(field_spec: FieldSpec, document_view: Any) -> CandidateSet:
    if not field_spec.strategy_bindings:
        raise ValueError(f"field {field_spec.field_id!r} has no strategy_bindings")
    candidate_sets = tuple(
        cast("CandidateStrategy", binding.cls()).generate(
            field_spec=field_spec.model_copy(update={"strategy_bindings": (binding,)}),
            document_view=document_view,
            instance_hint=None,
        )
        for binding in field_spec.strategy_bindings
    )
    if len(candidate_sets) == 1:
        return candidate_sets[0]

    candidates: list[Candidate] = []
    seen: set[str] = set()
    for candidate_set in candidate_sets:
        for candidate in candidate_set.candidates:
            key = stable_hash(
                (
                    candidate.text,
                    candidate.source_span.model_dump(mode="json"),
                    tuple(span.model_dump(mode="json") for span in candidate.evidence_spans),
                    candidate.normalized_hint,
                    candidate.source_kind,
                    candidate.entity_type,
                ),
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return build_candidate_set(
        field_id=field_spec.field_id,
        document_id=document_view.document_id,
        candidates=tuple(candidates),
        strategy_id="composite:" + stable_hash([s.strategy_id for s in candidate_sets]),
        instance_hint=None,
    )


def expected_fields_by_id(fixture: BenchmarkFixture) -> dict[str, FieldGoldExpectation]:
    fields: dict[str, list[GoldField]] = {}
    for instance in fixture.expected_instances:
        for field in instance.fields:
            fields.setdefault(field.field_id, []).append(field)
    return {
        field_id: FieldGoldExpectation(
            field_id=field_id,
            expected_values=tuple(field.expected_value for field in grouped),
            evidence=tuple(evidence for field in grouped for evidence in field.evidence),
            expected_absent=any(field.expected_absent for field in grouped),
        )
        for field_id, grouped in fields.items()
    }


def matches_by_gold(
    evidence: tuple[GoldEvidence, ...],
    candidate_set: CandidateSet,
    config: SpanMatchConfig,
) -> dict[int, tuple[_EvidenceMatch, ...]]:
    matches: dict[int, tuple[_EvidenceMatch, ...]] = {}
    for index, gold in enumerate(evidence):
        matched: list[_EvidenceMatch] = []
        for candidate in candidate_set.candidates:
            via = _match_via(gold, candidate, config)
            if via is not None:
                matched.append(
                    _EvidenceMatch(
                        gold_index=index,
                        candidate_id=candidate.candidate_id,
                        candidate_text=candidate.text,
                        strategy_id=candidate.source_id,
                        matched_via=via,
                    ),
                )
        matches[index] = tuple(matched)
    return matches


def _candidate_stage_attributions(
    *,
    field_id: str,
    gold: FieldGoldExpectation,
    candidate_set: CandidateSet,
    matches: Mapping[int, tuple[_EvidenceMatch, ...]],
    match_config: SpanMatchConfig,
) -> tuple[MissAttribution, ...]:
    attributions: list[MissAttribution] = []
    for gold_index, evidence in enumerate(gold.evidence):
        if matches.get(gold_index):
            continue
        near = _near_miss(evidence, candidate_set)
        if near is not None:
            attributions.append(
                MissAttribution(
                    stage="candidates",
                    kind="span_near_miss",
                    field_id=field_id,
                    gold_index=gold_index,
                    candidate_id=near.candidate_id,
                    strategy_id=near.source_id,
                    reason="candidate overlapped expected evidence but did not match config",
                    details={
                        **_gold_details(evidence),
                        **_candidate_details(near),
                        "match_config": match_config.model_dump(mode="json"),
                    },
                ),
            )
            continue
        attributions.append(
            MissAttribution(
                stage="candidates",
                kind="not_generated",
                field_id=field_id,
                gold_index=gold_index,
                reason="expected evidence did not match any generated candidate",
                details=_gold_details(evidence),
            ),
        )
    return tuple(attributions)


def _filtered_stage_attributions(
    *,
    field_id: str,
    gold: FieldGoldExpectation,
    raw_set: CandidateSet,
    candidate_matches: Mapping[int, tuple[_EvidenceMatch, ...]],
    filtered_matches: Mapping[int, tuple[_EvidenceMatch, ...]],
    filter_drops: tuple[dict[str, Any], ...],
) -> tuple[MissAttribution, ...]:
    drops_by_candidate_id = {
        str(drop["candidate"]["candidate_id"]): drop
        for drop in filter_drops
        if isinstance(drop.get("candidate"), Mapping)
        and "candidate_id" in drop["candidate"]
    }
    candidates_by_id = {candidate.candidate_id: candidate for candidate in raw_set.candidates}
    attributions: list[MissAttribution] = []
    for gold_index, evidence in enumerate(gold.evidence):
        if filtered_matches.get(gold_index):
            continue
        raw_matches = candidate_matches.get(gold_index, ())
        for match in raw_matches:
            drop = drops_by_candidate_id.get(match.candidate_id)
            if drop is None:
                continue
            candidate = candidates_by_id.get(match.candidate_id)
            details = {
                **_gold_details(evidence),
                "candidate_text": match.candidate_text,
                "filter_node": drop.get("rejected_by"),
                "filter_reason": drop.get("reason"),
            }
            if candidate is not None:
                details.update(_candidate_details(candidate))
            attributions.append(
                MissAttribution(
                    stage="filtered_candidates",
                    kind="generated_then_filtered",
                    field_id=field_id,
                    gold_index=gold_index,
                    candidate_id=match.candidate_id,
                    strategy_id=match.strategy_id,
                    filter_node=_stringify_json(drop.get("rejected_by")),
                    reason=str(drop.get("reason", "candidate was rejected by filter")),
                    details=details,
                ),
            )
    return tuple(attributions)


def _near_miss(evidence: GoldEvidence, candidate_set: CandidateSet) -> Candidate | None:
    for candidate in candidate_set.candidates:
        if evidence.span is not None and _span_matches(
            evidence.span,
            candidate.source_span,
            SpanMatchConfig(mode="overlap"),
        ):
            return candidate
        if evidence.text is None:
            continue
        expected = evidence.text.casefold()
        candidate_text = candidate.text.casefold()
        if candidate_text and candidate_text in expected:
            return candidate
        if expected and expected in candidate.context.casefold():
            return candidate
    return None


def _candidate_matches_any(
    candidate: Candidate,
    evidence: tuple[GoldEvidence, ...],
    config: SpanMatchConfig,
) -> bool:
    return any(_match_via(gold, candidate, config) is not None for gold in evidence)


def _match_via(
    gold: GoldEvidence,
    candidate: Candidate,
    config: SpanMatchConfig,
) -> _MatchVia | None:
    if gold.span is not None and _span_matches(gold.span, candidate.source_span, config):
        return "span"
    if gold.text is None:
        return None
    expected = gold.text.casefold()
    if expected in candidate.text.casefold():
        return "text"
    candidate_text = candidate.text.casefold()
    if (
        config.allow_context_text_match
        and expected in candidate.context.casefold()
        and candidate_text in expected
    ):
        return "context"
    return None


def _span_matches(gold: SourceSpan, observed: SourceSpan, config: SpanMatchConfig) -> bool:
    if gold.text_anchor_space != observed.text_anchor_space:
        return False
    start = max(gold.byte_start, observed.byte_start)
    end = min(gold.byte_end, observed.byte_end)
    intersection = max(0, end - start)
    if config.mode == "exact":
        return gold.byte_start == observed.byte_start and gold.byte_end == observed.byte_end
    if config.mode == "overlap":
        return intersection > 0
    if config.mode == "contains":
        return gold.byte_start <= observed.byte_start and gold.byte_end >= observed.byte_end
    if config.mode == "contained_by":
        return observed.byte_start <= gold.byte_start and observed.byte_end >= gold.byte_end
    union = max(gold.byte_end, observed.byte_end) - min(gold.byte_start, observed.byte_start)
    return union > 0 and (intersection / union) >= config.min_iou


def _filter_drops(
    *,
    raw_set: CandidateSet,
    filtered_set: CandidateSet,
    expr: FilterExpr | None,
) -> tuple[dict[str, Any], ...]:
    if expr is None:
        return ()
    kept_ids = {candidate.candidate_id for candidate in filtered_set.candidates}
    drops: list[dict[str, Any]] = []
    for candidate in raw_set.candidates:
        if candidate.candidate_id in kept_ids:
            continue
        drops.append(
            {
                "candidate": _candidate_summary(candidate),
                "rejected_by": _expr_summary(expr),
                "reason": _rejection_reason(candidate=candidate, expr=expr, candidate_set=raw_set),
            },
        )
    return tuple(drops)


def _rejection_reason(
    *,
    candidate: Candidate,
    expr: FilterExpr,
    candidate_set: CandidateSet,
) -> str:
    if isinstance(expr, LabelIn):
        if candidate.entity_type not in expr.labels:
            return f"entity_type {candidate.entity_type!r} not in labels={expr.labels!r}"
        return "matched"
    if isinstance(expr, LabelNotIn):
        if candidate.entity_type in expr.labels:
            return f"entity_type {candidate.entity_type!r} is excluded by labels={expr.labels!r}"
        return "matched"
    if isinstance(expr, ContainedBy):
        if not _is_contained_by(candidate, expr.label, candidate_set):
            return f"not contained by label={expr.label!r}"
        return "matched"
    if isinstance(expr, Contains):
        if not _contains(candidate, expr.label, candidate_set):
            return f"does not contain label={expr.label!r}"
        return "matched"
    if isinstance(expr, NumericRange):
        return _numeric_range_reason(candidate, expr)
    if isinstance(expr, ContextContains):
        return _context_reason(candidate, expr)
    if isinstance(expr, And):
        for child in expr.exprs:
            reason = _rejection_reason(candidate=candidate, expr=child, candidate_set=candidate_set)
            if reason != "matched":
                return reason
        return "matched"
    if isinstance(expr, Or):
        if any(
            _expr_matches(candidate=candidate, expr=child, candidate_set=candidate_set)
            for child in expr.exprs
        ):
            return "matched"
        return "no child expression matched"
    if _expr_matches(candidate=candidate, expr=expr.expr, candidate_set=candidate_set):
        return "negated expression matched"
    return "matched"


def _expr_matches(*, candidate: Candidate, expr: FilterExpr, candidate_set: CandidateSet) -> bool:
    return (
        _rejection_reason(candidate=candidate, expr=expr, candidate_set=candidate_set) == "matched"
    )


def _is_contained_by(candidate: Candidate, label: str | None, candidate_set: CandidateSet) -> bool:
    return any(
        _label_matches(other, label) and _span_contains(outer=other, inner=candidate)
        for other in candidate_set.candidates
        if other.candidate_id != candidate.candidate_id
    )


def _contains(candidate: Candidate, label: str | None, candidate_set: CandidateSet) -> bool:
    return any(
        _label_matches(other, label) and _span_contains(outer=candidate, inner=other)
        for other in candidate_set.candidates
        if other.candidate_id != candidate.candidate_id
    )


def _label_matches(candidate: Candidate, label: str | None) -> bool:
    return label is None or candidate.entity_type == label


def _span_contains(*, outer: Candidate, inner: Candidate) -> bool:
    outer_span = outer.source_span
    inner_span = inner.source_span
    if outer_span.source_ref != inner_span.source_ref:
        return False
    if outer_span.text_anchor_space != inner_span.text_anchor_space:
        return False
    return (
        outer_span.byte_start <= inner_span.byte_start
        and outer_span.byte_end >= inner_span.byte_end
        and (
            outer_span.byte_start != inner_span.byte_start
            or outer_span.byte_end != inner_span.byte_end
        )
    )


def _numeric_range_reason(candidate: Candidate, expr: NumericRange) -> str:
    value = _candidate_decimal(candidate)
    if value is None:
        return "candidate is not numeric"
    if expr.lo is not None:
        lo = Decimal(expr.lo)
        if value < lo or (value == lo and not expr.include_lo):
            return f"value {value} below lower bound {expr.lo}"
    if expr.hi is not None:
        hi = Decimal(expr.hi)
        if value > hi or (value == hi and not expr.include_hi):
            return f"value {value} above upper bound {expr.hi}"
    return "matched"


def _candidate_decimal(candidate: Candidate) -> Decimal | None:
    raw = candidate.normalized_hint if candidate.normalized_hint is not None else candidate.text
    try:
        return Decimal(str(raw).strip().replace(",", "").replace("$", "").replace("%", ""))
    except (InvalidOperation, ValueError):
        return None


def _context_reason(candidate: Candidate, expr: ContextContains) -> str:
    haystack = candidate.context if expr.case_sensitive else candidate.context.casefold()
    any_of = expr.any_of if expr.case_sensitive else tuple(s.casefold() for s in expr.any_of)
    all_of = expr.all_of if expr.case_sensitive else tuple(s.casefold() for s in expr.all_of)
    if any_of and not any(needle in haystack for needle in any_of):
        return f"missing any_of={expr.any_of!r}"
    missing_all = tuple(
        original
        for original, needle in zip(expr.all_of, all_of, strict=True)
        if needle not in haystack
    )
    if missing_all:
        return f"missing all_of={missing_all!r}"
    return "matched"


def candidate_summaries(candidate_set: CandidateSet) -> tuple[dict[str, Any], ...]:
    return tuple(_candidate_summary(candidate) for candidate in candidate_set.candidates)


def _candidate_summary(candidate: Candidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "text": candidate.text,
        "strategy_id": candidate.source_id,
        "entity_type": candidate.entity_type,
        "source_span": candidate.source_span.model_dump(mode="json"),
    }


def _candidate_details(candidate: Candidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "candidate_text": candidate.text,
        "candidate_span": candidate.source_span.model_dump(mode="json"),
        "strategy_id": candidate.source_id,
        "source_context": candidate.context,
    }


def _gold_details(evidence: GoldEvidence) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if evidence.text is not None:
        details["gold_text"] = evidence.text
    if evidence.span is not None:
        details["gold_span"] = evidence.span.model_dump(mode="json")
    return details


def _stringify_json(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def gold_summary(gold: FieldGoldExpectation) -> dict[str, Any]:
    return {
        "field_id": gold.field_id,
        "expected_values": gold.expected_values,
        "evidence": tuple(evidence.model_dump(mode="json") for evidence in gold.evidence),
    }


def match_summary(matches: Mapping[int, tuple[_EvidenceMatch, ...]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "gold_index": gold_index,
            "matches": tuple(match.model_dump(mode="json") for match in matched),
        }
        for gold_index, matched in sorted(matches.items())
    )


def _expr_summary(expr: FilterExpr) -> dict[str, Any]:
    return cast("BaseModel", expr).model_dump(mode="json")


def rate(count: int, total: int) -> float | None:
    if total == 0:
        return None
    return count / total


def _worse_status(
    current: Literal["passed", "failed", "setup_failed", "comparability_failed"],
    new: Literal["passed", "failed", "setup_failed", "comparability_failed"],
) -> Literal["passed", "failed", "setup_failed", "comparability_failed"]:
    priority = {
        "passed": 0,
        "failed": 1,
        "comparability_failed": 2,
        "setup_failed": 3,
    }
    return new if priority[new] > priority[current] else current
