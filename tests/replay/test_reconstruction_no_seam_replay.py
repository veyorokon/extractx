"""white-box proof that phase-1 reconstruction does not re-execute seams.

per M9 phase-1 brief §9: "reconstruction does not re-execute seams".
monkey-patches the four algorithmic seam classes' canonical entry
points and asserts zero invocations during `read_replay` +
`reconstruct_extraction_result`.
"""

from __future__ import annotations

import pytest

from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.execution.executor.serial import SerialExecutor
from extractx.instances.resolvers.deterministic import DeterministicInstanceResolver
from extractx.proposals.validation import LayeredProposalValidator
from extractx.replay import read_replay, reconstruct_extraction_result
from extractx.selection.algorithmic.singleton import SingletonSelector


@pytest.mark.asyncio
async def test_reconstruction_invokes_no_seam_classes(
    executor_with_storage: SerialExecutor,
    pydantic_spec,
    runtime,
    policy,
    doc_complete: str,
    store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """count invocations of the four seam classes during the reader path.

    the executor (write path) DOES call them. we do the run first,
    then install counters, then drive the read + reconstruct path,
    and assert all four counters stay at zero.
    """

    # write path — pre-counter setup so we can isolate the read path.
    result = await executor_with_storage.execute(
        document=doc_complete, spec=pydantic_spec, runtime=runtime, policy=policy,
    )

    counters = {"candidate": 0, "selector": 0, "validator": 0, "resolver": 0}

    original_generate = RegexCandidateStrategy.generate
    original_select = SingletonSelector.select
    original_validate_field = LayeredProposalValidator.validate
    original_validate_instance = LayeredProposalValidator.validate_instance
    original_resolve = DeterministicInstanceResolver.resolve

    def _wrap_generate(self, *args, **kwargs):
        counters["candidate"] += 1
        return original_generate(self, *args, **kwargs)

    def _wrap_select(self, *args, **kwargs):
        counters["selector"] += 1
        return original_select(self, *args, **kwargs)

    def _wrap_validate(self, *args, **kwargs):
        counters["validator"] += 1
        return original_validate_field(self, *args, **kwargs)

    def _wrap_validate_instance(self, *args, **kwargs):
        counters["validator"] += 1
        return original_validate_instance(self, *args, **kwargs)

    def _wrap_resolve(self, *args, **kwargs):
        counters["resolver"] += 1
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(RegexCandidateStrategy, "generate", _wrap_generate)
    monkeypatch.setattr(SingletonSelector, "select", _wrap_select)
    monkeypatch.setattr(LayeredProposalValidator, "validate", _wrap_validate)
    monkeypatch.setattr(
        LayeredProposalValidator, "validate_instance", _wrap_validate_instance,
    )
    monkeypatch.setattr(DeterministicInstanceResolver, "resolve", _wrap_resolve)

    # read path — must not touch any seam.
    artifact = read_replay(store, result.replay_artifact_ref)
    rebuilt = reconstruct_extraction_result(
        artifact, artifact_id=result.replay_artifact_ref,
    )
    assert rebuilt == result

    assert counters == {
        "candidate": 0,
        "selector": 0,
        "validator": 0,
        "resolver": 0,
    }, counters
