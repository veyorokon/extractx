"""instance subsystem per docs/architecture.md §7 seams G.planner and G.resolver.

phase-1 exposes:

- `StructuralInstancePlanner` (seam G.planner, deterministic)
- `DeterministicInstanceResolver` (seam G.resolver, deterministic)
- `algorithmic_code_hash` — the phase-1 planner's `code:{code_hash}`
  `producer_version` helper
- `order_boundary_defining_fields` / `collect_advisory_anchors` — pure
  pre-plan helpers for the boundary_defining C->D pre-pass
- `BoundaryHelperContractError` — local `ValueError` subtype raised
  when a pre-plan helper sees a structural seam violation
- `InstanceResolverContractError` — local `ValueError` subtype raised
  when a resolver input violates a structural invariant
- `spans_overlap` / `spans_share_frame` — pure precedence helpers
  (phase-1 source-anchor-continuity primitives)

the resolver's per-class `algorithmic_code_hash` helper is exposed via
`extractx.instances.resolvers.algorithmic_code_hash` to avoid the
name collision with the planner's same-shaped helper.

the graph / neural planner and resolver variants remain
later-thread work.
"""

from __future__ import annotations

from .boundary import (
    BoundaryHelperContractError,
    collect_advisory_anchors,
    order_boundary_defining_fields,
)
from .planners import StructuralInstancePlanner, algorithmic_code_hash
from .precedence import (
    spans_overlap,
    spans_share_frame,
)
from .resolvers import (
    DeterministicInstanceResolver,
    InstanceResolverContractError,
)

__all__ = [
    "BoundaryHelperContractError",
    "DeterministicInstanceResolver",
    "InstanceResolverContractError",
    "StructuralInstancePlanner",
    "algorithmic_code_hash",
    "collect_advisory_anchors",
    "order_boundary_defining_fields",
    "spans_overlap",
    "spans_share_frame",
]
