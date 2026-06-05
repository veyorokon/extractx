"""document-level classification prompt for Literal candidate sets."""

from __future__ import annotations

import json
from collections.abc import Sequence

from extractx.core.objects import Candidate, ContextPack, FieldSpec, Message, RenderedPrompt
from extractx.core.versions import stable_hash

__all__ = ["ClassificationPrompt"]


class ClassificationPrompt:
    """render a bounded Literal-set document classification prompt."""

    template_id = "extractx.selection.classification.v2"

    @property
    def template_hash(self) -> str:
        return stable_hash(
            {
                "template_id": self.template_id,
                "contract": "bounded-literal-classification",
                "version": 2,
            },
        )

    def render(
        self,
        field_spec: FieldSpec,
        candidate_summaries: tuple[Candidate, ...],
        context_pack: ContextPack,
        instance_state: object | None,
    ) -> RenderedPrompt:
        del instance_state
        return self.render_for_ids(
            field_spec=field_spec,
            candidate_summaries=candidate_summaries,
            allowed_instance_ids=("inst_0",),
            context_pack=context_pack,
        )

    def render_for_ids(
        self,
        *,
        field_spec: FieldSpec,
        candidate_summaries: tuple[Candidate, ...],
        allowed_instance_ids: Sequence[str],
        context_pack: ContextPack | None = None,
    ) -> RenderedPrompt:
        candidate_payload = [
            {
                "candidate_id": candidate.candidate_id,
                "literal": candidate.structured_payload["literal"]
                if candidate.structured_payload is not None
                else candidate.text,
                "text": candidate.text,
                "source_kind": candidate.source_kind,
                "source_id": candidate.source_id,
                "structural_status": candidate.structural_status.model_dump(mode="json")
                if candidate.structural_status is not None
                else None,
            }
            for candidate in candidate_summaries
        ]
        classification_context = _classification_context_payload(
            context_pack,
            field_spec.field_id,
        ) if context_pack is not None else ()
        user_payload = {
            "task": "document_classification",
            "field": {
                "field_id": field_spec.field_id,
                "description": field_spec.description,
                "value_kind": field_spec.value_kind.name,
                "cardinality": field_spec.cardinality.value,
                "python_type": _qualname(field_spec.python_type),
            },
            "allowed_instance_ids": tuple(allowed_instance_ids),
            "allowed_evidence_ids": tuple(
                candidate.candidate_id for candidate in candidate_summaries
            ),
            "document_context": ""
            if context_pack is None or classification_context
            else context_pack.document_summary,
            "classification_context": classification_context,
            "retry_feedback": ()
            if context_pack is None
            else tuple(context_pack.retry_feedback),
            "candidates": candidate_payload,
        }
        user_message = json.dumps(
            user_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return RenderedPrompt(
            messages=(
                Message(
                    role="system",
                    content=(
                        "Classify the document by selecting bounded literal candidate ids. "
                        "Return only the structured output. Do not invent ids or author "
                        "values. For single-label fields choose exactly one candidate unless "
                        "the document is insufficient. For multi-label fields choose every "
                        "candidate supported by the document. If no labels apply to a "
                        "multi-label field, return abstain=false with selected_candidate_ids=[]; "
                        "that means the correct answer is an empty set. Use abstain=true only "
                        "when the document does not contain enough information to decide. Use "
                        "reason only as a diagnostic note. When retry_feedback is non-empty, "
                        "treat it as validator feedback about a prior bounded selection attempt "
                        "and choose again from allowed_evidence_ids."
                    ),
                ),
                Message(role="user", content=user_message),
            ),
            metadata={
                "prompt_template_id": self.template_id,
                "prompt_template_hash": self.template_hash,
                "allowed_field_ids": (field_spec.field_id,),
                "allowed_instance_ids": tuple(allowed_instance_ids),
                "allowed_evidence_ids": tuple(
                    candidate.candidate_id for candidate in candidate_summaries
                ),
                "classification_context_window_ids": ()
                if context_pack is None
                else _classification_context_window_ids(context_pack, field_spec.field_id),
            },
        )


def _qualname(obj: object) -> str:
    module = getattr(obj, "__module__", None)
    qualname = getattr(obj, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return f"{module}.{qualname}"
    return repr(obj)


def _classification_context_payload(
    context_pack: ContextPack,
    field_id: str,
) -> tuple[dict[str, object], ...]:
    context_set = context_pack.classification_context_by_field.get(field_id)
    if context_set is None:
        return ()
    return tuple(
        {
            "window_id": window.window_id,
            "text": window.text,
            "source_kind": window.source_kind,
            "source_id": window.source_id,
            "source_span": window.source_span.model_dump(mode="json"),
            "matched_terms": window.matched_terms,
            "rank": window.rank,
            "metadata": dict(window.metadata),
        }
        for window in context_set.windows
    )


def _classification_context_window_ids(
    context_pack: ContextPack,
    field_id: str,
) -> tuple[str, ...]:
    context_set = context_pack.classification_context_by_field.get(field_id)
    if context_set is None:
        return ()
    return tuple(window.window_id for window in context_set.windows)
