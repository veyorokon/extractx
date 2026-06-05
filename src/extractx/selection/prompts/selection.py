"""selection prompt template per docs/architecture.md §7 seam D.

The selector prompt is deliberately narrow: it classifies among bounded
candidate ids. It does not receive full document text, prior field values,
or cross-field context.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import cast

from extractx.core.objects import Candidate, ContextPack, FieldSpec, Message, RenderedPrompt
from extractx.core.versions import stable_hash

__all__ = [
    "SelectionPrompt",
    "intern_prompt_contexts",
    "render_candidate_lines",
    "render_context_open_tag",
]

type PromptCandidatePayload = dict[str, object]


class SelectionPrompt:
    """render the field-scoped candidate-classification prompt."""

    template_id = "extractx.selection.observation.v2"

    @property
    def template_hash(self) -> str:
        return stable_hash(
            {
                "template_id": self.template_id,
                "contract": "bounded-id-observation",
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
        """render with the protocol-compatible signature.

        `instance_state` is accepted for protocol compatibility. The
        prompt renders retry feedback from `context_pack` when present;
        richer cross-field state remains out of this bounded prompt.
        """

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
        """render a prompt for a bounded field / instance / candidate set."""

        retry_feedback = () if context_pack is None else context_pack.retry_feedback
        prompt_to_canonical = _prompt_candidate_id_map(candidate_summaries)
        canonical_to_prompt = {v: k for k, v in prompt_to_canonical.items()}
        candidate_payload: list[dict[str, object]] = [
            {
                "candidate_id": canonical_to_prompt[candidate.candidate_id],
                "text": candidate.text,
                "source_kind": candidate.source_kind,
                "source_id": candidate.source_id,
                "context": candidate.context,
                "context_span": candidate.context_span.model_dump(mode="json")
                if candidate.context_span is not None
                else None,
                "normalized_span": candidate.normalized_span.model_dump(mode="json")
                if candidate.normalized_span is not None
                else None,
                "entity_type": candidate.entity_type,
                "structured_payload_keys": sorted(
                    candidate.structured_payload.keys(),
                )
                if candidate.structured_payload is not None
                else (),
                "structural_status": candidate.structural_status.model_dump(mode="json")
                if candidate.structural_status is not None
                else None,
                "evidence_span_count": len(candidate.evidence_spans),
            }
            for candidate in candidate_summaries
        ]
        user_message = _render_selection_user_message(
            field={
                "field_id": field_spec.field_id,
                "description": field_spec.description,
                "value_kind": field_spec.value_kind.name,
                "cardinality": field_spec.cardinality.value,
                "python_type": _qualname(field_spec.python_type),
            },
            allowed_instance_ids=tuple(allowed_instance_ids),
            retry_feedback=tuple(retry_feedback),
            candidates=candidate_payload,
        )
        return RenderedPrompt(
            messages=(
                Message(
                    role="system",
                    content=(
                        "Classify the requested field by choosing bounded candidate IDs only. "
                        "Return only the structured output. Do not extract values, normalize "
                        "values, copy candidate text, write spans, invent IDs, or infer domain "
                        "identity. If a candidate matches the requested field, set abstain=false "
                        "and set evidence_id to the exact candidate id shown on that candidate "
                        "block. evidence_id must never be the raw value or text shown in a "
                        "candidate. Set abstain=true only when no bounded candidate matches the "
                        "field; when abstaining, evidence_id must be null. For optional or "
                        "nullable fields, absence is represented by abstain=true and "
                        "evidence_id=null, not by returning \"None\", \"null\", \"N/A\", an "
                        "empty string, or a weak nearby candidate. Use reason only as a "
                        "diagnostic note. When retry_feedback is non-empty, treat it as "
                        "validator feedback about a prior bounded selection attempt and choose "
                        "again from the candidate blocks; do not return the raw value."
                    ),
                ),
                Message(role="user", content=user_message),
            ),
            metadata={
                "prompt_template_id": self.template_id,
                "prompt_template_hash": self.template_hash,
                "allowed_field_ids": (field_spec.field_id,),
                "allowed_instance_ids": tuple(allowed_instance_ids),
                "allowed_evidence_ids": tuple(prompt_to_canonical),
                "canonical_allowed_evidence_ids": tuple(prompt_to_canonical.values()),
                "prompt_candidate_id_map": prompt_to_canonical,
                "prompt_contexts_by_field": {
                    field_spec.field_id: intern_prompt_contexts(candidate_payload)[1],
                },
            },
        )


def _render_selection_user_message(
    *,
    field: dict[str, object],
    allowed_instance_ids: tuple[str, ...],
    retry_feedback: tuple[str, ...],
    candidates: Sequence[PromptCandidatePayload],
) -> str:
    rendered_candidates, contexts = intern_prompt_contexts(candidates)
    lines: list[str] = [
        "<task>",
        "Choose the candidate_id that best answers this field. Do not write values.",
        "</task>",
        "",
        "<selection_procedure>",
        "1. Read the field description and value_kind.",
        "2. Review only this field's candidate blocks and linked contexts.",
        "3. Pick the candidate whose text and context satisfy the field description.",
        "4. If no bounded candidate satisfies the field, abstain.",
        "5. Return the chosen candidate id exactly as shown; never return the value text.",
        "</selection_procedure>",
        "",
        "<output_rules>",
        "Return structured output only.",
        (
            "For a match: abstain=false, evidence_id=<candidate id>, "
            "selected_candidate_ids=[<candidate id>]."
        ),
        "For no match: abstain=true, evidence_id=null, selected_candidate_ids=[].",
        (
            "Invalid patterns: raw values as evidence_id, invented candidate ids, "
            "empty selected ids with abstain=false."
        ),
        "</output_rules>",
        "",
        "<output_example>",
        (
            '{"instance_id":"inst_0","field_id":"example_field","evidence_id":"c001",'
            '"selected_candidate_ids":["c001"],"abstain":false,'
            '"reason":"candidate c001 matches the field"}'
        ),
        (
            '{"instance_id":"inst_0","field_id":"example_field","evidence_id":null,'
            '"selected_candidate_ids":[],"abstain":true,'
            '"reason":"no bounded candidate matches"}'
        ),
        "</output_example>",
        "",
        "<allowed_instance_ids>",
        ", ".join(allowed_instance_ids),
        "</allowed_instance_ids>",
        "",
    ]
    if retry_feedback:
        lines.extend(("<retry_feedback>", *retry_feedback, "</retry_feedback>", ""))
    lines.extend(
        (
            f'<field id="{field["field_id"]}">',
            f"description: {field['description']}",
            f"value_kind: {field['value_kind']}",
            f"cardinality: {field['cardinality']}",
            f"python_type: {field['python_type']}",
        ),
    )
    lines.extend(_render_contexts(contexts))
    lines.append("<candidates>")
    for candidate in rendered_candidates:
        lines.extend(render_candidate_lines(candidate))
    lines.extend(("</candidates>", "</field>"))
    return "\n".join(lines)


def intern_prompt_contexts(
    candidates: Sequence[PromptCandidatePayload],
) -> tuple[list[PromptCandidatePayload], dict[str, dict[str, object]]]:
    span_result = _intern_span_contexts(candidates)
    if span_result is not None:
        return span_result

    contexts: dict[str, dict[str, object]] = {}
    normalized_to_context_id: dict[str, str] = {}
    rendered_candidates: list[PromptCandidatePayload] = []

    for candidate in candidates:
        rendered = dict(candidate)
        context = str(candidate.get("context") or "")
        normalized = _normalize_context_for_interning(context)
        if normalized:
            context_id = _context_id_for(
                context=context,
                normalized=normalized,
                contexts=contexts,
                normalized_to_context_id=normalized_to_context_id,
            )
            rendered["context_id"] = context_id
            rendered["context"] = None
            cast("list[str]", contexts[context_id]["candidate_ids"]).append(
                str(candidate["candidate_id"]),
            )
        rendered_candidates.append(rendered)

    return rendered_candidates, contexts


def _intern_span_contexts(
    candidates: Sequence[PromptCandidatePayload],
) -> tuple[list[PromptCandidatePayload], dict[str, dict[str, object]]] | None:
    span_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate.get("context_span"), dict)
        and isinstance(candidate.get("normalized_span"), dict)
        and candidate.get("context")
    ]
    if len(span_candidates) != len(candidates):
        return None

    groups: dict[tuple[str, str, str], list[PromptCandidatePayload]] = {}
    for candidate in candidates:
        context_span = cast("dict[str, object]", candidate["context_span"])
        source_ref = cast("dict[str, object]", context_span["source_ref"])
        key = (
            str(source_ref["source_id"]),
            str(source_ref["content_hash"]),
            str(context_span["text_anchor_space"]),
        )
        groups.setdefault(key, []).append(candidate)

    contexts: dict[str, dict[str, object]] = {}
    rendered_candidates: list[PromptCandidatePayload] = []
    candidate_to_context: dict[str, tuple[str, int, int]] = {}

    for group in groups.values():
        intervals = sorted(
            group,
            key=lambda candidate: (
                _int_mapping_value(
                    cast("dict[str, object]", candidate["context_span"]),
                    "byte_start",
                ),
                _int_mapping_value(
                    cast("dict[str, object]", candidate["context_span"]),
                    "byte_end",
                ),
            ),
        )
        current: dict[str, object] | None = None
        current_candidates: list[PromptCandidatePayload] = []

        def flush(
            context_window: dict[str, object] | None,
            window_candidates: Sequence[PromptCandidatePayload],
        ) -> None:
            if context_window is None:
                return
            context_id = f"ctx{len(contexts) + 1:03d}"
            merged = _merged_context_with_inline_anchors(
                current=context_window,
                candidates=window_candidates,
            )
            contexts[context_id] = {
                **context_window,
                "text": merged,
                "candidate_ids": [str(c["candidate_id"]) for c in window_candidates],
            }
            for candidate in window_candidates:
                normalized_span = cast("dict[str, object]", candidate["normalized_span"])
                context_start = _int_mapping_value(context_window, "byte_start")
                local_start = _int_mapping_value(normalized_span, "byte_start") - context_start
                local_end = _int_mapping_value(normalized_span, "byte_end") - context_start
                candidate_to_context[str(candidate["candidate_id"])] = (
                    context_id,
                    local_start,
                    local_end,
                )

        for candidate in intervals:
            context_span = cast("dict[str, object]", candidate["context_span"])
            start = _int_mapping_value(context_span, "byte_start")
            end = _int_mapping_value(context_span, "byte_end")
            if current is None:
                current = dict(context_span)
                current["text"] = str(candidate["context"])
                current_candidates = [candidate]
                continue
            current_end = _int_mapping_value(current, "byte_end")
            if start <= current_end:
                current["text"] = _merge_overlapping_context_text(
                    left_text=str(current["text"]),
                    left_start=_int_mapping_value(current, "byte_start"),
                    left_end=current_end,
                    right_text=str(candidate["context"]),
                    right_start=start,
                    right_end=end,
                )
                current["byte_end"] = max(current_end, end)
                current_candidates.append(candidate)
            else:
                flush(current, current_candidates)
                current = dict(context_span)
                current["text"] = str(candidate["context"])
                current_candidates = [candidate]
        flush(current, current_candidates)

    for candidate in candidates:
        rendered = dict(candidate)
        context_ref = candidate_to_context.get(str(candidate["candidate_id"]))
        if context_ref is not None:
            context_id, local_start, local_end = context_ref
            rendered["context_id"] = context_id
            rendered["local_span"] = f"{local_start}:{local_end}"
            rendered["context"] = None
        rendered_candidates.append(rendered)

    return rendered_candidates, contexts


def _merge_overlapping_context_text(
    *,
    left_text: str,
    left_start: int,
    left_end: int,
    right_text: str,
    right_start: int,
    right_end: int,
) -> str:
    left_bytes = left_text.encode("utf-8")
    right_bytes = right_text.encode("utf-8")
    if right_end <= left_end:
        return left_text
    overlap = max(0, left_end - right_start)
    merged = left_bytes + right_bytes[overlap:]
    return merged.decode("utf-8", errors="replace")


def _merged_context_with_inline_anchors(
    *,
    current: dict[str, object],
    candidates: Sequence[PromptCandidatePayload],
) -> str:
    context_bytes = str(current["text"]).encode("utf-8")
    context_start = _int_mapping_value(current, "byte_start")
    edits: list[tuple[int, int, str, str, str]] = []
    for candidate in candidates:
        normalized_span = cast("dict[str, object]", candidate["normalized_span"])
        local_start = _int_mapping_value(normalized_span, "byte_start") - context_start
        local_end = _int_mapping_value(normalized_span, "byte_end") - context_start
        if local_start < 0 or local_end > len(context_bytes) or local_end < local_start:
            continue
        candidate_id = str(candidate["candidate_id"])
        edits.append(
            (
                local_start,
                local_end,
                f'<cand id="{candidate_id}">',
                "</cand>",
                candidate_id,
            ),
        )
    for local_start, local_end, open_tag, close_tag, _candidate_id in sorted(
        edits,
        key=lambda item: item[0],
        reverse=True,
    ):
        context_bytes = (
            context_bytes[:local_start]
            + open_tag.encode("utf-8")
            + context_bytes[local_start:local_end]
            + close_tag.encode("utf-8")
            + context_bytes[local_end:]
        )
    return context_bytes.decode("utf-8", errors="replace")


def _int_mapping_value(mapping: Mapping[str, object], key: str) -> int:
    value = mapping[key]
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"{key} must be int-compatible")


def render_candidate_lines(candidate: PromptCandidatePayload) -> list[str]:
    lines = [
        f'<candidate id="{candidate["candidate_id"]}">',
        f"text: {candidate['text']}",
        f"entity_type: {candidate['entity_type']}",
        f"source_kind: {candidate['source_kind']}",
        f"source_id: {candidate['source_id']}",
        f"evidence_span_count: {candidate['evidence_span_count']}",
    ]
    context_id = candidate.get("context_id")
    if context_id:
        lines.append(f"context_id: {context_id}")
    local_span = candidate.get("local_span")
    if local_span:
        lines.append(f"local_span: {local_span}")
    structured_payload_keys = candidate["structured_payload_keys"]
    if isinstance(structured_payload_keys, (list, tuple)):
        keys = cast("Sequence[object]", structured_payload_keys)
        lines.append(
            "structured_payload_keys: "
            + ", ".join(str(key) for key in keys),
        )
    structural_status = candidate["structural_status"]
    if structural_status is not None:
        lines.append(
            "structural_status: "
            + json.dumps(
                structural_status,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
    context = candidate["context"]
    if context:
        lines.extend(("<context>", str(context), "</context>"))
    lines.append("</candidate>")
    return lines


def _render_contexts(contexts: dict[str, dict[str, object]]) -> list[str]:
    if not contexts:
        return []
    lines: list[str] = ["<contexts>"]
    for context_id, context in contexts.items():
        lines.extend(
            (
                render_context_open_tag(context_id, context),
                str(context["text"]),
                "</context>",
            ),
        )
    lines.append("</contexts>")
    return lines


def render_context_open_tag(context_id: str, context: Mapping[str, object]) -> str:
    if "byte_start" in context and "byte_end" in context:
        return (
            f'<context id="{context_id}" '
            f'source_span="{_int_mapping_value(context, "byte_start")}:'
            f'{_int_mapping_value(context, "byte_end")}">'
        )
    return f'<context id="{context_id}">'


def _normalize_context_for_interning(context: str) -> str:
    return " ".join(context.split())


def _context_id_for(
    *,
    context: str,
    normalized: str,
    contexts: dict[str, dict[str, object]],
    normalized_to_context_id: dict[str, str],
) -> str:
    exact = normalized_to_context_id.get(normalized)
    if exact is not None:
        return exact

    for existing_normalized, existing_id in tuple(normalized_to_context_id.items()):
        if normalized in existing_normalized:
            normalized_to_context_id[normalized] = existing_id
            return existing_id
        if existing_normalized in normalized:
            contexts[existing_id]["text"] = context
            contexts[existing_id]["normalized_text"] = normalized
            del normalized_to_context_id[existing_normalized]
            normalized_to_context_id[normalized] = existing_id
            return existing_id

    context_id = f"ctx{len(contexts) + 1:03d}"
    contexts[context_id] = {
        "text": context,
        "normalized_text": normalized,
        "candidate_ids": [],
    }
    normalized_to_context_id[normalized] = context_id
    return context_id


def _prompt_candidate_id_map(candidates: tuple[Candidate, ...]) -> dict[str, str]:
    width = max(3, len(str(len(candidates))))
    return {
        f"c{index:0{width}d}": candidate.candidate_id
        for index, candidate in enumerate(candidates, start=1)
    }


def _qualname(obj: object) -> str:
    module = getattr(obj, "__module__", None)
    qualname = getattr(obj, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return f"{module}.{qualname}"
    return repr(obj)
