"""`IterativeStrategy` marker for the bounded repair strategy path.

The first runnable iterative slice is executor-owned: `SerialExecutor`
runs the canonical independent pass, evaluates object validators, and
when `ExecutorPolicy.strategy == "iterative"` performs one bounded retry
round for fields implicated by error-severity `ObjectIssue`s.

Full planner-first, multi-instance iterative extraction remains a later
thread. This class stays constructible as the stable strategy vocabulary
entry, but the current behavior is intentionally implemented at the
executor composition seam so layer-3 validation remains single-owner.
"""

from __future__ import annotations

__all__ = ["IterativeStrategy"]


class IterativeStrategy:
    """marker object for the executor-owned iterative repair path."""

    __slots__ = ()
