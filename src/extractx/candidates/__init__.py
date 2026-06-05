"""candidate subsystem per docs/architecture.md §7 seam C (and optional C.alt).

houses `CandidateStrategy`, `CandidateSorter`, and `GroundedProposalGenerator`
implementations. canonical objects (`Candidate`, `CandidateSet`) live in
`extractx.core.objects`; this package exports only the seam-C-local helpers
and the phase-1 generator surface.
"""

from __future__ import annotations

from .candidate_set import (
    build_candidate_set,
    candidate_id_for,
    validate_source_span_against_view,
)
from .context import (
    DEFAULT_CONTEXT_WINDOW_BYTES,
    ByteWindowCandidateContextBuilder,
    CandidateContextBuilder,
)
from .filters import (
    And,
    ContainedBy,
    Contains,
    ContextContains,
    LabelIn,
    LabelNotIn,
    Not,
    NumericRange,
    Or,
    apply_filter_binding,
    filter_candidate_set,
)
from .generators import (
    LiteralSetCandidateStrategy,
    NerCandidateStrategy,
    NerEntityRulerConfig,
    NerStrategyParams,
    RegexCandidateStrategy,
    RegexStrategyParams,
)
from .selection_gate import AutoSelection, DeterministicSelectionGate
from .structured_contracts import (
    NamedPredicate,
    StructuredContractError,
    evaluate_structured_contract,
    evaluate_structured_payload,
)

__all__ = [
    "AutoSelection",
    "DEFAULT_CONTEXT_WINDOW_BYTES",
    "ByteWindowCandidateContextBuilder",
    "CandidateContextBuilder",
    "DeterministicSelectionGate",
    "And",
    "ContainedBy",
    "Contains",
    "ContextContains",
    "LabelIn",
    "LabelNotIn",
    "NamedPredicate",
    "LiteralSetCandidateStrategy",
    "NerCandidateStrategy",
    "NerEntityRulerConfig",
    "NerStrategyParams",
    "Not",
    "NumericRange",
    "Or",
    "RegexCandidateStrategy",
    "RegexStrategyParams",
    "StructuredContractError",
    "apply_filter_binding",
    "build_candidate_set",
    "candidate_id_for",
    "evaluate_structured_contract",
    "evaluate_structured_payload",
    "filter_candidate_set",
    "validate_source_span_against_view",
]
