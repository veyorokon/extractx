"""execution subsystem per docs/architecture.md §7 seams I, J, K and §11.

phase-1 (M8 vertical slice) re-exports the runnable surface:

- `Executor` protocol + `SerialExecutor` (runnable) + `AsyncExecutor`
  (typed stub).
- `IndependentStrategy` (single-pass runnable) + `IterativeStrategy`
  (bounded single-instance object-repair path through `SerialExecutor`).
- `Runtime` — capability container (`llm`, `nlp`, `fetch`, `budget`,
  `reporter`).
- `ExecutorPolicy` — typed run-time policy (`strategy`,
  `capture_interview_transcripts`, `on_validation_failure`).
- `TokenCountBudget` — default `Budget` impl per ADR-0001.
- `NullReporter` — no-op default `Reporter` for phase 1.
- `LocalPromptRecorder` — opt-in content-addressed prompt capture.

`Runtime` and `ExecutorPolicy` widen the tier-1 end-user surface in
`extractx/__init__.py` per the brief's section 8 / architecture §10.
"""

from __future__ import annotations

from .budget import TokenCountBudget
from .deferred import (
    DeferredAggregateSubmission,
    DeferredAggregateSubmissionManifest,
    DeferredHandle,
    DeferredPending,
    DeferredProvider,
    DeferredRequestRoute,
    DeferredResults,
    DeferredSubmission,
    DeferredSubmissionManifest,
    ExecutionMode,
    FakeDeferredProvider,
    RenderedDeferredSubmission,
    SoftCallError,
    SoftCallRequest,
    SoftCallResponse,
    SoftCallRouting,
    adapt_soft_call_response,
    aggregate_deferred_submissions,
    deferred_aggregate_submission_manifest_fingerprint,
    deferred_results_for_document,
    deferred_submission_manifest_fingerprint,
    deferred_submission_manifest_from_rendered,
    make_soft_call_request_id,
    submit_deferred_aggregate,
    usage_event_from_response,
    validate_deferred_collect_contract,
)
from .executor import AsyncExecutor, Executor, SerialExecutor
from .manifest import RunManifest
from .policy import ExecutorPolicy, PolicySummary
from .prompt_recorder import LocalPromptRecorder
from .reporter import NullReporter
from .runtime import Runtime
from .strategies import IndependentStrategy, IterativeStrategy

__all__ = [
    "AsyncExecutor",
    "DeferredAggregateSubmission",
    "DeferredAggregateSubmissionManifest",
    "DeferredHandle",
    "DeferredPending",
    "DeferredProvider",
    "DeferredRequestRoute",
    "DeferredResults",
    "DeferredSubmission",
    "DeferredSubmissionManifest",
    "Executor",
    "ExecutorPolicy",
    "ExecutionMode",
    "FakeDeferredProvider",
    "IndependentStrategy",
    "IterativeStrategy",
    "LocalPromptRecorder",
    "NullReporter",
    "PolicySummary",
    "RenderedDeferredSubmission",
    "RunManifest",
    "Runtime",
    "SerialExecutor",
    "SoftCallError",
    "SoftCallRequest",
    "SoftCallResponse",
    "SoftCallRouting",
    "TokenCountBudget",
    "adapt_soft_call_response",
    "aggregate_deferred_submissions",
    "deferred_aggregate_submission_manifest_fingerprint",
    "deferred_submission_manifest_from_rendered",
    "deferred_results_for_document",
    "deferred_submission_manifest_fingerprint",
    "make_soft_call_request_id",
    "submit_deferred_aggregate",
    "usage_event_from_response",
    "validate_deferred_collect_contract",
]
