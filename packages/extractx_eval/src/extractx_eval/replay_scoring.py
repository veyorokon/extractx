"""Deterministic replay-artifact benchmark scoring."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, Protocol

from extractx.core.objects import Observation
from extractx.core.outcomes import Evidence, NegativeOutcome, ValidatedField
from extractx.replay.artifact import ReplayArtifact

from .candidate_scoring import (
    FieldGoldExpectation,
    SpanMatchConfig,
    candidate_summaries,
    expected_fields_by_id,
    gold_summary,
    match_summary,
    matches_by_gold,
    rate,
)
from .fixtures import BenchmarkFixture, FixturePack, GoldEvidence
from .reports import (
    BenchmarkAggregate,
    BenchmarkCaseRow,
    BenchmarkFieldRow,
    BenchmarkReport,
    MissAttribution,
    ScorerMetadata,
)
from .scoring import json_safe

__all__ = ["score_replay"]

_SCORER_VERSION = "replay_scoring.v1"


class _EvidenceMatchLike(Protocol):
    candidate_id: str


def score_replay(
    replays: ReplayArtifact | Mapping[str, ReplayArtifact],
    fixtures: FixturePack | Iterable[BenchmarkFixture],
    *,
    span_match: SpanMatchConfig | None = None,
) -> BenchmarkReport:
    """Score stored replay artifacts against benchmark fixtures.

    This scorer is deterministic and does not re-execute extraction. Replay
    artifacts currently carry post-filter candidate sets, so the candidate
    stage here starts at `filtered_candidates`.
    """

    match_config = span_match or SpanMatchConfig()
    fixture_rows = tuple(fixtures.fixtures if isinstance(fixtures, FixturePack) else fixtures)
    replay_by_case = _replay_mapping(replays, fixture_rows)
    input_refs = (fixtures.pack_id,) if isinstance(fixtures, FixturePack) else ()

    case_rows: list[BenchmarkCaseRow] = []
    field_rows: list[BenchmarkFieldRow] = []
    candidate_hits = candidate_total = 0
    selection_hits = selection_total = 0
    validation_hits = validation_total = 0
    materialization_hits = materialization_total = 0
    materialization_value_hits = materialization_value_total = 0

    for fixture in fixture_rows:
        artifact = replay_by_case.get(fixture.case_id)
        if artifact is None:
            case_rows.append(
                BenchmarkCaseRow(
                    case_id=fixture.case_id,
                    status="comparability_failed",
                    message=f"no replay artifact supplied for case_id={fixture.case_id!r}",
                ),
            )
            continue

        case_status: Literal["passed", "failed", "setup_failed", "comparability_failed"] = "passed"
        expected_fields = expected_fields_by_id(fixture)
        candidate_sets = {
            candidate_set.field_id: candidate_set
            for candidate_set in artifact.candidate_sets
        }
        observations = _observations_by_field(artifact.observations)
        validated = _validated_by_field(artifact.validated_fields)
        evidence = _evidence_by_field(
            tuple(e for instance in artifact.final_instances for e in instance.evidence),
        )
        negatives = _negatives_by_field(
            (
                *artifact.pre_resolver_negatives,
                *artifact.trace.events,
                *(n for instance in artifact.final_instances for n in instance.negative_outcomes),
            ),
        )

        for field_id, gold in expected_fields.items():
            candidate_set = candidate_sets.get(field_id)
            if candidate_set is None:
                case_status = _status(case_status, "comparability_failed")
                field_rows.append(
                    BenchmarkFieldRow(
                        case_id=fixture.case_id,
                        field_id=field_id,
                        stage="filtered_candidates",
                        gap_kind="comparability_failure",
                        expected=gold_summary(gold),
                        replay_artifact_ref=fixture.case_id,
                        producer_versions=dict(artifact.producer_versions),
                        attributions=(
                            MissAttribution(
                                stage="comparability",
                                kind="fixture_schema_mismatch",
                                field_id=field_id,
                                reason=f"replay has no candidate set for field {field_id!r}",
                                details={"fixture_value": gold.expected_values},
                            ),
                        ),
                        message=f"replay has no candidate set for field {field_id!r}",
                    ),
                )
                continue

            if gold.expected_absent:
                if candidate_set.candidates:
                    case_status = _status(case_status, "failed")
                field_rows.append(
                    BenchmarkFieldRow(
                        case_id=fixture.case_id,
                        field_id=field_id,
                        stage="filtered_candidates",
                        gap_kind="unexpected_observed" if candidate_set.candidates else None,
                        expected=gold_summary(gold),
                        observed={
                            "false_positive_count": len(candidate_set.candidates),
                            "candidates": candidate_summaries(candidate_set),
                        },
                        producer_versions=dict(artifact.producer_versions),
                    ),
                )
                continue

            if not gold.evidence:
                case_status = _status(case_status, "comparability_failed")
                field_rows.append(
                    BenchmarkFieldRow(
                        case_id=fixture.case_id,
                        field_id=field_id,
                        stage="filtered_candidates",
                        gap_kind="comparability_failure",
                        expected=gold_summary(gold),
                        attributions=(
                            MissAttribution(
                                stage="comparability",
                                kind="fixture_missing_evidence",
                                field_id=field_id,
                                reason=(
                                    "replay scoring requires gold evidence or "
                                    "expected_absent=True"
                                ),
                                details={"fixture_value": gold.expected_values},
                            ),
                        ),
                        message="replay scoring requires gold evidence or expected_absent=True",
                    ),
                )
                continue

            matches = matches_by_gold(gold.evidence, candidate_set, match_config)
            field_candidate_hits = sum(1 for m in matches.values() if m)
            candidate_hits += field_candidate_hits
            candidate_total += len(gold.evidence)
            if field_candidate_hits < len(gold.evidence):
                case_status = _status(case_status, "failed")
            field_rows.append(
                BenchmarkFieldRow(
                    case_id=fixture.case_id,
                    field_id=field_id,
                    stage="filtered_candidates",
                    gap_kind=(
                        "missing_expected"
                        if field_candidate_hits < len(gold.evidence)
                        else None
                    ),
                    expected=gold_summary(gold),
                    attributions=_replay_candidate_attributions(
                        field_id=field_id,
                        gold=gold,
                        matches=matches,
                    ),
                    observed={
                        "matches": match_summary(matches),
                        "candidates": candidate_summaries(candidate_set),
                    },
                    producer_versions=dict(artifact.producer_versions),
                ),
            )

            matched_candidate_ids = {
                match.candidate_id for matched in matches.values() for match in matched
            }
            field_observations = observations.get(field_id, ())
            selected_ids = {
                candidate_id
                for observation in field_observations
                for candidate_id in observation.selected_candidate_ids
            }
            selected_gold_ids = matched_candidate_ids & selected_ids
            field_selection_hit = bool(selected_gold_ids)
            selection_hits += int(field_selection_hit)
            selection_total += 1
            if not field_selection_hit:
                case_status = _status(case_status, "failed")
            selection_attributions = _selection_attributions(
                field_id=field_id,
                gold=gold,
                matches=matches,
                observations=field_observations,
                matched_candidate_ids=matched_candidate_ids,
                selected_ids=selected_ids,
            )
            field_rows.append(
                BenchmarkFieldRow(
                    case_id=fixture.case_id,
                    field_id=field_id,
                    stage="selection",
                    gap_kind=None if field_selection_hit else "missing_expected",
                    expected={"candidate_ids": tuple(sorted(matched_candidate_ids))},
                    observed={
                        "observations": tuple(
                            o.model_dump(mode="json") for o in field_observations
                        ),
                        "selected_candidate_ids": tuple(sorted(selected_ids)),
                    },
                    producer_versions=dict(artifact.producer_versions),
                    attributions=selection_attributions,
                    message=_selection_message(field_observations, matched_candidate_ids),
                ),
            )

            field_validated = validated.get(field_id, ())
            validated_gold = tuple(
                item
                for item in field_validated
                if set(item.proposed.candidate_id_refs) & selected_gold_ids
            )
            validation_hit = bool(validated_gold)
            validation_hits += int(validation_hit)
            validation_total += 1
            if not validation_hit:
                case_status = _status(case_status, "failed")
            validation_attributions = _validation_attributions(
                field_id=field_id,
                gold=gold,
                selected_gold_ids=selected_gold_ids,
                field_validated=field_validated,
                negatives=negatives.get(field_id, ()),
            )
            field_rows.append(
                BenchmarkFieldRow(
                    case_id=fixture.case_id,
                    field_id=field_id,
                    stage="validation",
                    gap_kind=None if validation_hit else "missing_expected",
                    expected=_expected_value_summary(gold.expected_values),
                    observed={
                        "validated": tuple(_validated_summary(item) for item in field_validated),
                        "negative_outcomes": tuple(
                            n.model_dump(mode="json") for n in negatives.get(field_id, ())
                        ),
                    },
                    producer_versions=dict(artifact.producer_versions),
                    attributions=validation_attributions,
                ),
            )

            field_evidence = evidence.get(field_id, ())
            materialized_gold = tuple(
                item
                for item in field_evidence
                if set(item.proposal_provenance.candidate_id_refs) & selected_gold_ids
            )
            materialization_hit = bool(materialized_gold)
            materialization_hits += int(materialization_hit)
            materialization_total += 1
            value_hit, value_total = _value_hits(gold.expected_values, field_evidence)
            materialization_value_hits += value_hit
            materialization_value_total += value_total
            if not materialization_hit or value_hit < value_total:
                case_status = _status(case_status, "failed")
            materialization_attributions = _materialization_attributions(
                field_id=field_id,
                expected_values=gold.expected_values,
                selected_gold_ids=selected_gold_ids,
                validation_hit=validation_hit,
                materialization_hit=materialization_hit,
                field_evidence=field_evidence,
            )
            field_rows.append(
                BenchmarkFieldRow(
                    case_id=fixture.case_id,
                    field_id=field_id,
                    stage="materialization",
                    gap_kind=_materialization_gap(materialization_hit, value_hit, value_total),
                    expected=_expected_value_summary(gold.expected_values),
                    observed={
                        "evidence": tuple(_evidence_summary(item) for item in field_evidence),
                    },
                    producer_versions=dict(artifact.producer_versions),
                    attributions=materialization_attributions,
                ),
            )

        case_rows.append(
            BenchmarkCaseRow(
                case_id=fixture.case_id,
                status=case_status,
                producer_versions=dict(artifact.producer_versions),
            ),
        )

    return BenchmarkReport(
        metadata=ScorerMetadata(
            scorer_name="score_replay",
            scorer_version=_SCORER_VERSION,
            input_refs=input_refs,
            parameters={"span_match": match_config.model_dump(mode="json")},
        ),
        case_rows=tuple(case_rows),
        field_rows=tuple(field_rows),
        aggregates=(
            BenchmarkAggregate(
                name="recall_at_replay_candidates",
                count=candidate_hits,
                total=candidate_total,
                rate=rate(candidate_hits, candidate_total),
            ),
            BenchmarkAggregate(
                name="recall_at_selection",
                count=selection_hits,
                total=selection_total,
                rate=rate(selection_hits, selection_total),
            ),
            BenchmarkAggregate(
                name="recall_at_validation",
                count=validation_hits,
                total=validation_total,
                rate=rate(validation_hits, validation_total),
            ),
            BenchmarkAggregate(
                name="recall_at_materialization",
                count=materialization_hits,
                total=materialization_total,
                rate=rate(materialization_hits, materialization_total),
            ),
            BenchmarkAggregate(
                name="value_accuracy_at_materialization",
                count=materialization_value_hits,
                total=materialization_value_total,
                rate=rate(materialization_value_hits, materialization_value_total),
            ),
        ),
    )


def _replay_mapping(
    replays: ReplayArtifact | Mapping[str, ReplayArtifact],
    fixtures: tuple[BenchmarkFixture, ...],
) -> Mapping[str, ReplayArtifact]:
    if isinstance(replays, Mapping):
        return replays
    if len(fixtures) != 1:
        return {}
    return {fixtures[0].case_id: replays}


def _observations_by_field(
    observations: tuple[Observation, ...],
) -> dict[str, tuple[Observation, ...]]:
    grouped: dict[str, list[Observation]] = {}
    for observation in observations:
        if observation.field_id is None:
            continue
        grouped.setdefault(observation.field_id, []).append(observation)
    return {field_id: tuple(items) for field_id, items in grouped.items()}


def _validated_by_field(
    validated: tuple[ValidatedField, ...],
) -> dict[str, tuple[ValidatedField, ...]]:
    grouped: dict[str, list[ValidatedField]] = {}
    for item in validated:
        grouped.setdefault(item.proposed.field_id, []).append(item)
    return {field_id: tuple(items) for field_id, items in grouped.items()}


def _evidence_by_field(evidence: tuple[Evidence, ...]) -> dict[str, tuple[Evidence, ...]]:
    grouped: dict[str, list[Evidence]] = {}
    for item in evidence:
        grouped.setdefault(item.field_id, []).append(item)
    return {field_id: tuple(items) for field_id, items in grouped.items()}


def _negatives_by_field(
    negatives: tuple[NegativeOutcome, ...],
) -> dict[str, tuple[NegativeOutcome, ...]]:
    grouped: dict[str, list[NegativeOutcome]] = {}
    for negative in negatives:
        if negative.field_id is None:
            continue
        grouped.setdefault(negative.field_id, []).append(negative)
    return {field_id: tuple(items) for field_id, items in grouped.items()}


def _replay_candidate_attributions(
    *,
    field_id: str,
    gold: FieldGoldExpectation,
    matches: Mapping[int, tuple[_EvidenceMatchLike, ...]],
) -> tuple[MissAttribution, ...]:
    attributions: list[MissAttribution] = []
    for gold_index, evidence in enumerate(gold.evidence):
        if matches.get(gold_index):
            continue
        attributions.append(
            MissAttribution(
                stage="filtered_candidates",
                kind="not_generated",
                field_id=field_id,
                gold_index=gold_index,
                reason="expected evidence was not present in replay candidate set",
                details=_gold_details(evidence),
            ),
        )
    return tuple(attributions)


def _selection_attributions(
    *,
    field_id: str,
    gold: FieldGoldExpectation,
    matches: Mapping[int, tuple[_EvidenceMatchLike, ...]],
    observations: tuple[Observation, ...],
    matched_candidate_ids: set[str],
    selected_ids: set[str],
) -> tuple[MissAttribution, ...]:
    if not matched_candidate_ids or matched_candidate_ids & selected_ids:
        return ()
    selected_id = next(iter(sorted(selected_ids)), None)
    selected_text = _selected_candidate_text(observations, selected_id)
    kind = (
        "selection_abstained"
        if any(o.abstain for o in observations)
        else "wrong_candidate_selected"
    )
    reason = (
        "selector abstained"
        if kind == "selection_abstained"
        else "selector chose a non-gold candidate"
    )
    attributions: list[MissAttribution] = []
    for gold_index, evidence in enumerate(gold.evidence):
        if not matches.get(gold_index):
            continue
        attributions.append(
            MissAttribution(
                stage="selection",
                kind=kind,
                field_id=field_id,
                gold_index=gold_index,
                candidate_id=selected_id,
                reason=reason,
                details={
                    **_gold_details(evidence),
                    "selected_candidate_id": selected_id,
                    "selected_candidate_text": selected_text,
                    "expected_candidate_ids": tuple(sorted(matched_candidate_ids)),
                },
            ),
        )
    return tuple(attributions)


def _validation_attributions(
    *,
    field_id: str,
    gold: FieldGoldExpectation,
    selected_gold_ids: set[str],
    field_validated: tuple[ValidatedField, ...],
    negatives: tuple[NegativeOutcome, ...],
) -> tuple[MissAttribution, ...]:
    if not selected_gold_ids:
        return ()
    if any(set(item.proposed.candidate_id_refs) & selected_gold_ids for item in field_validated):
        return ()
    negative = negatives[0] if negatives else None
    return (
        MissAttribution(
            stage="validation",
            kind="validation_rejected",
            field_id=field_id,
            candidate_id=next(iter(sorted(selected_gold_ids))),
            reason=(
                negative.reason
                if negative is not None
                else "selected evidence failed validation"
            ),
            details={
                "candidate_id": next(iter(sorted(selected_gold_ids))),
                "fixture_value": gold.expected_values,
                "validation_code": negative.code if negative is not None else None,
                "negative_outcomes": tuple(n.model_dump(mode="json") for n in negatives),
            },
        ),
    )


def _materialization_attributions(
    *,
    field_id: str,
    expected_values: tuple[Any, ...],
    selected_gold_ids: set[str],
    validation_hit: bool,
    materialization_hit: bool,
    field_evidence: tuple[Evidence, ...],
) -> tuple[MissAttribution, ...]:
    if not validation_hit:
        return ()
    attributions: list[MissAttribution] = []
    if not materialization_hit:
        attributions.append(
            MissAttribution(
                stage="materialization",
                kind="materialization_missing",
                field_id=field_id,
                candidate_id=next(iter(sorted(selected_gold_ids)), None),
                reason="validated evidence did not appear in final instances",
                details={
                    "candidate_id": next(iter(sorted(selected_gold_ids)), None),
                    "fixture_value": expected_values,
                },
            ),
        )
    expected = tuple(value for value in expected_values if value is not None)
    if expected:
        observed_values = tuple(item.normalized_value for item in field_evidence)
        observed_comparison_values = tuple(json_safe(value) for value in observed_values)
        missing = tuple(
            value
            for value in expected
            if json_safe(value) not in observed_comparison_values
        )
        for value in missing:
            attributions.append(
                MissAttribution(
                    stage="normalization",
                    kind="normalization_mismatch",
                    field_id=field_id,
                    reason="materialized normalized value did not match fixture value",
                    details={
                        "fixture_value": value,
                        "normalized_value": observed_values,
                    },
                ),
            )
    return tuple(attributions)


def _gold_details(evidence: GoldEvidence) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if evidence.text is not None:
        details["gold_text"] = evidence.text
    if evidence.span is not None:
        details["gold_span"] = evidence.span.model_dump(mode="json")
    return details


def _selected_candidate_text(
    observations: tuple[Observation, ...],
    selected_id: str | None,
) -> str | None:
    # Replay observations carry selected ids, not candidate text. The selected
    # id is still enough for deterministic joins against the candidate row.
    del observations
    del selected_id
    return None


def _selection_message(
    observations: tuple[Observation, ...],
    matched_candidate_ids: set[str],
) -> str | None:
    if not observations:
        return "no observation for field"
    if not matched_candidate_ids:
        return "expected evidence was not present in replay candidate set"
    if any(observation.abstain for observation in observations):
        return "selector abstained"
    if any(observation.outcome == "NO_CANDIDATES" for observation in observations):
        return "selector saw no candidates"
    if any(observation.outcome == "AMBIGUOUS" for observation in observations):
        return "selector returned ambiguous outcome"
    return None


def _validated_summary(item: ValidatedField) -> dict[str, Any]:
    return {
        "field_id": item.proposed.field_id,
        "candidate_id_refs": item.proposed.candidate_id_refs,
        "raw_value": item.proposed.raw_value,
        "normalized_value": item.normalized_value,
        "field_validation_version": item.field_validation_version,
    }


def _evidence_summary(item: Evidence) -> dict[str, Any]:
    return {
        "field_id": item.field_id,
        "instance_id": item.instance_id,
        "candidate_id_refs": item.proposal_provenance.candidate_id_refs,
        "raw_value": item.raw_value,
        "evidence_text": item.evidence_text,
        "normalized_value": item.normalized_value,
    }


def _expected_value_summary(values: tuple[Any, ...]) -> dict[str, Any]:
    return {"expected_values": values}


def _value_hits(
    expected_values: tuple[Any, ...],
    evidence: tuple[Evidence, ...],
) -> tuple[int, int]:
    expected = tuple(value for value in expected_values if value is not None)
    if not expected:
        return 0, 0
    observed = tuple(json_safe(item.normalized_value) for item in evidence)
    return sum(1 for value in expected if json_safe(value) in observed), len(expected)


def _materialization_gap(
    materialization_hit: bool,
    value_hit: int,
    value_total: int,
) -> Literal["missing_expected", "value_mismatch"] | None:
    if not materialization_hit:
        return "missing_expected"
    if value_hit < value_total:
        return "value_mismatch"
    return None


def _status(
    current: Literal["passed", "failed", "setup_failed", "comparability_failed"],
    new: Literal["passed", "failed", "setup_failed", "comparability_failed"],
) -> Literal["passed", "failed", "setup_failed", "comparability_failed"]:
    priority = {"passed": 0, "failed": 1, "comparability_failed": 2, "setup_failed": 3}
    return new if priority[new] > priority[current] else current
