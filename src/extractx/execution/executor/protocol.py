"""`Executor` protocol per docs/architecture.md §11 and §7 seam I.

phase-1 (M8 vertical slice) declares the single canonical executor
callable surface:

    async def execute(
        document: bytes | str,
        spec: ExtractionSpec,
        runtime: Runtime,
        policy: ExecutorPolicy,
    ) -> Extraction

executors are the only writers of `ExecutionTrace` and the only entities
allowed to construct `Extraction`. step failures, validation
errors, and budget exhaustion become typed `NegativeOutcome`s or
`ValidationFailure`s routed through `ExecutorPolicy` — never raw
exceptions surfaced to the caller after the run begins.

phase-1 supports a single concrete `Executor` impl (`SerialExecutor`).
`AsyncExecutor` remains a typed stub.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from extractx.core.objects import ExtractionSpec
    from extractx.core.outcomes import Extraction

    from ..policy import ExecutorPolicy
    from ..runtime import Runtime

__all__ = ["Executor"]


class Executor(Protocol):
    """see docs/architecture.md §7 seam I (Executor × Runtime × Strategy).

    phase-1 callable surface: a single async `execute(...)` method.
    `Executor` impls own concurrency, retry, budget enforcement, trace
    writing, manifest check, and graph construction. graph construction
    is internal to the impl; no public `PipelineGraph` type is exposed.
    """

    async def execute(
        self,
        document: bytes | str,
        spec: ExtractionSpec,
        runtime: Runtime,
        policy: ExecutorPolicy,
    ) -> Extraction: ...
