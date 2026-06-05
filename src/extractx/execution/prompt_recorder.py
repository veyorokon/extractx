"""Prompt recording helpers for opt-in soft-compute forensics."""

from __future__ import annotations

from pathlib import Path

from extractx.core.objects import RenderedPrompt
from extractx.core.versions import stable_hash

__all__ = ["LocalPromptRecorder"]


class LocalPromptRecorder:
    """Content-addressed prompt recorder.

    The same rendered prompt writes to the same artifact path, so repeated
    calls dedupe naturally. The returned ref is the prompt hash.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def record(self, rendered: RenderedPrompt, *, seam: str) -> str:
        payload = rendered.model_dump(mode="json")
        prompt_hash = stable_hash(payload)
        target_dir = self.root / _safe_segment(seam)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{prompt_hash}.json"
        if not target.exists():
            target.write_text(
                rendered.model_dump_json(indent=2),
                encoding="utf-8",
            )
        return prompt_hash


def _safe_segment(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return safe or "prompt"
