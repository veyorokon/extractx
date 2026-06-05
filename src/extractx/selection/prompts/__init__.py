"""`Prompt` templates per docs/architecture.md §9.

prompt text lives in `Prompt` implementations, not in scattered string
concatenation.
"""

from __future__ import annotations

from .classification import ClassificationPrompt
from .selection import (
    SelectionPrompt,
    intern_prompt_contexts,
    render_candidate_lines,
    render_context_open_tag,
)

__all__ = [
    "ClassificationPrompt",
    "SelectionPrompt",
    "intern_prompt_contexts",
    "render_candidate_lines",
    "render_context_open_tag",
]
