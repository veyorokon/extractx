"""phase-1 deterministic `InstanceResolver` per docs/architecture.md §7 seam G.resolver.

see §7 seam G.resolver, §9 canonical objects (`InstanceGroupingKey`,
`Instance`, `Evidence`, `GroupingEvidence`,
`ProposalProvenance`), §11 independent + iterative pseudocode, §17
proof table entries for G.resolver, plus ADR-0003 (no validator
invocation inside the resolver; layer 3 stays out) and ADR-0006
(`group_anchors` may carry mixed `text_anchor_space`s across a group
— the resolver does not coerce across spaces).

phase-1 resolver policy (fixed):

- when `instance_plan` is present, start from `instance_plan.tentative_keys`
  in plan order as the initial tentative buckets.
- when `instance_plan` is absent and `validated_fields` is non-empty,
  synthesize exactly one document-scope tentative bucket whose
  `group_anchors` are the stable ordered unique
  `ValidatedField.proposed.source_span`s for the run.
- when `validated_fields` is empty, emit `()`.
- every field is assigned to at most one final bucket under the
  documented precedence rule. authorities 1–4 in order: explicit
  `GroupingBinding(role="boundary_defining")` match on tentative key
  > source-anchor continuity (single overlapping bucket) > candidate
  co-occurrence (minimum byte gap across the matching `CandidateSet`'s
  referenced candidate spans) > `InstancePlan` priors (lowest
  authority; uses the validated field's tentative_instance_key as the
  prior and falls back to tentative-key order).
- if the four authorities leave a field ambiguous, the resolver emits
  `NegativeOutcome(category="resolution", code="ambiguous_grouping",
  ...)` on the tentative bucket with the strongest partial signal and
  drops the proposal — it does not invent a winner.
- resolution-stage cardinality:
  - `Cardinality.ONE` spread across multiple final instances → one
    `NegativeOutcome(category="resolution",
    code="cardinality.one_multiple_instances", ...)` per affected
    (field, final-instance) pair; affected proposals are dropped.
  - `Cardinality.PER_INSTANCE` with multiple surviving proposals for
    the same field in the same final instance → one
    `NegativeOutcome(category="resolution",
    code="cardinality.per_instance_multi_in_instance", ...)` per
    affected (field, final-instance) pair; affected proposals are
    dropped.
  - phase-1 does not invent `cardinality.optional_*` or
    `cardinality.many_*` codes.
- instances that resolve to zero proposals are dropped from the
  output.
- each remaining instance emits:
  - final `InstanceGroupingKey` (anchors policy below)
  - outcome (`complete` iff `negative_outcomes == ()`, else
    `partial`)
  - `evidence` — `Evidence` promotions without
    mutation
  - `negative_outcomes` — typed ambiguity / cardinality negatives on
    that instance
  - `grouping_evidence` — `GroupingEvidence(stage="resolved", ...)`
    whose `anchor_spans` equal the final `group_anchors`

final `InstanceGroupingKey.group_anchors` policy:

- if one or more surviving proposals in the instance come from
  `boundary_defining` fields, anchors are those proposals' source
  spans in stable (field declaration, proposal order) order.
- else if the tentative bucket had anchors, those anchors are
  carried forward.
- else anchors are the stable ordered unique `source_span`s of the
  surviving proposals in that instance.

`InstanceGroupingKey.group_id` is a deterministic hash over
`(group_anchors, group_key_material)` where
`group_key_material = ("resolved", final_ordinal)`. the static
`"resolved"` tag ensures any future resolver variant that computes a
different material tuple automatically produces a different
`group_id`, mirroring the seam-G.planner pattern.

`producer_version` composition follows the seam-C / seam-D / seam-F /
seam-G.planner pattern: `stable_hash(
"{DeterministicInstanceResolver.__module__}.{qualname}")` fed to
`algorithmic_producer_version(...)` → `code:{code_hash}`. no model
id, no prompt-template hash, no timestamp.
"""

from __future__ import annotations

from typing import Any, cast

from extractx.core.anchors import SourceSpan
from extractx.core.cardinality import Cardinality
from extractx.core.objects import (
    CandidateSet,
    ExtractionSpec,
    FieldSpec,
    GroupingDiscriminator,
    GroupingEvidence,
    InstanceGroupingKey,
    InstancePlan,
)
from extractx.core.outcomes import (
    Evidence,
    Instance,
    NegativeOutcome,
    ProposalProvenance,
    ValidatedField,
)
from extractx.core.versions import algorithmic_producer_version, stable_hash

from ..precedence import (
    boundary_defining_bucket,
    candidate_cooccurrence_buckets,
    source_anchor_continuity_buckets,
)

__all__ = [
    "DeterministicInstanceResolver",
    "InstanceResolverContractError",
    "algorithmic_code_hash",
]


class InstanceResolverContractError(ValueError):
    """raised when a seam-G.resolver input violates a structural invariant.

    this is an implementation-defect failure, not a typed
    `NegativeOutcome`. it fires when:

    - a `CandidateSet.field_id` is not declared in `spec.fields` —
      seam C or the calling strategy fabricated an unknown field;
    - a `ValidatedField.proposed.field_id` is not declared in
      `spec.fields` — seam D / seam E / seam F produced a proposal
      for a field the spec does not know about.

    mirrors the `SelectionAdapterContractError` /
    `ProposalValidatorContractError` / `BoundaryHelperContractError`
    shape: a local `ValueError` subtype, not a widened public exception
    surface. callers inside the execution substrate wrap these into
    diagnostics; direct callers should treat this as a programmer
    error.
    """


# clustering-signal authority labels; carried inside
# `GroupingEvidence.clustering_signals` so downstream diagnostics can
# tell which authority decided each instance without parsing prose.
_AUTHORITY_BOUNDARY = "boundary_defining"
_AUTHORITY_CONTINUITY = "source_anchor_continuity"
_AUTHORITY_COOCCURRENCE = "candidate_cooccurrence"
_AUTHORITY_PLAN_PRIOR = "instance_plan_prior"


class DeterministicInstanceResolver:
    """deterministic algorithmic `InstanceResolver` per phase-1 policy.

    structural `InstanceResolver` subtype — no base class required.
    the class deliberately holds no configurable state: identity is
    carried by `producer_version`, which is composed from the class's
    qualname so any subclass with different behavior produces a
    different `producer_version` automatically.
    """

    @property
    def producer_version(self) -> str:
        """the `code:{code_hash}` string attached to every emitted `GroupingEvidence`."""

        return algorithmic_code_hash()

    def resolve(
        self,
        validated_fields: tuple[ValidatedField, ...],
        candidate_sets: tuple[CandidateSet, ...],
        spec: ExtractionSpec,
        instance_plan: InstancePlan | None = None,
    ) -> tuple[Instance, ...]:
        """run the phase-1 deterministic resolver on the given inputs.

        see module docstring and docs/architecture.md §7 seam
        G.resolver for the full policy; the dispatch is intentionally
        narrow.
        """

        if not validated_fields:
            return ()

        producer_version = self.producer_version

        # structural input checks — seam violations surface loudly.
        field_by_id = _index_fields(spec)
        _enforce_structural_invariants(
            validated_fields=validated_fields,
            candidate_sets=candidate_sets,
            field_by_id=field_by_id,
        )

        # build the initial tentative buckets.
        tentative_keys, used_plan = _initial_tentative_buckets(
            validated_fields=validated_fields,
            instance_plan=instance_plan,
        )

        # per-field authority resolution.
        assignments, ambiguities = _assign_fields(
            tentative_keys=tentative_keys,
            validated_fields=validated_fields,
            candidate_sets=candidate_sets,
            field_by_id=field_by_id,
            used_plan=used_plan,
        )

        # apply resolution-stage cardinality policy; this may drop
        # proposals and attach `cardinality.*` negatives.
        survivors, cardinality_negatives = _apply_cardinality_policy(
            assignments=assignments,
            field_by_id=field_by_id,
            tentative_keys=tentative_keys,
        )

        # compose final `Instance`s.
        return _compose_results(
            tentative_keys=tentative_keys,
            survivors=survivors,
            ambiguities=ambiguities,
            cardinality_negatives=cardinality_negatives,
            field_by_id=field_by_id,
            producer_version=producer_version,
            used_plan=used_plan,
        )


def algorithmic_code_hash() -> str:
    """return the phase-1 resolver's `producer_version` string.

    mirrors the pattern used by seams C / D / F / G.planner: the
    `code_hash` is composed from the class's fully-qualified name so
    any subclass with different behavior produces a different
    `producer_version` automatically.
    """

    digest = stable_hash(
        f"{DeterministicInstanceResolver.__module__}.{DeterministicInstanceResolver.__qualname__}",
    )
    return algorithmic_producer_version(digest)


# ---------------------------------------------------------------------------
# internal data classes (module-private; not part of the public surface)
# ---------------------------------------------------------------------------


class _Assignment:
    """one validated field's tentative final bucket + the authority that decided it.

    carried as a small mutable internal record through the resolver
    pipeline; never escapes the module.
    """

    __slots__ = ("bucket_index", "authority", "validated")

    def __init__(
        self,
        *,
        bucket_index: int,
        authority: str,
        validated: ValidatedField,
    ) -> None:
        self.bucket_index: int = bucket_index
        self.authority: str = authority
        self.validated: ValidatedField = validated


class _AmbiguityNegative:
    """one `ambiguous_grouping` finding; attached to a tentative bucket.

    the `tentative_bucket_index` points at the bucket with the
    strongest partial signal from authorities 1–4; deterministic
    tie-break via tentative-key order.
    """

    __slots__ = ("tentative_bucket_index", "validated", "partial_signal_authority")

    def __init__(
        self,
        *,
        tentative_bucket_index: int,
        validated: ValidatedField,
        partial_signal_authority: str | None,
    ) -> None:
        self.tentative_bucket_index: int = tentative_bucket_index
        self.validated: ValidatedField = validated
        self.partial_signal_authority: str | None = partial_signal_authority


class _CardinalityNegative:
    """one resolution-stage cardinality finding; attached to a tentative bucket.

    `code` is drawn from the seam-G.resolver phase-1 code set:
    `"cardinality.one_multiple_instances"` or
    `"cardinality.per_instance_multi_in_instance"`.
    """

    __slots__ = ("tentative_bucket_index", "field_id", "code", "validated")

    def __init__(
        self,
        *,
        tentative_bucket_index: int,
        field_id: str,
        code: str,
        validated: ValidatedField,
    ) -> None:
        self.tentative_bucket_index: int = tentative_bucket_index
        self.field_id: str = field_id
        self.code: str = code
        self.validated: ValidatedField = validated


# ---------------------------------------------------------------------------
# structural invariant checks
# ---------------------------------------------------------------------------


def _index_fields(spec: ExtractionSpec) -> dict[str, tuple[int, FieldSpec]]:
    """build a `field_id -> (declaration_index, FieldSpec)` dict.

    the declaration index drives stable ordering wherever the resolver
    needs to break ties by spec declaration order.
    """

    out: dict[str, tuple[int, FieldSpec]] = {}
    for index, field in enumerate(spec.fields):
        out[field.field_id] = (index, field)
    return out


def _enforce_structural_invariants(
    *,
    validated_fields: tuple[ValidatedField, ...],
    candidate_sets: tuple[CandidateSet, ...],
    field_by_id: dict[str, tuple[int, FieldSpec]],
) -> None:
    """fail loudly on resolver structural violations.

    see `InstanceResolverContractError` for the rules enforced.
    """

    for validated in validated_fields:
        if validated.proposed.field_id not in field_by_id:
            raise InstanceResolverContractError(
                "DeterministicInstanceResolver: ValidatedField.proposed.field_id "
                f"{validated.proposed.field_id!r} is not declared in spec.fields",
            )

    for candidate_set in candidate_sets:
        if candidate_set.field_id not in field_by_id:
            raise InstanceResolverContractError(
                "DeterministicInstanceResolver: CandidateSet.field_id "
                f"{candidate_set.field_id!r} is not declared in spec.fields",
            )


# ---------------------------------------------------------------------------
# initial tentative buckets
# ---------------------------------------------------------------------------


def _initial_tentative_buckets(
    *,
    validated_fields: tuple[ValidatedField, ...],
    instance_plan: InstancePlan | None,
) -> tuple[tuple[InstanceGroupingKey, ...], bool]:
    """return the initial tentative buckets and whether they came from a plan.

    if `instance_plan` is not None, its `tentative_keys` are used
    verbatim and `used_plan=True`. otherwise one document-scope
    tentative bucket is synthesized from the ordered unique source
    spans of the validated fields, with `used_plan=False`.
    """

    if instance_plan is not None:
        return instance_plan.tentative_keys, True

    anchors = _ordered_unique_spans(
        tuple(validated.proposed.source_span for validated in validated_fields),
    )
    synthetic_material: tuple[object, ...] = ("document_scope", 0)
    synthetic_key = InstanceGroupingKey(
        group_id=_compute_group_id(
            group_anchors=anchors,
            group_key_material=synthetic_material,
        ),
        ordinal=0,
        group_anchors=anchors,
    )
    return (synthetic_key,), False


def _ordered_unique_spans(spans: tuple[SourceSpan, ...]) -> tuple[SourceSpan, ...]:
    """dedup `spans` while preserving input order (pydantic equality)."""

    seen: list[SourceSpan] = []
    out: list[SourceSpan] = []
    for span in spans:
        if any(span == existing for existing in seen):
            continue
        seen.append(span)
        out.append(span)
    return tuple(out)


# ---------------------------------------------------------------------------
# per-field authority resolution
# ---------------------------------------------------------------------------


def _assign_fields(
    *,
    tentative_keys: tuple[InstanceGroupingKey, ...],
    validated_fields: tuple[ValidatedField, ...],
    candidate_sets: tuple[CandidateSet, ...],
    field_by_id: dict[str, tuple[int, FieldSpec]],
    used_plan: bool,
) -> tuple[list[_Assignment], list[_AmbiguityNegative]]:
    """apply authorities 1–4 per field, in declared resolver order.

    returns `(assignments, ambiguities)`:

    - `assignments` is a list of `_Assignment` — one per field that
      reached a unique bucket under some authority.
    - `ambiguities` is a list of `_AmbiguityNegative` — one per field
      that remained ambiguous after authorities 1–4.

    deterministic tie-break — if ambiguity persists, the negative is
    attached to the tentative bucket with the strongest partial signal
    from authorities 1–4; when signals tie, the bucket with the lowest
    tentative-key index wins.
    """

    assignments: list[_Assignment] = []
    ambiguities: list[_AmbiguityNegative] = []

    for validated in validated_fields:
        _declaration_index, field_spec = field_by_id[validated.proposed.field_id]
        grouping_role: str | None = (
            field_spec.grouping_binding.role if field_spec.grouping_binding is not None else None
        )

        # authority #1 — explicit GroupingBinding
        index_a1 = boundary_defining_bucket(
            tentative_keys=tentative_keys,
            validated_field_tentative_instance_key=validated.proposed.tentative_instance_key,
            grouping_role=grouping_role,
        )
        if index_a1 is not None:
            assignments.append(
                _Assignment(
                    bucket_index=index_a1,
                    authority=_AUTHORITY_BOUNDARY,
                    validated=validated,
                ),
            )
            continue

        # authority #2 — source-anchor continuity
        hits_a2 = source_anchor_continuity_buckets(
            tentative_keys=tentative_keys,
            validated_field_source_span=validated.proposed.source_span,
        )
        if len(hits_a2) == 1:
            assignments.append(
                _Assignment(
                    bucket_index=hits_a2[0],
                    authority=_AUTHORITY_CONTINUITY,
                    validated=validated,
                ),
            )
            continue

        # authority #3 — candidate co-occurrence
        hits_a3 = candidate_cooccurrence_buckets(
            tentative_keys=tentative_keys,
            validated_field=validated,
            candidate_sets=candidate_sets,
        )
        if len(hits_a3) == 1:
            assignments.append(
                _Assignment(
                    bucket_index=hits_a3[0],
                    authority=_AUTHORITY_COOCCURRENCE,
                    validated=validated,
                ),
            )
            continue

        # authority #4 — InstancePlan priors (only when the plan is
        # present; authority #4 is silent for synthesized buckets).
        index_a4: int | None = None
        if used_plan:
            tentative_hint = validated.proposed.tentative_instance_key
            if tentative_hint is not None:
                prior_matches: list[int] = []
                for i, key in enumerate(tentative_keys):
                    if key == tentative_hint:
                        prior_matches.append(i)
                if len(prior_matches) == 1:
                    index_a4 = prior_matches[0]
        if index_a4 is not None:
            assignments.append(
                _Assignment(
                    bucket_index=index_a4,
                    authority=_AUTHORITY_PLAN_PRIOR,
                    validated=validated,
                ),
            )
            continue

        # ambiguous — compute the strongest partial signal + target
        # bucket for the typed negative.
        target_bucket, partial_authority = _strongest_partial_signal(
            hits_a2=hits_a2,
            hits_a3=hits_a3,
            tentative_keys=tentative_keys,
            validated=validated,
            used_plan=used_plan,
        )
        ambiguities.append(
            _AmbiguityNegative(
                tentative_bucket_index=target_bucket,
                validated=validated,
                partial_signal_authority=partial_authority,
            ),
        )

    return assignments, ambiguities


def _strongest_partial_signal(
    *,
    hits_a2: tuple[int, ...],
    hits_a3: tuple[int, ...],
    tentative_keys: tuple[InstanceGroupingKey, ...],
    validated: ValidatedField,
    used_plan: bool,
) -> tuple[int, str | None]:
    """return `(bucket_index, partial_authority)` for an ambiguous field.

    preference order for "strongest partial signal":

    1. authority #2 matches — the lowest tentative-key index among
       `hits_a2`;
    2. else authority #3 matches — the lowest tentative-key index
       among `hits_a3`;
    3. else authority #4 (plan prior) — when the plan is present and
       the field carries a tentative_instance_key equal to some
       bucket;
    4. else bucket 0 — stable fallback so the negative always lands
       on an existing instance.
    """

    if hits_a2:
        return hits_a2[0], _AUTHORITY_CONTINUITY
    if hits_a3:
        return hits_a3[0], _AUTHORITY_COOCCURRENCE
    if used_plan:
        tentative_hint = validated.proposed.tentative_instance_key
        if tentative_hint is not None:
            for i, key in enumerate(tentative_keys):
                if key == tentative_hint:
                    return i, _AUTHORITY_PLAN_PRIOR
    return 0, None


# ---------------------------------------------------------------------------
# cardinality policy
# ---------------------------------------------------------------------------


def _apply_cardinality_policy(
    *,
    assignments: list[_Assignment],
    field_by_id: dict[str, tuple[int, FieldSpec]],
    tentative_keys: tuple[InstanceGroupingKey, ...],
) -> tuple[list[_Assignment], list[_CardinalityNegative]]:
    """drop proposals that violate resolution-stage cardinality policy.

    two codes fire in phase 1:

    - `cardinality.one_multiple_instances` when `Cardinality.ONE`
      produces proposals for a single field across more than one
      tentative bucket. every affected (field, bucket) produces one
      negative, attached to that bucket; all affected proposals are
      dropped.
    - `cardinality.per_instance_multi_in_instance` when
      `Cardinality.PER_INSTANCE` produces more than one surviving
      proposal for the same field in the same bucket. every affected
      (field, bucket) produces one negative; all affected proposals
      in that bucket are dropped.
    """

    # group `assignments` by (field_id, bucket_index) and by field_id.
    by_field_bucket: dict[tuple[str, int], list[_Assignment]] = {}
    by_field: dict[str, dict[int, list[_Assignment]]] = {}
    for assignment in assignments:
        field_id = assignment.validated.proposed.field_id
        key = (field_id, assignment.bucket_index)
        by_field_bucket.setdefault(key, []).append(assignment)
        by_field.setdefault(field_id, {}).setdefault(assignment.bucket_index, []).append(assignment)

    survivors_mask = [True] * len(assignments)
    negatives: list[_CardinalityNegative] = []

    # index-map so we can flag specific assignment entries for drop
    # without destroying the assignment order.
    assignment_index: dict[int, int] = {id(a): i for i, a in enumerate(assignments)}

    # Cardinality.ONE — multi-bucket
    for field_id, bucket_map in by_field.items():
        _declaration_index, field_spec = field_by_id[field_id]
        if field_spec.cardinality is not Cardinality.ONE:
            continue
        if len(bucket_map) <= 1:
            continue
        # every bucket's proposals are affected; emit one negative per
        # bucket in stable tentative-key order.
        for bucket_index in sorted(bucket_map.keys()):
            bucket_assignments = bucket_map[bucket_index]
            # representative: first assignment in this bucket, for
            # provenance of the typed negative.
            representative = bucket_assignments[0]
            negatives.append(
                _CardinalityNegative(
                    tentative_bucket_index=bucket_index,
                    field_id=field_id,
                    code="cardinality.one_multiple_instances",
                    validated=representative.validated,
                ),
            )
            for a in bucket_assignments:
                survivors_mask[assignment_index[id(a)]] = False

    # Cardinality.PER_INSTANCE — multi-in-instance
    for (field_id, bucket_index), group in by_field_bucket.items():
        _declaration_index, field_spec = field_by_id[field_id]
        if field_spec.cardinality is not Cardinality.PER_INSTANCE:
            continue
        # skip if already dropped by ONE policy (defensive — should
        # not overlap in phase 1 because the two cardinalities are
        # disjoint per field).
        if len(group) <= 1:
            continue
        representative = group[0]
        negatives.append(
            _CardinalityNegative(
                tentative_bucket_index=bucket_index,
                field_id=field_id,
                code="cardinality.per_instance_multi_in_instance",
                validated=representative.validated,
            ),
        )
        for a in group:
            survivors_mask[assignment_index[id(a)]] = False

    # preserve tentative-key order then declaration order — the loop
    # above already enumerated buckets in ascending order. drop the
    # negatives tracker does not require further sorting here.
    _ = tentative_keys  # currently only used for order-bounded iteration semantics

    survivors = [a for a, keep in zip(assignments, survivors_mask, strict=True) if keep]
    return survivors, negatives


# ---------------------------------------------------------------------------
# compose final `Instance`s
# ---------------------------------------------------------------------------


def _compose_results(
    *,
    tentative_keys: tuple[InstanceGroupingKey, ...],
    survivors: list[_Assignment],
    ambiguities: list[_AmbiguityNegative],
    cardinality_negatives: list[_CardinalityNegative],
    field_by_id: dict[str, tuple[int, FieldSpec]],
    producer_version: str,
    used_plan: bool,
) -> tuple[Instance, ...]:
    """form the per-bucket final `Instance`s.

    an instance is dropped when it has zero surviving proposals and
    zero negatives attached. negatives alone keep the instance in the
    output — the resolver promised typed negatives land on an
    `Instance`, not free-floating.
    """

    # group survivors by bucket.
    survivors_by_bucket: dict[int, list[_Assignment]] = {}
    for assignment in survivors:
        survivors_by_bucket.setdefault(assignment.bucket_index, []).append(assignment)

    ambiguities_by_bucket: dict[int, list[_AmbiguityNegative]] = {}
    for amb in ambiguities:
        ambiguities_by_bucket.setdefault(amb.tentative_bucket_index, []).append(amb)

    cardinality_by_bucket: dict[int, list[_CardinalityNegative]] = {}
    for card in cardinality_negatives:
        cardinality_by_bucket.setdefault(card.tentative_bucket_index, []).append(card)

    results: list[Instance] = []
    final_ordinal = 0
    for bucket_index, tentative_key in enumerate(tentative_keys):
        bucket_survivors = survivors_by_bucket.get(bucket_index, [])
        bucket_ambiguities = ambiguities_by_bucket.get(bucket_index, [])
        bucket_cardinalities = cardinality_by_bucket.get(bucket_index, [])

        if not bucket_survivors and not bucket_ambiguities and not bucket_cardinalities:
            # instance resolved to zero proposals and carried no
            # negatives — drop per the phase-1 rule.
            continue

        # stable ordering for survivors: (declaration_index, index of
        # the validated field inside `validated_fields`). we recover
        # declaration_index from `field_by_id`; the per-assignment
        # index into the original `validated_fields` tuple is implicit
        # in survivor iteration order, so we order survivors by
        # `(declaration_index, surviving-assignment creation order)`.
        bucket_survivors_sorted = sorted(
            bucket_survivors,
            key=lambda a: (field_by_id[a.validated.proposed.field_id][0],),
        )

        evidence = _promote_survivors(
            bucket_survivors_sorted=bucket_survivors_sorted,
            final_instance_key_placeholder=tentative_key,
        )

        final_group_anchors = _final_group_anchors(
            bucket_survivors_sorted=bucket_survivors_sorted,
            field_by_id=field_by_id,
            tentative_key=tentative_key,
        )
        final_instance_key = InstanceGroupingKey(
            group_id=(
                tentative_key.group_id
                if used_plan
                else _compute_group_id(
                    group_anchors=final_group_anchors,
                    group_key_material=("resolved", final_ordinal),
                )
            ),
            ordinal=final_ordinal,
            group_anchors=final_group_anchors,
        )
        final_ordinal += 1

        # rebuild evidence now that the final grouping key exists —
        # `_promote_survivors` used the tentative as a placeholder so
        # we could sort and compute anchors first.
        evidence = tuple(
            Evidence(
                field_id=item.field_id,
                instance_id=final_instance_key.group_id,
                instance_key=final_instance_key,
                raw_value=item.raw_value,
                evidence_text=item.evidence_text,
                source_span=item.source_span,
                evidence_spans=item.evidence_spans,
                normalized_value=item.normalized_value,
                proposal_provenance=item.proposal_provenance,
            )
            for item in evidence
        )

        negative_outcomes: list[NegativeOutcome] = []
        for amb in bucket_ambiguities:
            negative_outcomes.append(
                NegativeOutcome(
                    category="resolution",
                    code="ambiguous_grouping",
                    field_id=amb.validated.proposed.field_id,
                    instance_key=amb.validated.proposed.tentative_instance_key,
                    reason="ambiguous_grouping",
                ),
            )
        for card in bucket_cardinalities:
            negative_outcomes.append(
                NegativeOutcome(
                    category="resolution",
                    code=card.code,
                    field_id=card.field_id,
                    instance_key=card.validated.proposed.tentative_instance_key,
                    reason=card.code,
                ),
            )

        negatives_tuple: tuple[NegativeOutcome, ...] = tuple(negative_outcomes)
        outcome = "complete" if not negatives_tuple else "partial"

        clustering_signals = _clustering_signals(
            bucket_survivors_sorted=bucket_survivors_sorted,
            bucket_ambiguities=bucket_ambiguities,
            used_plan=used_plan,
        )

        grouping_evidence = GroupingEvidence(
            stage="resolved",
            anchor_spans=final_group_anchors,
            discriminators=_grouping_discriminators(bucket_survivors_sorted),
            clustering_signals=clustering_signals,
            confidence=None,
            producer_version=producer_version,
        )

        results.append(
            Instance(
                instance_id=final_instance_key.group_id,
                instance_key=final_instance_key,
                outcome=outcome,
                evidence=evidence,
                negative_outcomes=negatives_tuple,
                grouping_evidence=grouping_evidence,
            ),
        )

    return tuple(results)


def _promote_survivors(
    *,
    bucket_survivors_sorted: list[_Assignment],
    final_instance_key_placeholder: InstanceGroupingKey,
) -> tuple[Evidence, ...]:
    """promote each surviving `ValidatedField` to `Evidence`.

    phase-1 promotion is purely structural: fields are copied from the
    `ValidatedField` / `ProposedField` without mutation. the
    `proposal_provenance` is composed from the landed lifecycle
    surface — see `_compose_provenance` for the exact field set (the
    landed `ProposalProvenance` type owns the canonical shape; this
    function does not invent fields).

    `instance_key` is populated with a placeholder at this stage;
    `_compose_results` rebuilds each proposal with the final
    `InstanceGroupingKey` once `final_group_anchors` are known.
    """

    out: list[Evidence] = []
    for assignment in bucket_survivors_sorted:
        proposed = assignment.validated.proposed
        out.append(
            Evidence(
                field_id=proposed.field_id,
                instance_id=final_instance_key_placeholder.group_id,
                instance_key=final_instance_key_placeholder,
                raw_value=proposed.raw_value,
                evidence_text=proposed.evidence_text,
                source_span=proposed.source_span,
                evidence_spans=proposed.evidence_spans,
                normalized_value=assignment.validated.normalized_value,
                proposal_provenance=_compose_provenance(assignment.validated),
            ),
        )
    return tuple(out)


def _compose_provenance(validated: ValidatedField) -> ProposalProvenance:
    """compose `ProposalProvenance` from landed lifecycle objects.

    the field set matches the landed `ProposalProvenance` type
    exactly — phase-1 does not invent new provenance fields. the
    brief mentions `field_validation_version` as a composition
    source, but the landed `ProposalProvenance` does not currently
    carry that field; we pin to code reality rather than widen the
    public canonical. if a future thread adds
    `field_validation_version` to `ProposalProvenance`, this helper is
    the single site to thread it through.
    """

    proposed = validated.proposed
    return ProposalProvenance(
        strategy_id=proposed.strategy_id,
        candidate_id_refs=proposed.candidate_id_refs,
        selector_producer_version=proposed.selector_producer_version,
        grounded_producer_version=proposed.grounded_producer_version,
    )


def _final_group_anchors(
    *,
    bucket_survivors_sorted: list[_Assignment],
    field_by_id: dict[str, tuple[int, FieldSpec]],
    tentative_key: InstanceGroupingKey,
) -> tuple[SourceSpan, ...]:
    """return the final `InstanceGroupingKey.group_anchors` for a bucket.

    policy (phase-1, fixed):

    1. if one or more surviving proposals come from `boundary_defining`
       fields, anchors are those proposals' `source_span`s in stable
       (declaration_index, survivor order) order.
    2. else if the tentative bucket had non-empty `group_anchors`,
       carry them forward verbatim.
    3. else anchors are the ordered unique `source_span`s of the
       surviving proposals in that bucket.

    `group_anchors` may carry mixed `text_anchor_space` across a
    group (ADR-0006); the resolver does not coerce.
    """

    boundary_defining_spans: list[SourceSpan] = []
    for assignment in bucket_survivors_sorted:
        _declaration_index, field_spec = field_by_id[assignment.validated.proposed.field_id]
        binding = field_spec.grouping_binding
        if binding is not None and binding.role == "boundary_defining":
            boundary_defining_spans.append(assignment.validated.proposed.source_span)

    if boundary_defining_spans:
        return _ordered_unique_spans(tuple(boundary_defining_spans))

    if tentative_key.group_anchors:
        return tentative_key.group_anchors

    survivor_spans = tuple(a.validated.proposed.source_span for a in bucket_survivors_sorted)
    return _ordered_unique_spans(survivor_spans)


def _clustering_signals(
    *,
    bucket_survivors_sorted: list[_Assignment],
    bucket_ambiguities: list[_AmbiguityNegative],
    used_plan: bool,
) -> dict[str, Any]:
    """compose a JSON-safe, deterministic `clustering_signals` mapping.

    the mapping names which authority resolved each survivor and
    whether tentative priors were consulted. it is optional under the
    `GroupingEvidence` contract; phase-1 emits a small stable
    snapshot so downstream diagnostics can inspect the resolver's
    decision without parsing prose.
    """

    authorities_counter: dict[str, int] = {}
    for assignment in bucket_survivors_sorted:
        authorities_counter[assignment.authority] = (
            authorities_counter.get(assignment.authority, 0) + 1
        )

    signals: dict[str, Any] = {
        "authorities": dict(sorted(authorities_counter.items())),
        "used_plan": used_plan,
    }
    if bucket_ambiguities:
        signals["ambiguous_count"] = len(bucket_ambiguities)
    return signals


def _grouping_discriminators(
    bucket_survivors_sorted: list[_Assignment],
) -> tuple[GroupingDiscriminator, ...]:
    """return typed grouping diagnostics for public consumers.

    Discriminators explain which extracted fields helped separate this
    instance from its siblings. They are diagnostic, not domain identity.
    """

    out: list[GroupingDiscriminator] = []
    seen: set[tuple[str, tuple[str, ...], str]] = set()
    for assignment in bucket_survivors_sorted:
        proposed = assignment.validated.proposed
        key = (proposed.field_id, proposed.candidate_id_refs, assignment.authority)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            GroupingDiscriminator(
                field_id=proposed.field_id,
                candidate_id_refs=proposed.candidate_id_refs,
                authority=cast("Any", assignment.authority),
            ),
        )
    return tuple(out)


def _compute_group_id(
    *,
    group_anchors: tuple[SourceSpan, ...],
    group_key_material: tuple[object, ...],
) -> str:
    """compute `InstanceGroupingKey.group_id` per the seam-G.resolver invariant.

    mirrors the seam-G.planner computation so the two sites agree on
    the hash shape: every `SourceSpan` is serialized via
    `model_dump(mode="json")` (which includes `text_anchor_space` per
    ADR-0006) and the combined tuple is fed to `stable_hash`.
    """

    serialized_anchors = [span.model_dump(mode="json") for span in group_anchors]
    return stable_hash((serialized_anchors, list(group_key_material)))
