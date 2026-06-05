"""contract test for the `InstanceResolver` protocol surface.

proof targets (from
docs/tasks/seam-g-resolver-phase-1-deterministic-instance-resolution.md,
"Focused proof"):

- `InstanceResolver.resolve(...) -> tuple[Instance, ...]` exists
  on the protocol surface (explicit callable method, not an implicit
  duck-typed shape).
- the phase-1 `DeterministicInstanceResolver` satisfies the protocol
  structurally.
- the phase-1 `DeterministicInstanceResolver` emits `producer_version`
  in the documented `code:{code_hash}` shape using the core helper,
  matching the seam-C / seam-D / seam-F / seam-G.planner algorithmic
  composition pattern.

behavioral proof (authority order, ambiguity negatives, cardinality
negatives, promotion, purity) lives in
`tests/instances/test_deterministic_resolver.py`. this file guards
only the shape of the seam.
"""

from __future__ import annotations

import inspect

from extractx.core.contracts import InstanceResolver
from extractx.core.versions import algorithmic_producer_version, stable_hash
from extractx.instances import DeterministicInstanceResolver
from extractx.instances.resolvers import algorithmic_code_hash


class TestInstanceResolverProtocolSurface:
    def test_resolve_is_a_declared_protocol_member(self) -> None:
        # an explicit method on the protocol — if this reference
        # disappears, seam G.resolver has lost its callable boundary.
        assert hasattr(InstanceResolver, "resolve")

    def test_resolve_signature_matches_the_seam_g_resolver_contract(self) -> None:
        # architecture §7 seam G.resolver + task brief:
        # `resolve(validated_fields, candidate_sets, spec,
        #  instance_plan=None) -> tuple[Instance, ...]`.
        # parameter names and the optional default on `instance_plan`
        # are part of the protocol — a drift from keyword-capable to
        # positional-only or a renaming is caught here rather than at
        # every resolver implementation.
        sig = inspect.signature(InstanceResolver.resolve)
        param_names = list(sig.parameters.keys())
        assert param_names == [
            "self",
            "validated_fields",
            "candidate_sets",
            "spec",
            "instance_plan",
        ]
        assert sig.parameters["instance_plan"].default is None

    def test_deterministic_resolver_satisfies_protocol_structurally(self) -> None:
        resolver: InstanceResolver = DeterministicInstanceResolver()
        # structural subtype check — the assignment above is the proof.
        # we also assert it has the method explicitly for legibility.
        assert hasattr(resolver, "resolve")


class TestAlgorithmicProducerVersionShape:
    def test_module_helper_matches_core_helper_output(self) -> None:
        # keep the helper's output tied to the documented composition
        # rule (architecture §4 / §8: `code:{code_hash}`) and to the
        # exact class-qualname seed that seam-C / seam-D / seam-F /
        # seam-G.planner use. any drift here means the algorithmic
        # producer-version sites have diverged.
        expected_digest = stable_hash(
            f"{DeterministicInstanceResolver.__module__}."
            f"{DeterministicInstanceResolver.__qualname__}",
        )
        assert algorithmic_code_hash() == algorithmic_producer_version(expected_digest)

    def test_module_helper_is_code_prefixed(self) -> None:
        # the `code:` prefix is the public shape for algorithmic
        # producers per architecture §4 / §8. guard it explicitly so a
        # local rename does not silently break replay / diagnostic
        # consumers that parse the prefix.
        assert algorithmic_code_hash().startswith("code:")

    def test_resolver_producer_version_matches_helper(self) -> None:
        resolver = DeterministicInstanceResolver()
        assert resolver.producer_version == algorithmic_code_hash()
