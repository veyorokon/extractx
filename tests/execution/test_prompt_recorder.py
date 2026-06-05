from __future__ import annotations

import json

from extractx.core.objects import Message, RenderedPrompt
from extractx.execution.prompt_recorder import LocalPromptRecorder


def _prompt() -> RenderedPrompt:
    return RenderedPrompt(
        messages=(
            Message(role="system", content="system"),
            Message(role="user", content="user"),
        ),
        metadata={"model_id": "fake:model"},
    )


def test_local_prompt_recorder_content_addresses_and_dedupes(tmp_path) -> None:
    recorder = LocalPromptRecorder(tmp_path)
    rendered = _prompt()

    first = recorder.record(rendered, seam="selector.batch")
    second = recorder.record(rendered, seam="selector.batch")

    assert first == second
    files = list((tmp_path / "selector.batch").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["messages"][0]["content"] == "system"
    assert payload["messages"][1]["content"] == "user"
