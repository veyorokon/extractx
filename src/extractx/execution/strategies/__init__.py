"""extraction strategy implementations per docs/architecture.md §11.

strategies are internal to v1 — no public `Strategy` extension protocol.

phase-1 (M8 vertical slice) ships:

- `IndependentStrategy` — the runnable v1 strategy for the supported
  regex-bound vertical slice.
- `IterativeStrategy` — marker for the executor-owned bounded repair
  strategy path.
"""

from __future__ import annotations

from .independent import IndependentStrategy
from .iterative import IterativeStrategy

__all__ = [
    "IndependentStrategy",
    "IterativeStrategy",
]
