"""`InstanceResolver` implementations per docs/architecture.md §7 seam G.resolver.

phase-1 lands only the deterministic `DeterministicInstanceResolver`.
`GraphInstanceResolver` and `NeuralInstanceResolver` remain stubs —
they are later-thread work (graph partitioning, soft-compute resolver).
"""

from __future__ import annotations

from .deterministic import (
    DeterministicInstanceResolver,
    InstanceResolverContractError,
    algorithmic_code_hash,
)

__all__ = [
    "DeterministicInstanceResolver",
    "InstanceResolverContractError",
    "algorithmic_code_hash",
]
