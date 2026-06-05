"""narrow in-process registry mapping `ExtractionSpec.version` to a live
pydantic schema class.

phase-1 scope (M8 vertical slice):

- the registry is **execution / schema-internal plumbing**, not a second
  canonical truth surface.
- it exists solely so that `SerialExecutor` can resolve a live
  `type[BaseModel]` from `spec.version` and pass it into seam F's
  `LayeredProposalValidator.validate(..., schema_cls=...)` without
  widening the public `run_extraction(...)` signature with a
  `schema_cls` parameter and without resolving the class from
  `ExtractionSpec.source_schema_ref` (which is a stable reference
  string, not a live class import).
- `ExtractionSpec.from_pydantic(...)` registers the live class once,
  keyed by the resulting `spec.version`. manual specs do not register
  anything; their seam-F path stays on `schema_cls=None`.
- nothing here imports filesystem state, environment variables, or
  reflection-by-string. registration is purely a local function call
  inside `from_pydantic.py`.

failure modes (raised loudly so seam ownership stays clear):

- `register_schema_cls` is idempotent over `(spec_version, cls)` —
  calling it twice with the same key and same class is a no-op. calling
  it with the same key but a different class raises `RuntimeError`
  (which the caller in `from_pydantic` surfaces; `from_pydantic`
  itself is pure modulo this side effect, so the only callers expected
  to hit this path are tests that intentionally smuggle two classes
  through the same `version`).
- `lookup_schema_cls` returns `None` when the key is not registered —
  the executor decides whether the absence is a real `InfrastructureError`
  (pydantic-backed spec, no live class) or an expected manual-path miss.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "lookup_class_by_qualname",
    "lookup_schema_cls",
    "register_class_by_qualname",
    "register_schema_cls",
]


# module-private mutable mapping. holds live `type[BaseModel]` references
# keyed by `ExtractionSpec.version`. keys are stable across runs for the
# same schema class because `version` is itself a content hash. values
# are kept as `Any` so this module does not import pydantic (avoiding a
# core / schema cycle) — `BaseModel` instances are checked at the
# registration callsite in `from_pydantic.py`.
_SCHEMA_CLS_BY_SPEC_VERSION: dict[str, Any] = {}


def register_schema_cls(spec_version: str, schema_cls: Any) -> None:
    """register a live `schema_cls` under `spec_version`.

    idempotent for repeated registration of the same `(spec_version,
    schema_cls)` pair. raises `RuntimeError` if `spec_version` is
    already registered to a *different* class — silent overwrite would
    let two unrelated classes share a hash key without anyone noticing.
    """

    existing = _SCHEMA_CLS_BY_SPEC_VERSION.get(spec_version)
    if existing is None:
        _SCHEMA_CLS_BY_SPEC_VERSION[spec_version] = schema_cls
        return
    if existing is schema_cls:
        return
    raise RuntimeError(
        "extractx.schema._schema_cls_registry: spec_version "
        f"{spec_version!r} is already registered to a different schema "
        f"class ({existing!r}); refusing to overwrite",
    )


def lookup_schema_cls(spec_version: str) -> Any | None:
    """return the live `schema_cls` registered under `spec_version`, or
    `None` if the key is not registered.

    callers (the executor) decide whether `None` is an honest miss
    (manual spec) or an `InfrastructureError` (pydantic-backed spec
    with no live class registered in this process).
    """

    return _SCHEMA_CLS_BY_SPEC_VERSION.get(spec_version)


# ---------------------------------------------------------------------------
# M9 phase-2: class-by-qualname registry (sibling map)
# ---------------------------------------------------------------------------
#
# the qualname registry is the second in-process map populated by
# `from_pydantic(...)`. it widens registry coverage so a future
# manual-spec replay thread can resolve binding `cls` references
# (`StrategyBinding.cls`, `SorterBinding.cls`) by their stable
# `module.qualname` surrogate without a second migration. it stays
# in-process only — no filesystem walk, no `importlib`, no
# module-discovery shortcut.
_CLASS_BY_QUALNAME: dict[str, Any] = {}


def _qualname_for(cls: Any) -> str:
    """compose `f"{cls.__module__}.{cls.__qualname__}"` for `cls`.

    raises `RuntimeError` on objects missing the attributes — the
    public surface only registers concrete classes from
    `from_pydantic`, so this is a defense-in-depth surface.
    """

    module = getattr(cls, "__module__", None)
    qualname = getattr(cls, "__qualname__", None)
    if module is None or qualname is None:
        raise RuntimeError(
            "extractx.schema._schema_cls_registry: cannot derive "
            f"qualname for {cls!r}: missing __module__ / __qualname__",
        )
    return f"{module}.{qualname}"


def register_class_by_qualname(cls: Any) -> None:
    """register a live `cls` keyed by its `module.qualname` qualname.

    idempotent for repeated registration of the same `(qualname, cls)`
    pair. raises `RuntimeError` if the qualname is already registered
    to a *different* class — silent overwrite would let two unrelated
    classes share a key without anyone noticing (mirrors
    `register_schema_cls` semantics).
    """

    qualname = _qualname_for(cls)
    existing = _CLASS_BY_QUALNAME.get(qualname)
    if existing is None:
        _CLASS_BY_QUALNAME[qualname] = cls
        return
    if existing is cls:
        return
    raise RuntimeError(
        "extractx.schema._schema_cls_registry: qualname "
        f"{qualname!r} is already registered to a different class "
        f"({existing!r}); refusing to overwrite",
    )


def lookup_class_by_qualname(qualname: str) -> Any | None:
    """return the live class registered under `qualname`, or `None`."""

    return _CLASS_BY_QUALNAME.get(qualname)
