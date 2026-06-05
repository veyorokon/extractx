"""tier-1 end-user public exceptions per docs/architecture.md §10 and §13.

five exception types. after a run begins, engine step failures,
validation errors, budget exhaustion, and malformed soft-compute output
all become typed `NegativeOutcome`s or `ValidationFailure`s routed
through `ExecutorPolicy`. `ExtractionFailed` is the public materializing
helper exception raised after a run result exists and the helper cannot
return its promised object.

- `SpecError`           — raised at `ExtractionSpec.from_pydantic()` or
                          manual construction
- `CapabilityError`     — raised at `Runtime(...)` / `Runtime.from_env()`
- `InfrastructureError` — raised at `Executor` setup
- `InterviewError`      — raised post-run on `.interview()` when
                          transcripts were not captured, the transcript
                          cannot be found, or `producer_version` does not
                          match the current runtime
- `ExtractionFailed`    — raised by `extract_one(...)` when a completed
                          single materialized object is not available;
                          carries the full `Extraction`
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .outcomes import Extraction


class SpecError(Exception):
    """raised at `ExtractionSpec` construction. see docs/architecture.md §13."""


class CapabilityError(Exception):
    """raised at `Runtime` construction. see docs/architecture.md §13."""


class InfrastructureError(Exception):
    """raised at `Executor` setup. see docs/architecture.md §13."""


class InterviewError(Exception):
    """raised at `Extraction.interview(...)`.

    see docs/architecture.md §13 and
    docs/adr/0002-pydantic-ai-default-selector-and-interview.md.
    """


class ExtractionFailed(Exception):  # noqa: N818 - public API name from architecture.
    """raised by `extract_one(...)` after a run result exists."""

    result: Extraction

    def __init__(self, message: str, *, result: Extraction) -> None:
        super().__init__(message)
        self.result = result
