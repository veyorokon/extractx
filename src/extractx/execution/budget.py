"""`Budget` default impl per docs/architecture.md §7 seam J and ADR-0001.

phase-1 (M8 vertical slice) ships **`TokenCountBudget`** — the documented
default `Budget` impl. it satisfies the `Budget` protocol declared in
`extractx.core.contracts` and counts input/output tokens against
user-provided ceilings. the algorithmic execution path landed in this
slice does not emit `UsageEvent`s, so `TokenCountBudget`'s counters stay
at zero unless the budget is exercised directly.

discipline (per ADR-0001 / principle 21):

- pricing logic stays out of core. cost-in-dollars translation is a user
  concern; consumers wanting it subclass or wrap `Budget` and read
  `UsageEvent.raw_usage` against their own pricing source.
- `record(event)` does **not** reshape `event.raw_usage`; the raw payload
  passes through untouched. the budget reads only the typed projection
  (`input_tokens`, `output_tokens`).
- `check()` returns a typed `BudgetDecision`; on a budget breach the
  reason names the limit that was exceeded so diagnostics can route
  cleanly.
- the budget is intentionally not async and not stateful in any way that
  defeats determinism: equal `(limits, recorded_events)` pairs always
  produce equal `check()` decisions.
"""

from __future__ import annotations

from extractx.core.contracts import BudgetDecision
from extractx.core.objects import UsageEvent

__all__ = ["TokenCountBudget"]


class TokenCountBudget:
    """default `Budget` impl that tracks input/output tokens.

    structural `Budget` subtype — no base class required. `record(event)`
    accumulates `event.input_tokens` and `event.output_tokens` (treating
    `None` as zero) and `check()` returns `allowed=False` with a typed
    reason string the moment either ceiling has been exceeded.

    phase-1 caveat: the algorithmic vertical slice does not emit
    `UsageEvent`s, so `TokenCountBudget` constructed by `Runtime()`
    holds counters of zero through a successful run. tests exercise
    `record` / `check` directly to prove the protocol surface holds.
    """

    def __init__(
        self,
        *,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        if max_input_tokens is not None and max_input_tokens < 0:
            raise ValueError(
                f"TokenCountBudget: max_input_tokens must be >= 0 when set, got {max_input_tokens}",
            )
        if max_output_tokens is not None and max_output_tokens < 0:
            raise ValueError(
                "TokenCountBudget: max_output_tokens must be >= 0 when set, "
                f"got {max_output_tokens}",
            )
        self._max_input_tokens: int | None = max_input_tokens
        self._max_output_tokens: int | None = max_output_tokens
        self._input_tokens: int = 0
        self._output_tokens: int = 0

    @property
    def input_tokens(self) -> int:
        """cumulative input tokens recorded so far."""

        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        """cumulative output tokens recorded so far."""

        return self._output_tokens

    def record(self, event: UsageEvent) -> None:
        """accumulate token counts from a `UsageEvent`.

        `None` token counts are treated as zero — algorithmic producers
        emit `UsageEvent`s with null tokens (per ADR-0001), and we do
        not want to fail loudly on those. `raw_usage` is untouched.
        """

        if event.input_tokens is not None:
            self._input_tokens += event.input_tokens
        if event.output_tokens is not None:
            self._output_tokens += event.output_tokens

    def check(self) -> BudgetDecision:
        """return `allow` / `deny_with_reason` decision for the current state.

        denial reasons are stable strings naming the limit that was
        exceeded (`"input_tokens_exceeded"` / `"output_tokens_exceeded"`)
        so diagnostics can route cleanly without parsing prose.
        """

        if self._max_input_tokens is not None and self._input_tokens > self._max_input_tokens:
            return BudgetDecision(
                allowed=False,
                reason="input_tokens_exceeded",
            )
        if self._max_output_tokens is not None and self._output_tokens > self._max_output_tokens:
            return BudgetDecision(
                allowed=False,
                reason="output_tokens_exceeded",
            )
        return BudgetDecision(allowed=True)
