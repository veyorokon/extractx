"""pydantic-ai integration per ADR-0002.

houses `PydanticAISelector` (default llm-backed `Selector`) and the
`InterviewTranscript` capture machinery that powers
`Extraction.interview()`.
"""

from __future__ import annotations

from .instance_proposer import (
    InstanceProposalResponse,
    InstanceProposerOutputMalformedError,
    LLMInstanceProposer,
)
from .openai import PydanticAIOpenAIProvider, StructuredOutputMode
from .openai_deferred import OpenAIDeferredProvider
from .selector import (
    BatchSelectorObservationResponse,
    PydanticAIBatchSelector,
    PydanticAISelector,
    SelectorObservationResponse,
    SelectorOutputMalformedError,
)

__all__ = [
    "OpenAIDeferredProvider",
    "PydanticAIOpenAIProvider",
    "StructuredOutputMode",
    "PydanticAIBatchSelector",
    "PydanticAISelector",
    "BatchSelectorObservationResponse",
    "InstanceProposalResponse",
    "InstanceProposerOutputMalformedError",
    "LLMInstanceProposer",
    "SelectorObservationResponse",
    "SelectorOutputMalformedError",
]
