"""contract tests for `Extraction.stream()` and remaining projection methods.

proof targets:
- `.stream()` is a post-hoc async iterator over `self.instances`.
- `.usage()` returns captured soft-compute usage events.
- `.interview()` remains a typed stub naming the owning seam.
"""

from __future__ import annotations

import pytest

from extractx.core import (
    ExecutionTrace,
    Extraction,
    GroupingEvidence,
    Instance,
    InstanceGroupingKey,
    SourceRef,
    SourceSpan,
)


def _span() -> SourceSpan:
    return SourceSpan(
        source_ref=SourceRef(source_id="d", content_hash="h"),
        text_anchor_space="source_bytes",
        byte_start=0,
        byte_end=1,
    )


def _instance(group_id: str, ordinal: int) -> Instance:
    return Instance(
        instance_key=InstanceGroupingKey(
            group_id=group_id,
            ordinal=ordinal,
            group_anchors=(_span(),),
        ),
        outcome="complete",
        grouping_evidence=GroupingEvidence(
            stage="resolved",
            anchor_spans=(_span(),),
            producer_version="code:abc",
        ),
    )


def _result(instances: tuple[Instance, ...]) -> Extraction:
    return Extraction(
        document_id="doc-1",
        spec_version="v1",
        outcome="complete",
        strategy="independent",
        instances=instances,
        trace=ExecutionTrace(trace_id="t1"),
        replay_artifact_ref="artifact://1",
    )


async def test_stream_yields_instances_in_order() -> None:
    a = _instance("g1", 0)
    b = _instance("g2", 1)
    result = _result((a, b))

    seen: list[Instance] = []
    async for inst in result.stream():
        seen.append(inst)
    assert seen == [a, b]


def test_usage_returns_captured_events() -> None:
    result = _result(())
    assert result.usage() == ()


def test_interview_is_stub() -> None:
    result = _result(())
    with pytest.raises(NotImplementedError, match="extras/pydantic_ai"):
        result.interview(field_id="total", question="why?")
