"""Classification-context retrieval strategies.

These strategies produce non-selectable evidence windows for CATEGORY selector
prompts. They intentionally do not produce `Candidate` objects.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from extractx.core import (
    ClassificationContextOverflowMetadata,
    ClassificationContextSet,
    ClassificationContextWindow,
    DocumentView,
    FieldSpec,
    SourceSpan,
)
from extractx.core.exceptions import InfrastructureError
from extractx.core.versions import stable_hash

__all__ = ["RegexWindowClassificationContextStrategy"]


class RegexWindowClassificationContextStrategy(BaseModel):
    """Build classification context windows around regex matches."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: tuple[str, ...]
    before_chars: int = Field(default=450, ge=0)
    after_chars: int = Field(default=750, ge=0)
    max_window_chars: int = Field(default=1_500, gt=0)
    max_total_chars: int | None = Field(default=10_000, gt=0)
    max_windows: int | None = Field(default=12, gt=0)
    boundary_mode: Literal["none", "line", "paragraph", "punctuation"] = "paragraph"
    ignore_case: bool = True
    multiline: bool = True
    strategy_id: str = "regex_window_classification_context:v1"
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_patterns(self) -> RegexWindowClassificationContextStrategy:
        if not self.patterns:
            raise ValueError("RegexWindowClassificationContextStrategy.patterns is required")
        for pattern in self.patterns:
            if not pattern:
                raise ValueError(
                    "RegexWindowClassificationContextStrategy.patterns must be non-empty",
                )
            try:
                re.compile(pattern, self.flags)
            except re.error as exc:
                raise ValueError(
                    "RegexWindowClassificationContextStrategy.patterns contains "
                    f"invalid regex {pattern!r}: {exc}",
                ) from exc
        return self

    @property
    def flags(self) -> int:
        flags = 0
        if self.ignore_case:
            flags |= re.IGNORECASE
        if self.multiline:
            flags |= re.MULTILINE
        return flags

    def generate(
        self,
        field_spec: FieldSpec,
        document_view: DocumentView,
    ) -> ClassificationContextSet:
        if field_spec.value_kind.name != "CATEGORY":
            raise InfrastructureError(
                "classification_context.invalid_field: field "
                f"{field_spec.field_id!r} is not ValueKind.CATEGORY",
            )
        text = document_view.normalized_text
        matches = _regex_matches(text=text, patterns=self.patterns, flags=self.flags)
        raw_windows = [
            self._window_from_match(
                text=text,
                match=match,
                field_spec=field_spec,
                document_view=document_view,
                rank=rank,
            )
            for rank, match in enumerate(matches, start=1)
        ]
        windows = _dedupe_windows(raw_windows)
        source_window_count = len(windows)
        overflow: ClassificationContextOverflowMetadata | None = None

        if self.max_windows is not None and len(windows) > self.max_windows:
            windows = windows[: self.max_windows]
            overflow = ClassificationContextOverflowMetadata(
                source_window_count=source_window_count,
                presented_window_count=len(windows),
                max_windows=self.max_windows,
                max_total_chars=self.max_total_chars,
                overflow_policy="truncate_ranked",
            )

        if self.max_total_chars is not None:
            bounded: list[ClassificationContextWindow] = []
            total = 0
            for window in windows:
                next_total = total + len(window.text)
                if next_total > self.max_total_chars:
                    break
                bounded.append(window)
                total = next_total
            if len(bounded) != len(windows):
                windows = tuple(bounded)
                overflow = ClassificationContextOverflowMetadata(
                    source_window_count=source_window_count,
                    presented_window_count=len(windows),
                    max_windows=self.max_windows,
                    max_total_chars=self.max_total_chars,
                    overflow_policy="truncate_ranked",
                )

        return ClassificationContextSet(
            field_id=field_spec.field_id,
            document_id=document_view.document_id,
            strategy_id=self.strategy_id,
            windows=tuple(windows),
            overflow=overflow,
        )

    def _window_from_match(
        self,
        *,
        text: str,
        match: _RegexMatch,
        field_spec: FieldSpec,
        document_view: DocumentView,
        rank: int,
    ) -> ClassificationContextWindow:
        start = max(0, match.start - self.before_chars)
        end = min(len(text), match.end + self.after_chars)
        start, end = _snap_bounds(
            text=text,
            start=start,
            end=end,
            mode=self.boundary_mode,
            max_window_chars=self.max_window_chars,
            match_start=match.start,
            match_end=match.end,
        )
        window_text = text[start:end]
        byte_start = len(text[:start].encode("utf-8"))
        byte_end = len(text[:end].encode("utf-8"))
        window_id = stable_hash(
            {
                "field_id": field_spec.field_id,
                "strategy_id": self.strategy_id,
                "source_ref": document_view.source_ref.model_dump(mode="json"),
                "byte_start": byte_start,
                "byte_end": byte_end,
                "matched_terms": match.terms,
            },
        )
        return ClassificationContextWindow(
            window_id=window_id,
            field_id=field_spec.field_id,
            text=window_text,
            source_kind="text",
            source_id=self.strategy_id,
            source_span=SourceSpan(
                source_ref=document_view.source_ref,
                text_anchor_space="normalized_text",
                byte_start=byte_start,
                byte_end=byte_end,
            ),
            matched_terms=match.terms,
            strategy_id=self.strategy_id,
            rank=rank,
            metadata={
                **dict(self.metadata),
                "pattern": match.pattern,
                "match_start_char": match.start,
                "match_end_char": match.end,
            },
        )


class _RegexMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int
    end: int
    terms: tuple[str, ...]
    pattern: str


def _regex_matches(
    *,
    text: str,
    patterns: Sequence[str],
    flags: int,
) -> tuple[_RegexMatch, ...]:
    matches: list[_RegexMatch] = []
    for pattern in patterns:
        compiled = re.compile(pattern, flags)
        for match in compiled.finditer(text):
            matches.append(
                _RegexMatch(
                    start=match.start(),
                    end=match.end(),
                    terms=(match.group(0),),
                    pattern=pattern,
                ),
            )
    return tuple(sorted(matches, key=lambda item: (item.start, item.end, item.pattern)))


def _dedupe_windows(
    windows: Sequence[ClassificationContextWindow],
) -> tuple[ClassificationContextWindow, ...]:
    seen: set[tuple[int, int, str]] = set()
    out: list[ClassificationContextWindow] = []
    for window in windows:
        key = (
            window.source_span.byte_start,
            window.source_span.byte_end,
            "|".join(window.matched_terms),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(window.model_copy(update={"rank": len(out) + 1}))
    return tuple(out)


def _snap_bounds(
    *,
    text: str,
    start: int,
    end: int,
    mode: Literal["none", "line", "paragraph", "punctuation"],
    max_window_chars: int,
    match_start: int,
    match_end: int,
) -> tuple[int, int]:
    if mode == "paragraph":
        start = _previous_boundary(text, start, "\n\n")
        end = _next_boundary(text, end, "\n\n")
    elif mode == "line":
        start = _previous_boundary(text, start, "\n")
        end = _next_boundary(text, end, "\n")
    elif mode == "punctuation":
        start = _previous_punctuation_boundary(text, start)
        end = _next_punctuation_boundary(text, end)
    return _trim_around_match(
        start=start,
        end=end,
        max_window_chars=max_window_chars,
        match_start=match_start,
        match_end=match_end,
    )


def _previous_boundary(text: str, start: int, marker: str) -> int:
    index = text.rfind(marker, 0, start)
    return 0 if index < 0 else index + len(marker)


def _next_boundary(text: str, end: int, marker: str) -> int:
    index = text.find(marker, end)
    return len(text) if index < 0 else index


def _previous_punctuation_boundary(text: str, start: int) -> int:
    for index in range(start - 1, -1, -1):
        if text[index] in ".?!;":
            return min(len(text), index + 1)
    return 0


def _next_punctuation_boundary(text: str, end: int) -> int:
    for index in range(end, len(text)):
        if text[index] in ".?!;":
            return index + 1
    return len(text)


def _trim_around_match(
    *,
    start: int,
    end: int,
    max_window_chars: int,
    match_start: int,
    match_end: int,
) -> tuple[int, int]:
    if end - start <= max_window_chars:
        return start, end
    available_before = max(0, match_start - start)
    available_after = max(0, end - match_end)
    match_len = max(0, match_end - match_start)
    remaining = max(0, max_window_chars - match_len)
    before = min(available_before, remaining // 2)
    after = min(available_after, remaining - before)
    if before + after < remaining:
        extra = remaining - before - after
        before += min(available_before - before, extra)
        extra = remaining - before - after
        after += min(available_after - after, extra)
    return max(0, match_start - before), min(end, match_end + after)
