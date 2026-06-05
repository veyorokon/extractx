"""contract test for the `InstancePlanner` protocol surface.

proof targets (from
docs/tasks/seam-g-planner-phase-1-structural-instance-planner.md,
"Focused proof"):

- `InstancePlanner.plan(...) -> InstancePlan | NegativeOutcome` exists
  on the protocol surface (explicit callable method, not an implicit
  duck-typed shape).
- the phase-1 `StructuralInstancePlanner` satisfies the protocol
  structurally.
- the phase-1 `StructuralInstancePlanner` emits `producer_version` in
  the documented `code:{code_hash}` shape using the core helper,
  matching the seam-C / seam-D / seam-F algorithmic composition
  pattern.

behavioral proof (advisory anchors, dedup, structural fallback,
max_instances, text_anchor_space consistency, determinism) lives in
`tests/instances/test_structural_planner.py`. this file guards only
the shape of the seam.
"""

from __future__ import annotations

import inspect

from extractx.core.contracts import InstancePlanner
from extractx.core.versions import algorithmic_producer_version, stable_hash
from extractx.instances import StructuralInstancePlanner, algorithmic_code_hash


class TestInstancePlannerProtocolSurface:
    def test_plan_is_a_declared_protocol_member(self) -> None:
        # an explicit method on the protocol — if this reference
        # disappears, seam G.planner has lost its callable boundary.
        assert hasattr(InstancePlanner, "plan")

    def test_plan_signature_matches_the_seam_g_planner_contract(self) -> None:
        # architecture §7 seam G.planner + §11 iterative pseudocode:
        # `plan(document_view, spec, boundary_anchor_spans=()) ->
        #  InstancePlan | NegativeOutcome`.
        # parameter names and default are part of the protocol — a
        # drift from keyword-capable to positional-only (or a
        # renaming) is caught here rather than at every planner
        # implementation.
        sig = inspect.signature(InstancePlanner.plan)
        param_names = list(sig.parameters.keys())
        assert param_names == [
            "self",
            "document_view",
            "spec",
            "boundary_anchor_spans",
        ]
        assert sig.parameters["boundary_anchor_spans"].default == ()

    def test_structural_planner_satisfies_protocol_structurally(self) -> None:
        planner: InstancePlanner = StructuralInstancePlanner()
        # structural subtype check — the assignment above is the proof.
        # we also assert it has the method explicitly for legibility.
        assert hasattr(planner, "plan")


class TestAlgorithmicProducerVersionShape:
    def test_module_helper_matches_core_helper_output(self) -> None:
        # keep the helper's output tied to the documented composition
        # rule (architecture §4 / §8: `code:{code_hash}`) and to the
        # exact class-qualname seed that seam-C / seam-D / seam-F use.
        # any drift here means the algorithmic producer-version sites
        # have diverged.
        expected_digest = stable_hash(
            f"{StructuralInstancePlanner.__module__}.{StructuralInstancePlanner.__qualname__}",
        )
        assert algorithmic_code_hash() == algorithmic_producer_version(expected_digest)

    def test_module_helper_is_code_prefixed(self) -> None:
        # the `code:` prefix is the public shape for algorithmic
        # producers per architecture §4 / §8. guard it explicitly so a
        # local rename does not silently break replay / diagnostic
        # consumers that parse the prefix.
        assert algorithmic_code_hash().startswith("code:")

    def test_planner_producer_version_matches_helper(self) -> None:
        planner = StructuralInstancePlanner()
        assert planner.producer_version == algorithmic_code_hash()
