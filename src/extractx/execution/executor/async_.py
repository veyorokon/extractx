"""`AsyncExecutor` typed stub per docs/architecture.md §11.

phase-1 (M8 vertical slice) ships **only** `SerialExecutor` as the
runnable executor. `AsyncExecutor` exists today as a typed stub so the
public type surface in `execution/__init__.py` is honest about the
documented v1 executor pair, but instantiation raises
`NotImplementedError` until the async-execution thread lands.

discipline (per the M8 brief):

- no `asyncio.gather` graph, no parallel-per-field flow, no
  per-instance scheduling logic in this stub. the brief explicitly
  forbids inventing them.
- the `execute(...)` callable is declared so the protocol structural
  match holds; a caller that constructs `AsyncExecutor()` and calls
  `.execute(...)` gets a clear `NotImplementedError` rather than
  silent fall-through behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from extractx.core.objects import ExtractionSpec
    from extractx.core.outcomes import Extraction

    from ..policy import ExecutorPolicy
    from ..runtime import Runtime

__all__ = ["AsyncExecutor"]


class AsyncExecutor:
    """typed stub for the documented async executor.

    structural `Executor` match. `execute(...)` raises
    `NotImplementedError` until the async-execution thread lands.
    """

    async def execute(
        self,
        document: bytes | str,
        spec: ExtractionSpec,
        runtime: Runtime,
        policy: ExecutorPolicy,
    ) -> Extraction:
        del document, spec, runtime, policy
        raise NotImplementedError(
            "AsyncExecutor.execute is a typed stub for phase 1; "
            "the runnable v1 executor is SerialExecutor. async "
            "execution lands with a later thread.",
        )
