"""public tier-1 end-user surface.

this module exposes the stable tier-1 types and entrypoints per
`docs/architecture.md` §10. M8 phase 1 widens the surface with
`Runtime` and `ExecutorPolicy` per the brief's section 8.

tier-2 plugin types are imported from their canonical modules directly
(e.g. `from extractx.core.contracts import Selector`).
"""

from __future__ import annotations

from .api import (
    collect_deferred_submission,
    extract,
    extract_one,
    render_deferred_submission,
    run_extraction,
)
from .core.anchors import SourceSpan, slice_utf8_byte_span, utf8_byte_span_to_char_range
from .core.cardinality import Cardinality
from .core.exceptions import (
    CapabilityError,
    ExtractionFailed,
    InfrastructureError,
    InterviewError,
    SpecError,
)
from .core.filters import (
    And,
    ContainedBy,
    Contains,
    ContextContains,
    LabelIn,
    LabelNotIn,
    Not,
    NumericRange,
    Or,
)
from .core.objects import ExtractionSpec, FilterBinding
from .core.outcomes import (
    Evidence,
    Extraction,
    FieldRef,
    Instance,
    NegativeOutcome,
    ObjectIssue,
)
from .core.value_kinds import ValueKind
from .execution.deferred import (
    DeferredAggregateSubmission,
    DeferredSubmission,
    RenderedDeferredSubmission,
    deferred_results_for_document,
    submit_deferred_aggregate,
)
from .execution.policy import ExecutorPolicy
from .execution.prompt_recorder import LocalPromptRecorder
from .execution.runtime import Runtime
from .schema import extract_field, extractx_object_validator
from .types import (
    Bool,
    Cardinal,
    Category,
    Date,
    Gpe,
    Money,
    Ordinal,
    Org,
    Percent,
    Person,
)

__version__ = "0.1.0"

__all__ = [
    "Bool",
    "Cardinal",
    "Cardinality",
    "Category",
    "And",
    "CapabilityError",
    "collect_deferred_submission",
    "ContainedBy",
    "Contains",
    "ContextContains",
    "Date",
    "DeferredAggregateSubmission",
    "DeferredSubmission",
    "ExecutorPolicy",
    "Evidence",
    "Extraction",
    "ExtractionFailed",
    "ExtractionSpec",
    "FieldRef",
    "FilterBinding",
    "Gpe",
    "InfrastructureError",
    "Instance",
    "InterviewError",
    "LabelIn",
    "LabelNotIn",
    "LocalPromptRecorder",
    "Money",
    "NegativeOutcome",
    "ObjectIssue",
    "Not",
    "NumericRange",
    "Ordinal",
    "Org",
    "Or",
    "Percent",
    "Person",
    "Runtime",
    "RenderedDeferredSubmission",
    "SourceSpan",
    "SpecError",
    "ValueKind",
    "extract",
    "extract_field",
    "extractx_object_validator",
    "extract_one",
    "deferred_results_for_document",
    "render_deferred_submission",
    "run_extraction",
    "slice_utf8_byte_span",
    "submit_deferred_aggregate",
    "utf8_byte_span_to_char_range",
]
