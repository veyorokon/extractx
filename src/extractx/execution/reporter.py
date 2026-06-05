"""`Reporter` minimal impl per docs/architecture.md §7 seam K.

phase-1 (M8 vertical slice) ships a no-op `NullReporter`. the architecture
positions seam K as an OTEL-tracer-shaped, write-only protocol; this
slice does **not** thread step events through `Reporter` from the
strategy or executor (per the brief's "phase-1 discipline"). the
algorithmic substrate landed here assembles a minimal `ExecutionTrace`
directly inside the executor.

`NullReporter` exists so that:

- `Runtime` can bind a real, constructible `Reporter` without inventing
  fake activity (anti-pattern §15 "Silent None" / "Duplicate Overlapping
  Path");
- contract tests can assert that the binding is of the documented
  protocol shape;
- a later thread that lands the real OTEL-backed reporter has a
  drop-in seam to replace.

discipline:

- write-only from the step's perspective. `NullReporter` accepts whatever
  it is given and discards it.
- no shared mutable state visible to callers; `NullReporter` carries no
  fields.
- protocol surface beyond construction is intentionally absent — the
  `Reporter` protocol's callable surface is owned by a later seam-K
  task. exposing methods here would be inventing public contract.
"""

from __future__ import annotations

__all__ = ["NullReporter"]


class NullReporter:
    """no-op default `Reporter` for phase 1.

    structural subtype of `extractx.core.contracts.Reporter` (the
    protocol body is intentionally empty in phase 1). phase-1 strategy
    and executor do **not** invoke this reporter from inside per-step
    code; it is bound on `Runtime` so the capability surface is honest
    and so tests can assert the shape.
    """

    __slots__ = ()
