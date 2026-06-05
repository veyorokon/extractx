"""`CandidateStrategy` generator implementations per docs/architecture.md §7 seam C."""

from __future__ import annotations

from .literal_set import LiteralSetCandidateStrategy
from .ner import NerCandidateStrategy, NerEntityRulerConfig, NerStrategyParams
from .regex import RegexCandidateStrategy, RegexStrategyParams

__all__ = [
    "LiteralSetCandidateStrategy",
    "NerCandidateStrategy",
    "NerEntityRulerConfig",
    "NerStrategyParams",
    "RegexCandidateStrategy",
    "RegexStrategyParams",
]
