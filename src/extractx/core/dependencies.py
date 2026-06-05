"""`FieldSpec.depends_on` dependency-graph validation.

see docs/architecture.md §7 seam B (spec-load validation: cyclic
`depends_on` raises `SpecError` at construction) and §9 (`FieldSpec`
shape).

two pure helpers, no env or filesystem access:

- `validate_dependency_graph(edges)` — accepts a mapping from `field_id`
  to the iterable of `field_id`s it depends on, plus the set of known
  field ids. raises `SpecError` on unknown references or cycles.
- `topological_order(edges)` — deterministic topological sort over a
  valid dependency graph. ordering is by `(in_degree_ascending,
  field_id_lex)` to keep results stable across runs; callers that want
  a different order (e.g. by `FieldSpec.priority`) compose this helper
  with their own tie-breaker.

both helpers work at the `field_id`-string layer so that `objects.py`
and `schema/*` can reuse them without a circular import.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .exceptions import SpecError

__all__ = [
    "DependencyEdges",
    "topological_order",
    "validate_dependency_graph",
]


type DependencyEdges = Mapping[str, Iterable[str]]


def validate_dependency_graph(edges: DependencyEdges) -> None:
    """validate that `edges` forms an acyclic graph over a closed set of ids.

    `edges[field_id]` is the iterable of field ids that `field_id` depends
    on. every referenced dependency must itself be a key of `edges` (i.e.
    the id set is closed). any violation raises `SpecError`.

    see docs/architecture.md §7 seam B.
    """

    node_set = set(edges.keys())
    # unknown-reference check comes first so cycle detection only runs on
    # a well-formed closed graph.
    for field_id, deps in edges.items():
        for dep in deps:
            if dep not in node_set:
                raise SpecError(
                    f"FieldSpec dependency graph: field {field_id!r} depends on "
                    f"unknown field {dep!r}",
                )

    # kahn's algorithm: if we cannot consume every node, there is a cycle.
    in_degree: dict[str, int] = {n: 0 for n in node_set}
    out_adj: dict[str, list[str]] = {n: [] for n in node_set}
    for field_id, deps in edges.items():
        for dep in deps:
            # edge: dep --> field_id (dep must be produced before field_id)
            out_adj[dep].append(field_id)
            in_degree[field_id] += 1

    queue = sorted(n for n, d in in_degree.items() if d == 0)
    consumed: list[str] = []
    while queue:
        node = queue.pop(0)
        consumed.append(node)
        next_level: list[str] = []
        for succ in out_adj[node]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                next_level.append(succ)
        queue.extend(sorted(next_level))

    if len(consumed) != len(node_set):
        remaining = sorted(node_set - set(consumed))
        raise SpecError(
            f"FieldSpec dependency graph has a cycle; fields still on the frontier: {remaining}",
        )


def topological_order(edges: DependencyEdges) -> tuple[str, ...]:
    """deterministic topological order over the field ids in `edges`.

    runs `validate_dependency_graph(edges)` first, so any cyclic or
    dangling reference raises `SpecError`. the tie-break is
    lexicographic on `field_id`; callers who need a priority-based or
    declaration-order break compose this with their own sort.
    """

    validate_dependency_graph(edges)
    node_set = set(edges.keys())
    in_degree: dict[str, int] = {n: 0 for n in node_set}
    out_adj: dict[str, list[str]] = {n: [] for n in node_set}
    for field_id, deps in edges.items():
        for dep in deps:
            out_adj[dep].append(field_id)
            in_degree[field_id] += 1

    queue = sorted(n for n, d in in_degree.items() if d == 0)
    ordered: list[str] = []
    while queue:
        node = queue.pop(0)
        ordered.append(node)
        next_level: list[str] = []
        for succ in out_adj[node]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                next_level.append(succ)
        queue.extend(sorted(next_level))
    return tuple(ordered)
