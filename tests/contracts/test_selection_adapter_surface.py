"""contract test for the `SelectionAdapter` protocol surface.

proof targets (from docs/tasks/seam-e-cardinality-selection-adapter-phase-1.md,
"Focused proof"):

- `SelectionAdapter.adapt(...) -> tuple[ProposedField, ...] | NegativeOutcome`
  exists on the protocol surface (explicit callable method, not an implicit
  duck-typed shape).
- the phase-1 `CardinalitySelectionAdapter` satisfies the protocol
  structurally.

this file guards only the shape of the seam. behavioral proof (the
cardinality table, honest `ProposedField` projection, structural-violation
loudness) lives in `tests/proposals/`.
"""

from __future__ import annotations

import inspect

from extractx.core.contracts import SelectionAdapter
from extractx.proposals import CardinalitySelectionAdapter


class TestSelectionAdapterProtocolSurface:
    def test_adapt_is_a_declared_protocol_member(self) -> None:
        # an explicit method on the protocol — if this reference
        # disappears, seam E has lost its callable boundary.
        assert hasattr(SelectionAdapter, "adapt")

    def test_adapt_signature_matches_the_seam_e_contract(self) -> None:
        # ADR-0008 seam E: `adapt(observation, candidate_set,
        # field_spec) -> tuple[ProposedField, ...] | NegativeOutcome`.
        # parameter names are part of the protocol surface — a drift
        # from keyword-capable to positional-only (or a renaming) is
        # caught here rather than at every adapter implementation.
        sig = inspect.signature(SelectionAdapter.adapt)
        assert list(sig.parameters.keys()) == [
            "self",
            "observation",
            "candidate_set",
            "field_spec",
        ]

    def test_cardinality_adapter_satisfies_protocol_structurally(self) -> None:
        adapter: SelectionAdapter = CardinalitySelectionAdapter()
        # structural subtype check — the assignment above is the proof.
        # we also assert it has the method explicitly for legibility.
        assert hasattr(adapter, "adapt")
