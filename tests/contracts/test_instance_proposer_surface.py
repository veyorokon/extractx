"""contract tests for the `InstanceProposer` protocol surface."""

from __future__ import annotations

import inspect

from extractx.core.contracts import InstanceProposer


def test_instance_proposer_protocol_signature() -> None:
    sig = inspect.signature(InstanceProposer.propose)
    assert list(sig.parameters) == [
        "self",
        "document_view",
        "spec",
        "candidate_set",
    ]
    assert sig.return_annotation == "InstanceProposerResponse"
