"""`ExecutorPolicy` typed container per docs/architecture.md §11.

phase-1 (M8 vertical slice) lands a minimal real `ExecutorPolicy` with
the surface the brief's section 1 calls out:

- `strategy: Literal["independent", "iterative", "batch"]` — selection mode
  is explicit and never inferred from the spec.
- `repair: bool | None = None` — when `None`, preserves historical behavior:
  `strategy="iterative"` repairs, other strategies do not. When explicit, it
  turns the bounded repair pass on or off independently of selection mode.
- `execution_mode: Literal["immediate", "deferred"] = "immediate"` —
  soft-call lifecycle. "immediate" preserves current behavior; "deferred"
  is an explicit submit-now, collect-later lifecycle owned by ADR-0028.
- `capture_interview_transcripts: bool = False` — opt-in capture for
  the post-run `.interview()` surface (ADR-0002 / ADR-0004); phase-1
  does not implement capture, so the flag must remain `False` for now;
  passing `True` raises `InfrastructureError` at executor setup.
- `on_validation_failure: Literal["fail"] = "fail"` — phase-1 routes
  every layer-2 `ValidationFailure(layer="field", ...)` immediately to
  a typed `NegativeOutcome("validation", "field_failure", ...)` (per
  the brief). retry / `Retry(n, ...)` policies are out of scope; only
  the literal `"fail"` is accepted.

`ExecutorPolicy` is immutable and frozen: same `(spec, runtime,
policy)` triple yields byte-identical `Extraction` per the
architecture's determinism clause.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .deferred import ExecutionMode

__all__ = ["ExecutorPolicy", "PolicySummary"]


class ExecutorPolicy(BaseModel):
    """typed run-time policy carried into `Executor.execute(...)`.

    immutable; pydantic-frozen. the strategy literal is the only
    runtime decision the executor reads from policy; everything else
    in this object is forwarded into seam plumbing (validation
    failure routing, interview capture toggle).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["independent", "iterative", "batch"] = "independent"
    execution_mode: Literal["immediate", "deferred"] = ExecutionMode.IMMEDIATE.value
    repair: bool | None = None
    capture_interview_transcripts: bool = False
    on_validation_failure: Literal["fail"] = "fail"

    @property
    def repair_enabled(self) -> bool:
        if self.repair is not None:
            return self.repair
        return self.strategy == "iterative"

    @classmethod
    def from_summary(cls, summary: PolicySummary) -> ExecutorPolicy:
        """rebuild an `ExecutorPolicy` from a persisted `PolicySummary`.

        M9 phase-2 small additive helper. the inverse direction
        (`to_summary`) is not added in this thread — M9 phase-1
        already builds `PolicySummary` directly inside the executor at
        write-time and that path stays unchanged.
        """

        return cls(
            strategy=summary.strategy,
            execution_mode=summary.execution_mode,
            repair=summary.repair,
            on_validation_failure=summary.on_validation_failure,
            capture_interview_transcripts=summary.capture_interview_transcripts,
        )


class PolicySummary(BaseModel):
    """phase-1 persisted summary of `ExecutorPolicy` carried on
    `ReplayArtifact` and `RunManifest`.

    same shape on both objects so equivalence checks read the same
    payload from either side. consistency is preserved by the M9 phase-1
    "manifest derived from artifact" rule.

    intentionally narrow:

    - `strategy` mirrors `ExecutorPolicy.strategy`.
    - `execution_mode` mirrors `ExecutorPolicy.execution_mode`.
    - `repair` mirrors `ExecutorPolicy.repair`; `None` preserves the
      historical strategy-derived default.
    - `on_validation_failure` is `"fail"` only in phase 1 (mirrors
      `ExecutorPolicy.on_validation_failure`).
    - `capture_interview_transcripts` is always `False` in phase 1 —
      the executor pre-run gate rejects `True` per ADR-0004.

    widening this shape (richer policy knobs, retry shapes, …) is a
    coordinator-owned thread; do not extend it here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["independent", "iterative", "batch"]
    execution_mode: Literal["immediate", "deferred"] = ExecutionMode.IMMEDIATE.value
    repair: bool | None = None
    on_validation_failure: Literal["fail"] = "fail"
    capture_interview_transcripts: bool = False
