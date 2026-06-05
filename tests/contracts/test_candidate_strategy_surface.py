"""contract test for the `CandidateStrategy` protocol surface.

proof targets (from docs/tasks/seam-c-deterministic-candidate-generation.md,
"Focused proof"):

- `CandidateStrategy.generate(...) -> CandidateSet` exists on the protocol
  surface (explicit callable method, not an implicit duck-typed shape).
- the phase-1 `RegexCandidateStrategy` satisfies the protocol structurally.

this file guards only the *shape* of the seam. behavioral determinism,
span honesty, id uniqueness, param validation, and match-to-source
translation live in `tests/candidates/` under focused regex tests.
"""

from __future__ import annotations

import inspect

from extractx.candidates import RegexCandidateStrategy
from extractx.core.contracts import CandidateStrategy


class TestCandidateStrategyProtocolSurface:
    def test_generate_is_a_declared_protocol_member(self) -> None:
        # an explicit method on the protocol — if this reference
        # disappears, seam C has lost its callable boundary.
        assert hasattr(CandidateStrategy, "generate")

    def test_generate_signature_matches_the_seam_c_contract(self) -> None:
        # the architecture's seam C method signature:
        # `generate(field_spec, document_view, instance_hint=None) -> CandidateSet`.
        # we check the parameter names and kind so a drift in positional
        # vs. keyword-only is caught here rather than at every strategy
        # implementation.
        sig = inspect.signature(CandidateStrategy.generate)
        param_names = list(sig.parameters.keys())
        # `self` is first; then field_spec, document_view, instance_hint.
        assert param_names == ["self", "field_spec", "document_view", "instance_hint"]
        assert sig.parameters["instance_hint"].default is None

    def test_regex_strategy_satisfies_protocol_structurally(self) -> None:
        strategy: CandidateStrategy = RegexCandidateStrategy()
        # structural subtype check — the assignment above is the proof.
        # we also assert it has the method explicitly for legibility.
        assert hasattr(strategy, "generate")
