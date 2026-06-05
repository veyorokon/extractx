"""Rule-based CATEGORY selector backend.

This module implements ADR-0035's deterministic selector backend for existing
literal/category fields. It does not define a new classification layer: rules
select among bounded literal candidates and return the same canonical
`Observation` shape as every other seam-D selector.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from extractx.core import (
    CandidateSet,
    Cardinality,
    ContextPack,
    FieldSpec,
    Observation,
    SourceSpan,
)
from extractx.core.exceptions import InfrastructureError
from extractx.core.versions import algorithmic_producer_version, stable_hash

from ..selector import enforce_observation_contract

__all__ = [
    "CategorySignal",
    "CategorySignalStrength",
    "CategoryRule",
    "RuleBasedCategorySelector",
]


type CategorySignalStrength = Literal["weak", "medium", "strong"]


class CategoryRule(BaseModel):
    """One deterministic matcher for a category literal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    candidate_literal: str
    pattern: str
    polarity: Literal["positive", "negative", "ambiguous"] = "positive"
    strength: CategorySignalStrength = "strong"
    ignore_case: bool = True
    multiline: bool = True
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_rule(self) -> CategoryRule:
        if not self.rule_id:
            raise ValueError("CategoryRule.rule_id must be non-empty")
        if not self.candidate_literal:
            raise ValueError("CategoryRule.candidate_literal must be non-empty")
        if not self.pattern:
            raise ValueError("CategoryRule.pattern must be non-empty")
        try:
            re.compile(self.pattern, self.flags)
        except re.error as exc:
            raise ValueError(
                f"CategoryRule.pattern is not valid regex for rule_id={self.rule_id!r}: {exc}",
            ) from exc
        return self

    @property
    def flags(self) -> int:
        flags = 0
        if self.ignore_case:
            flags |= re.IGNORECASE
        if self.multiline:
            flags |= re.MULTILINE
        return flags


class CategorySignal(BaseModel):
    """Replayable signal emitted by `RuleBasedCategorySelector`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: str
    rule_id: str
    candidate_literal: str
    candidate_id: str | None
    polarity: Literal["positive", "negative", "ambiguous"]
    strength: CategorySignalStrength
    text: str
    source_span: SourceSpan
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class RuleBasedCategorySelector:
    """Deterministic CATEGORY selector over bounded literal candidates."""

    def __init__(
        self,
        *,
        rules: Sequence[CategoryRule | Mapping[str, Any]],
        uncertain_literal: str | None = None,
    ) -> None:
        self._rules = tuple(
            rule if isinstance(rule, CategoryRule) else CategoryRule.model_validate(rule)
            for rule in rules
        )
        self._uncertain_literal = uncertain_literal
        self._producer_version = algorithmic_producer_version(
            stable_hash(
                {
                    "producer": (
                        f"{RuleBasedCategorySelector.__module__}."
                        f"{RuleBasedCategorySelector.__qualname__}"
                    ),
                    "rules": [rule.model_dump(mode="json") for rule in self._rules],
                    "uncertain_literal": uncertain_literal,
                },
            ),
        )
        self._last_call_diagnostic: Mapping[str, object] | None = None

    @property
    def producer_version(self) -> str:
        return self._producer_version

    @property
    def last_call_diagnostic(self) -> Mapping[str, object] | None:
        return self._last_call_diagnostic

    def select(
        self,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        context_pack: ContextPack,
        instance_state: object | None = None,
        *,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> Observation:
        del instance_state
        self._validate_inputs(field_spec, candidate_set)
        instance_id = instance_ids[0] if instance_ids else None
        literal_to_candidate_id = _literal_to_candidate_id(candidate_set)
        signals = tuple(
            self._signals_for_rule(
                rule=rule,
                document_text=context_pack.document_summary,
                literal_to_candidate_id=literal_to_candidate_id,
                candidate_set=candidate_set,
            )
            for rule in self._rules
        )
        flat_signals = tuple(signal for rule_signals in signals for signal in rule_signals)
        observation = self._observation_from_signals(
            field_spec=field_spec,
            candidate_set=candidate_set,
            signals=flat_signals,
            instance_id=instance_id,
            literal_to_candidate_id=literal_to_candidate_id,
        )
        self._last_call_diagnostic = {
            "category_signals": tuple(
                signal.model_dump(mode="json") for signal in flat_signals
            ),
            "model_metadata": {
                "producer_version": self.producer_version,
                "selector_backend": "rule_based_category",
                "rule_count": len(self._rules),
                "signal_count": len(flat_signals),
            },
        }
        return enforce_observation_contract(observation, candidate_set)

    def _validate_inputs(self, field_spec: FieldSpec, candidate_set: CandidateSet) -> None:
        if field_spec.value_kind.name != "CATEGORY":
            raise InfrastructureError(
                "rule_based_category.invalid_field: field "
                f"{field_spec.field_id!r} is not ValueKind.CATEGORY",
            )
        if field_spec.field_id != candidate_set.field_id:
            raise InfrastructureError(
                "rule_based_category.field_mismatch: "
                f"field_spec.field_id={field_spec.field_id!r} "
                f"candidate_set.field_id={candidate_set.field_id!r}",
            )
        candidate_literals = set(_literal_to_candidate_id(candidate_set))
        unknown_literals = [
            rule.candidate_literal
            for rule in self._rules
            if rule.candidate_literal not in candidate_literals
        ]
        if unknown_literals:
            raise InfrastructureError(
                "rule_based_category.unknown_literal: rules target literals "
                f"{unknown_literals!r} not present in CandidateSet",
            )
        if (
            self._uncertain_literal is not None
            and self._uncertain_literal not in candidate_literals
        ):
            raise InfrastructureError(
                "rule_based_category.unknown_uncertain_literal: "
                f"{self._uncertain_literal!r} not present in CandidateSet",
            )

    def _signals_for_rule(
        self,
        *,
        rule: CategoryRule,
        document_text: str,
        literal_to_candidate_id: Mapping[str, str],
        candidate_set: CandidateSet,
    ) -> tuple[CategorySignal, ...]:
        compiled = re.compile(rule.pattern, rule.flags)
        source_ref = candidate_set.candidates[0].source_span.source_ref
        out: list[CategorySignal] = []
        for index, match in enumerate(compiled.finditer(document_text), start=1):
            text = match.group(0)
            byte_start = len(document_text[: match.start()].encode("utf-8"))
            byte_end = len(document_text[: match.end()].encode("utf-8"))
            out.append(
                CategorySignal(
                    signal_id=stable_hash(
                        {
                            "rule_id": rule.rule_id,
                            "candidate_literal": rule.candidate_literal,
                            "match_index": index,
                            "byte_start": byte_start,
                            "byte_end": byte_end,
                            "text": text,
                        },
                    ),
                    rule_id=rule.rule_id,
                    candidate_literal=rule.candidate_literal,
                    candidate_id=literal_to_candidate_id.get(rule.candidate_literal),
                    polarity=rule.polarity,
                    strength=rule.strength,
                    text=text,
                    source_span=SourceSpan(
                        source_ref=source_ref,
                        text_anchor_space="normalized_text",
                        byte_start=byte_start,
                        byte_end=byte_end,
                    ),
                    metadata=rule.metadata,
                ),
            )
        return tuple(out)

    def _observation_from_signals(
        self,
        *,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        signals: tuple[CategorySignal, ...],
        instance_id: str | None,
        literal_to_candidate_id: Mapping[str, str],
    ) -> Observation:
        ambiguous_literals = _literals_by_polarity(signals, "ambiguous")
        positive_literals = _literals_by_polarity(signals, "positive")
        negative_literals = _literals_by_polarity(signals, "negative")

        if ambiguous_literals:
            return self._uncertain_or_abstain(
                field_spec=field_spec,
                instance_id=instance_id,
                literal_to_candidate_id=literal_to_candidate_id,
                reason="rule_based_category.ambiguous_signal",
            )

        selected_literals = positive_literals - negative_literals
        if field_spec.cardinality is Cardinality.MANY:
            selected_ids = tuple(
                candidate.candidate_id
                for candidate in candidate_set.candidates
                if _candidate_literal(candidate) in selected_literals
            )
            return Observation(
                instance_id=instance_id,
                field_id=field_spec.field_id,
                evidence_id=selected_ids[0] if selected_ids else None,
                abstain=False,
                outcome="SELECTED",
                selected_candidate_ids=selected_ids,
                reason="rule_based_category.union",
                producer_version=self.producer_version,
            )

        if len(selected_literals) == 1:
            candidate_id = literal_to_candidate_id[next(iter(selected_literals))]
            return Observation(
                instance_id=instance_id,
                field_id=field_spec.field_id,
                evidence_id=candidate_id,
                abstain=False,
                outcome="SELECTED",
                selected_candidate_ids=(candidate_id,),
                reason="rule_based_category.single_match",
                producer_version=self.producer_version,
            )
        if len(selected_literals) > 1:
            return self._uncertain_or_abstain(
                field_spec=field_spec,
                instance_id=instance_id,
                literal_to_candidate_id=literal_to_candidate_id,
                reason="rule_based_category.conflicting_positive_signals",
            )
        return self._uncertain_or_abstain(
            field_spec=field_spec,
            instance_id=instance_id,
            literal_to_candidate_id=literal_to_candidate_id,
            reason="rule_based_category.no_signal",
        )

    def _uncertain_or_abstain(
        self,
        *,
        field_spec: FieldSpec,
        instance_id: str | None,
        literal_to_candidate_id: Mapping[str, str],
        reason: str,
    ) -> Observation:
        if self._uncertain_literal is not None:
            candidate_id = literal_to_candidate_id[self._uncertain_literal]
            return Observation(
                instance_id=instance_id,
                field_id=field_spec.field_id,
                evidence_id=candidate_id,
                abstain=False,
                outcome="SELECTED",
                selected_candidate_ids=(candidate_id,),
                reason=reason,
                producer_version=self.producer_version,
            )
        return Observation(
            instance_id=instance_id,
            field_id=field_spec.field_id,
            evidence_id=None,
            abstain=True,
            outcome="ABSTAINED",
            selected_candidate_ids=(),
            reason=reason,
            producer_version=self.producer_version,
        )


def _literal_to_candidate_id(candidate_set: CandidateSet) -> dict[str, str]:
    out: dict[str, str] = {}
    for candidate in candidate_set.candidates:
        literal = _candidate_literal(candidate)
        if literal is None:
            raise InfrastructureError(
                "rule_based_category.invalid_candidate: CATEGORY candidates must "
                f"carry structured_payload.literal; candidate_id={candidate.candidate_id!r}",
            )
        out[literal] = candidate.candidate_id
    return out


def _candidate_literal(candidate: object) -> str | None:
    structured_payload = getattr(candidate, "structured_payload", None)
    if not isinstance(structured_payload, Mapping):
        return None
    typed_payload = cast("Mapping[str, object]", structured_payload)
    literal = typed_payload.get("literal")
    return literal if isinstance(literal, str) else None


def _literals_by_polarity(
    signals: Iterable[CategorySignal],
    polarity: Literal["positive", "negative", "ambiguous"],
) -> set[str]:
    return {signal.candidate_literal for signal in signals if signal.polarity == polarity}
