"""deterministic auto-selection gate for structured candidates."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from extractx.core import CandidateSet
from extractx.core.versions import algorithmic_producer_version, stable_hash

__all__ = ["AutoSelection", "DeterministicSelectionGate", "algorithmic_code_hash"]


class AutoSelection(BaseModel):
    """deterministic candidate choice made without invoking a selector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    reason: Literal["single_structured_contract_match"] = "single_structured_contract_match"


class DeterministicSelectionGate:
    """auto-select only when the structured contract leaves no ambiguity."""

    def __init__(self) -> None:
        self._producer_version = algorithmic_code_hash()

    @property
    def producer_version(self) -> str:
        return self._producer_version

    def evaluate(
        self,
        candidate_set: CandidateSet,
        *,
        require_corroboration: bool = False,
    ) -> AutoSelection | None:
        """return an `AutoSelection` when exactly one structured candidate passed.

        Source declaration order has no authority semantics. If no structured
        candidate passed, more than one passed, or corroboration is required,
        caller must invoke the normal selector over the bounded candidate set.
        """

        if require_corroboration:
            return None
        eligible = tuple(
            candidate
            for candidate in candidate_set.candidates
            if candidate.source_kind == "structured"
            and candidate.structural_status is not None
            and candidate.structural_status.passed
        )
        if len(eligible) != 1:
            return None
        return AutoSelection(candidate_id=eligible[0].candidate_id)


def algorithmic_code_hash() -> str:
    digest = stable_hash(
        f"{DeterministicSelectionGate.__module__}.{DeterministicSelectionGate.__qualname__}",
    )
    return algorithmic_producer_version(code_hash=digest)
