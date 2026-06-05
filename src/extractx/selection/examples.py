"""selector-decision fixtures per ADR-0030."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from extractx.core.objects import CandidateSet, ClassificationContextBinding, Observation
from extractx.schema.summary import FieldSummary

__all__ = [
    "DocumentClassificationReducerPolicy",
    "ExpectedObservation",
    "SelectorExample",
    "SelectorDemo",
    "SelectorDemoSet",
    "SelectorPromptAssetResolver",
    "SelectorPromptPolicy",
    "SelectorScore",
    "export_selector_examples_jsonl",
    "load_selector_examples_jsonl",
    "score_selector_observation",
]


class DocumentClassificationReducerPolicy(BaseModel):
    """Reducer policy for budgeted document-level category classification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["priority", "union"] = "priority"
    priority: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_priority(self) -> DocumentClassificationReducerPolicy:
        if self.strategy == "union":
            if self.priority:
                raise ValueError(
                    "DocumentClassificationReducerPolicy.strategy='union' "
                    "requires priority == ()",
                )
            return self
        if not self.priority:
            raise ValueError("DocumentClassificationReducerPolicy.priority must be non-empty")
        if len(set(self.priority)) != len(self.priority):
            raise ValueError(
                "DocumentClassificationReducerPolicy.priority must not contain duplicates",
            )
        return self


class ExpectedObservation(BaseModel):
    """curated expected output for one selector decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected_candidate_ids: tuple[str, ...]
    abstain: bool
    evidence_id: str | None = None

    @model_validator(mode="after")
    def _check_expected_shape(self) -> ExpectedObservation:
        if self.abstain and self.selected_candidate_ids:
            raise ValueError(
                "ExpectedObservation: abstain=True requires empty selected_candidate_ids",
            )
        if self.evidence_id is not None and self.evidence_id not in self.selected_candidate_ids:
            raise ValueError(
                "ExpectedObservation: evidence_id must be one of selected_candidate_ids",
            )
        if self.evidence_id is None and len(self.selected_candidate_ids) == 1:
            object.__setattr__(self, "evidence_id", self.selected_candidate_ids[0])
        return self


class SelectorExample(BaseModel):
    """portable fixture for evaluating seam-D selector behavior.

    `field_summary` is used instead of a live `FieldSpec` because `FieldSpec`
    carries Python type and callable references that are not portable JSONL.
    A future runner helper can join this summary back to a caller-owned live
    spec by `field_id` / spec version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    example_version: str = "v1"
    document_id: str
    field_id: str
    field_summary: FieldSummary
    candidate_set: CandidateSet
    document_context: str
    expected: ExpectedObservation
    original_observation: Observation | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_example_shape(self) -> SelectorExample:
        if self.example_version != "v1":
            raise ValueError("SelectorExample.example_version must be 'v1'")
        if self.field_summary.field_id != self.field_id:
            raise ValueError(
                "SelectorExample.field_summary.field_id must match field_id",
            )
        if self.candidate_set.field_id != self.field_id:
            raise ValueError(
                "SelectorExample.candidate_set.field_id must match field_id",
            )
        if self.candidate_set.document_id != self.document_id:
            raise ValueError(
                "SelectorExample.candidate_set.document_id must match document_id",
            )
        if self.original_observation is not None and (
            self.original_observation.field_id is not None
            and self.original_observation.field_id != self.field_id
        ):
            raise ValueError(
                "SelectorExample.original_observation.field_id must match field_id",
            )
        return self


class SelectorDemo(BaseModel):
    """worked selector decision rendered into future selector prompts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str
    document_context: str
    candidate_set: CandidateSet
    expected: ExpectedObservation
    note: str | None = None

    @model_validator(mode="after")
    def _check_demo_shape(self) -> SelectorDemo:
        if self.candidate_set.field_id != self.field_id:
            raise ValueError("SelectorDemo.candidate_set.field_id must match field_id")
        return self


class SelectorDemoSet(BaseModel):
    """versioned selector-demo asset resolved by consumer-owned refs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    demo_set_id: str
    version: str
    demos: tuple[SelectorDemo, ...]
    source: str
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelectorPromptPolicy(BaseModel):
    """selector prompt asset refs applied at runtime, outside schema identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instruction_ref: str | None = None
    demo_refs: tuple[str, ...] = ()
    document_context_mode: Literal[
        "full",
        "budgeted_windows",
        "classification_context",
    ] = "full"
    document_window_overlap_chars: int = Field(default=1_000, ge=0)
    document_reducer: DocumentClassificationReducerPolicy | None = None
    classification_context_binding: ClassificationContextBinding | None = None

    @model_validator(mode="after")
    def _check_document_policy(self) -> SelectorPromptPolicy:
        if self.document_context_mode == "budgeted_windows" and self.document_reducer is None:
            raise ValueError(
                "SelectorPromptPolicy.document_context_mode='budgeted_windows' "
                "requires document_reducer",
            )
        if (
            self.document_context_mode == "classification_context"
            and self.classification_context_binding is None
        ):
            raise ValueError(
                "SelectorPromptPolicy.document_context_mode='classification_context' "
                "requires classification_context_binding",
            )
        if (
            self.document_context_mode != "classification_context"
            and self.classification_context_binding is not None
        ):
            raise ValueError(
                "SelectorPromptPolicy.classification_context_binding requires "
                "document_context_mode='classification_context'",
            )
        return self

    @field_serializer("classification_context_binding", when_used="json")
    def _serialize_context_binding(
        self,
        value: ClassificationContextBinding | None,
    ) -> dict[str, object] | None:
        if value is None:
            return None
        cls = value.cls
        module = getattr(cls, "__module__", None)
        qualname = getattr(cls, "__qualname__", None)
        cls_ref = (
            f"{module}.{qualname}"
            if isinstance(module, str) and isinstance(qualname, str)
            else repr(cls)
        )
        return {"cls": cls_ref, "params": dict(value.params)}


class SelectorPromptAssetResolver(Protocol):
    """consumer-owned resolver for selector prompt assets."""

    def resolve_demo_set(self, ref: str) -> SelectorDemoSet: ...

    def resolve_instruction(self, ref: str) -> str: ...


class SelectorScore(BaseModel):
    """default exact-match score for one selector decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correct: bool
    abstain_match: bool
    selected_candidate_ids_match: bool
    evidence_id_match: bool
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def score_selector_observation(
    expected: ExpectedObservation,
    actual: Observation,
) -> SelectorScore:
    """score one actual `Observation` against one expected selector decision."""

    abstain_match = expected.abstain == actual.abstain
    selected_match = expected.selected_candidate_ids == actual.selected_candidate_ids
    evidence_match = expected.evidence_id == actual.evidence_id
    correct = abstain_match and selected_match and evidence_match

    reason: str | None = None
    if not correct:
        mismatches: list[str] = []
        if not abstain_match:
            mismatches.append("abstain")
        if not selected_match:
            mismatches.append("selected_candidate_ids")
        if not evidence_match:
            mismatches.append("evidence_id")
        reason = "selector_score.mismatch: " + ", ".join(mismatches)

    return SelectorScore(
        correct=correct,
        abstain_match=abstain_match,
        selected_candidate_ids_match=selected_match,
        evidence_id_match=evidence_match,
        reason=reason,
        metadata={
            "expected_selected_candidate_ids": expected.selected_candidate_ids,
            "actual_selected_candidate_ids": actual.selected_candidate_ids,
            "expected_evidence_id": expected.evidence_id,
            "actual_evidence_id": actual.evidence_id,
        },
    )


def load_selector_examples_jsonl(path: str | Path) -> tuple[SelectorExample, ...]:
    """load selector examples from newline-delimited JSON."""

    examples: list[SelectorExample] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        examples.append(SelectorExample.model_validate_json(line))
    return tuple(examples)


def export_selector_examples_jsonl(
    examples: Iterable[SelectorExample],
    path: str | Path,
) -> None:
    """write selector examples as newline-delimited JSON."""

    lines = [example.model_dump_json() for example in examples]
    text = "\n".join(lines)
    if text:
        text += "\n"
    Path(path).write_text(text, encoding="utf-8")
