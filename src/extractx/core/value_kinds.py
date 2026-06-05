"""value-kind registry per docs/architecture.md §9 / §12 / §14.

`ValueKind` is a semantic tag attached to python types via
`Annotated[pytype, ValueKind.X]`. members must both be iterable like an
enum (`for k in ValueKind: ...`) and support registration of new kinds at
runtime (`ValueKind.register("NAME")`).

standard-library `Enum` seals its member set at class creation time, so
`Enum.register(...)` is not a supported semantic. we implement a minimal
registry-backed class that meets the architecture's requirements:

- built-in kinds are created as module-level instances.
- `ValueKind.register("NAME")` returns the existing kind on re-registration
  with the same name (idempotent), and creates a new one on first sight.
- instances compare by name, hash by name, and are immutable.
- iteration: `for k in ValueKind: ...` yields all registered members.

ADR discussion: a runtime registry is intentional — third-party domain
packages need to declare new `ValueKind`s outside the core package (see
docs/architecture.md §14). a sealed `Enum` would force a central list.
this is the narrowest runtime registry that satisfies the documented
behavior; it is not a generic plugin discovery mechanism.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar, Final

_REGISTRY: dict[str, ValueKind] = {}


class _ValueKindMeta(type):
    """metaclass that makes `ValueKind` itself iterable / contains-checkable."""

    def __iter__(cls) -> Iterator[ValueKind]:  # noqa: N804 — metaclass convention
        return iter(_REGISTRY.values())

    def __contains__(cls, item: object) -> bool:  # noqa: N804
        if isinstance(item, ValueKind):
            return item.name in _REGISTRY
        if isinstance(item, str):
            return item in _REGISTRY
        return False

    def __len__(cls) -> int:  # noqa: N804
        return len(_REGISTRY)


class ValueKind(metaclass=_ValueKindMeta):
    """semantic tag for a python type used in an `Annotated[pytype, tag]` brand.

    see docs/architecture.md §9 / §12 / §14.

    instances are immutable; equality and hashing are by name. registration
    is idempotent on the name — `ValueKind.register("FOO")` called twice
    returns the same instance and does not mutate existing members.
    """

    __slots__ = ("_name",)

    _name: str

    # built-in members, registered eagerly at module import below. declared
    # here so type checkers see `ValueKind.MONEY` etc. as known attributes;
    # the actual instances are installed via `type.__setattr__` in
    # `register(...)`. extending at runtime via `ValueKind.register("FOO")`
    # is intentional and will not be visible to static type checkers.
    MONEY: ClassVar[ValueKind]
    PERCENT: ClassVar[ValueKind]
    DATE: ClassVar[ValueKind]
    ORG: ClassVar[ValueKind]
    PERSON: ClassVar[ValueKind]
    GPE: ClassVar[ValueKind]
    CARDINAL: ClassVar[ValueKind]
    ORDINAL: ClassVar[ValueKind]
    BOOL: ClassVar[ValueKind]
    CATEGORY: ClassVar[ValueKind]

    def __init__(self, name: str) -> None:
        # direct construction is internal. user code goes through `register()`.
        object.__setattr__(self, "_name", name)

    @classmethod
    def register(cls, name: str) -> ValueKind:
        """register (or return the existing) `ValueKind` for `name`.

        idempotent: calling `register("FOO")` twice returns the same
        instance. thread-safety is not a guarantee of this helper.

        see docs/architecture.md §14 extensibility map (`custom ValueKind`).
        """

        existing = _REGISTRY.get(name)
        if existing is not None:
            return existing
        member = cls(name)
        _REGISTRY[name] = member
        # expose the member as a class attribute so `ValueKind.FOO` works
        # for uppercase names, matching the enum-like usage in the
        # architecture's examples (`ValueKind.MONEY`, etc.).
        type.__setattr__(cls, name, member)
        return member

    @property
    def name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"<ValueKind.{self._name}>"

    def __str__(self) -> str:
        return f"ValueKind.{self._name}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ValueKind):
            return self._name == other._name
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("ValueKind", self._name))

    def __setattr__(self, name: str, value: object) -> None:
        # instances are immutable after construction.
        raise AttributeError(f"ValueKind is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"ValueKind is immutable; cannot delete {name!r}")


# built-in kinds from docs/architecture.md §12 examples. registered eagerly so
# `ValueKind.MONEY` etc. are importable without explicit registration.
MONEY: Final[ValueKind] = ValueKind.register("MONEY")
PERCENT: Final[ValueKind] = ValueKind.register("PERCENT")
DATE: Final[ValueKind] = ValueKind.register("DATE")
ORG: Final[ValueKind] = ValueKind.register("ORG")
PERSON: Final[ValueKind] = ValueKind.register("PERSON")
GPE: Final[ValueKind] = ValueKind.register("GPE")
CARDINAL: Final[ValueKind] = ValueKind.register("CARDINAL")
ORDINAL: Final[ValueKind] = ValueKind.register("ORDINAL")
BOOL: Final[ValueKind] = ValueKind.register("BOOL")
CATEGORY: Final[ValueKind] = ValueKind.register("CATEGORY")


__all__ = ["ValueKind"]
