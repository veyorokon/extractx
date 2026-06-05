"""contract test for the `Selector` protocol surface.

proof targets (from docs/tasks/seam-d-algorithmic-selector-phase-1.md,
"Focused proof"):

- `Selector.select(...) -> Observation` exists on the protocol surface
  (explicit callable method, not an implicit duck-typed shape).
- the phase-1 `SingletonSelector` satisfies the protocol structurally.
- the phase-1 `SingletonSelector` emits `producer_version` in the
  documented `code:{code_hash}` shape using the core helper, matching
  the seam-C regex strategy's composition pattern.

behavioral proof (empty / singleton / ambiguous cases, purity, id-only
enforcement) lives under `tests/selection/`. this file guards only the
shape of the seam.
"""

from __future__ import annotations

import inspect

from extractx.core.contracts import Selector
from extractx.core.versions import algorithmic_producer_version, stable_hash
from extractx.selection import SingletonSelector, algorithmic_code_hash


class TestSelectorProtocolSurface:
    def test_select_is_a_declared_protocol_member(self) -> None:
        # an explicit method on the protocol — if this reference
        # disappears, seam D has lost its callable boundary.
        assert hasattr(Selector, "select")

    def test_select_signature_matches_the_seam_d_contract(self) -> None:
        # architecture §7 seam D + §9 `Observation`:
        # `select(field_spec, candidate_set, context_pack,
        #         instance_state=None, *, instance_ids=("inst_0",)) -> Observation`.
        # parameter names and default are part of the protocol — a drift
        # from keyword-capable to positional-only (or a renaming) is
        # caught here rather than at every selector implementation.
        sig = inspect.signature(Selector.select)
        param_names = list(sig.parameters.keys())
        assert param_names == [
            "self",
            "field_spec",
            "candidate_set",
            "context_pack",
            "instance_state",
            "instance_ids",
        ]
        assert sig.parameters["instance_state"].default is None
        assert sig.parameters["instance_ids"].default == ("inst_0",)

    def test_singleton_selector_satisfies_protocol_structurally(self) -> None:
        selector: Selector = SingletonSelector()
        # structural subtype check — the assignment above is the proof.
        # we also assert it has the method explicitly for legibility.
        assert hasattr(selector, "select")


class TestAlgorithmicProducerVersionShape:
    def test_module_helper_matches_core_helper_output(self) -> None:
        # keep the helper's output tied to the documented composition
        # rule (architecture §4 / §8: `code:{code_hash}`) and to the
        # exact class-qualname seed that `RegexCandidateStrategy` uses.
        # any drift here means the two algorithmic producer-version
        # sites have diverged.
        expected_digest = stable_hash(
            f"{SingletonSelector.__module__}.{SingletonSelector.__qualname__}",
        )
        assert algorithmic_code_hash() == algorithmic_producer_version(expected_digest)

    def test_module_helper_is_code_prefixed(self) -> None:
        # the `code:` prefix is the public shape for algorithmic
        # producers per architecture §4 / §8. guard it explicitly so a
        # local rename does not silently break replay/diagnostic
        # consumers that parse the prefix.
        assert algorithmic_code_hash().startswith("code:")

    def test_selector_producer_version_matches_helper(self) -> None:
        selector = SingletonSelector()
        assert selector.producer_version == algorithmic_code_hash()
