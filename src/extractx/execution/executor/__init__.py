"""`Executor` implementations per docs/architecture.md §11.

phase-1 (M8 vertical slice) ships:

- `Executor` protocol (single async `execute` method).
- `SerialExecutor` — the runnable v1 executor.
- `AsyncExecutor` — typed stub until the async-execution thread lands.
"""

from __future__ import annotations

from .async_ import AsyncExecutor
from .protocol import Executor
from .serial import SerialExecutor

__all__ = [
    "AsyncExecutor",
    "Executor",
    "SerialExecutor",
]
