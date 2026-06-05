"""algorithmic (non-llm) `Selector` implementations per docs/architecture.md §7 seam D.

phase-1 exposes `SingletonSelector`, a deterministic selector whose
policy is fixed at three cases (empty, singleton, multi). see the
`singleton` module docstring for the policy and rationale.
"""

from __future__ import annotations

from .category import (
    CategoryRule,
    CategorySignal,
    CategorySignalStrength,
    RuleBasedCategorySelector,
)
from .singleton import (
    AMBIGUOUS_REASON_LABEL,
    SingletonSelector,
    algorithmic_code_hash,
)

__all__ = [
    "AMBIGUOUS_REASON_LABEL",
    "CategoryRule",
    "CategorySignal",
    "CategorySignalStrength",
    "RuleBasedCategorySelector",
    "SingletonSelector",
    "algorithmic_code_hash",
]
