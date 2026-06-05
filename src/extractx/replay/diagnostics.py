"""Typed replay diagnostics for selector-call seams.

These records are structural diagnostics, not prompt transcript storage. They
identify which bounded candidates were presented to a selector call and how the
response was normalized into canonical `Observation` objects. Prompt and
response bodies are referenced by stable refs / hashes when available.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from extractx.core.objects import Observation, UsageEvent

__all__ = ["SelectorCallDiagnostic"]


class SelectorCallDiagnostic(BaseModel):
    """One selector-call diagnostic captured at the selector seam."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnostic_schema_version: Literal["v1"] = "v1"
    seam: Literal["selector", "batch_selector", "selection_gate"]
    decision_kind: Literal["llm", "auto_selected", "no_candidates", "shard_reducer"]

    document_id: str
    spec_version: str
    field_ids: tuple[str, ...]
    instance_ids: tuple[str, ...] = ()

    batch_index: int | None = None
    batch_count: int | None = None
    shard_index: int | None = None
    shard_count: int | None = None
    window_index: int | None = None
    window_count: int | None = None
    reducer_round: int | None = None

    candidate_count_by_field: Mapping[str, int]
    presented_candidate_ids_by_field: Mapping[str, tuple[str, ...]]
    presented_count_by_field: Mapping[str, int]
    allowed_evidence_ids_by_field: Mapping[str, tuple[str, ...]]

    prompt_candidate_id_map_by_field: Mapping[str, Mapping[str, str]] = Field(
        default_factory=dict,
    )
    prompt_field_id_map: Mapping[str, str] = Field(default_factory=dict)
    classification_context_by_field: Mapping[str, object] = Field(default_factory=dict)
    category_signals: tuple[Mapping[str, object], ...] = ()

    rendered_prompt_hash: str | None = None
    rendered_prompt_ref: str | None = None
    estimated_prompt_chars: int | None = None
    max_prompt_chars: int | None = None

    selector_response_before_translation_hash: str | None = None
    selector_response_before_translation_ref: str | None = None
    selector_response_after_translation_hash: str | None = None
    selector_response_after_translation_ref: str | None = None

    final_observations: tuple[Observation, ...]
    usage_event: UsageEvent | None = None
    model_metadata: Mapping[str, object] = Field(default_factory=dict)
