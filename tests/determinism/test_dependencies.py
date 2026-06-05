"""determinism tests for dependency-graph validation and topological sort.

proof targets:
- dependency cycle detection raises `SpecError` (docs/architecture.md §7
  seam B).
- `topological_order` is deterministic across runs.
"""

from __future__ import annotations

import pytest

from extractx.core import (
    SpecError,
    topological_order,
    validate_dependency_graph,
)


class TestCycleDetection:
    def test_self_cycle_raises(self) -> None:
        edges = {"a": ["a"]}
        with pytest.raises(SpecError, match="cycle"):
            validate_dependency_graph(edges)

    def test_two_node_cycle_raises(self) -> None:
        edges = {"a": ["b"], "b": ["a"]}
        with pytest.raises(SpecError, match="cycle"):
            validate_dependency_graph(edges)

    def test_three_node_cycle_raises(self) -> None:
        edges = {"a": ["b"], "b": ["c"], "c": ["a"]}
        with pytest.raises(SpecError, match="cycle"):
            validate_dependency_graph(edges)

    def test_valid_chain_passes(self) -> None:
        edges = {"a": [], "b": ["a"], "c": ["b"]}
        validate_dependency_graph(edges)

    def test_dangling_reference_raises(self) -> None:
        edges = {"a": ["missing"]}
        with pytest.raises(SpecError, match="unknown"):
            validate_dependency_graph(edges)


class TestTopologicalOrder:
    def test_chain_order(self) -> None:
        edges = {"a": [], "b": ["a"], "c": ["b"]}
        assert topological_order(edges) == ("a", "b", "c")

    def test_deterministic_tie_break_lex(self) -> None:
        # two independent roots; tie broken lexicographically.
        edges = {"b": [], "a": [], "c": ["a", "b"]}
        assert topological_order(edges) == ("a", "b", "c")

    def test_repeated_calls_identical(self) -> None:
        edges = {
            "d": ["b"],
            "b": ["a"],
            "a": [],
            "c": ["a"],
            "e": ["c", "d"],
        }
        assert topological_order(edges) == topological_order(edges)

    def test_rejects_cycle(self) -> None:
        with pytest.raises(SpecError):
            topological_order({"a": ["b"], "b": ["a"]})
