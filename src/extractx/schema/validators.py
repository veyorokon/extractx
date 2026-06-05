"""enforces the "pydantic-as-extractor" prohibition per docs/architecture.md §15.

pydantic `field_validator` runs at seam F layer 2 on the *normalized
value* the normalizer produced — never on raw text, candidate summaries,
or selector outputs. users who attempt to pull values from raw text in a
`field_validator` are smuggling extraction into a validator; the anti-
pattern is called out in §15 and blocked at spec load with `SpecError`.

detection here is **narrow on purpose**. pydantic v2's `field_validator`
shape is not uniformly introspectable (decorator metadata varies across
mode=`before` / `after` / `plain` / `wrap`), and the validator source is
often a closure. silently under-detecting is safer than silently mis-
firing on legitimate validators. we surface a pushback note in the
worker report describing exactly what we do and do not detect.

the current detectors:

1. `mode="before"` validators that accept a `str` annotation — the `str`
   annotation on a `before`-mode validator strongly suggests "parse this
   raw string", which is the seam-C / seam-D contract not the seam-F
   contract. this is the common failure pattern.
2. explicit opt-in marker: a validator decorated with
   `@pydantic_as_extractor_disallowed` is rejected unconditionally. this
   lets users / lint rules mark a known-bad validator without rewriting
   the detector.

users with borderline validators (`mode="plain"` that only strip
whitespace on an already-coerced value, etc.) pass inspection. this is
the narrow-honest failure mode, not over-reach.
"""

from __future__ import annotations

import inspect
from typing import Any

from pydantic import BaseModel

from ..core.exceptions import SpecError

__all__ = [
    "detect_pydantic_as_extractor",
    "pydantic_as_extractor_disallowed",
]


_DISALLOWED_MARKER_ATTR = "__extractx_pydantic_as_extractor_disallowed__"


def pydantic_as_extractor_disallowed(func: Any) -> Any:
    """opt-in marker for the pydantic-as-extractor detector.

    when a validator function is decorated with this, `from_pydantic`
    rejects the enclosing class at spec load. downstream lint tools or
    users who already know a given validator crosses the seam use this
    rather than waiting for the heuristic to catch them.
    """

    setattr(func, _DISALLOWED_MARKER_ATTR, True)
    return func


def detect_pydantic_as_extractor(cls: type[BaseModel]) -> None:
    """raise `SpecError` on `cls` when any validator parses raw text.

    walks `cls.__pydantic_decorators__.field_validators` if available
    (pydantic v2 surface), inspects decorator metadata, and raises on
    the documented bad patterns. pydantic versions that do not expose
    the decorator registry cause this function to be a no-op — narrow
    honest detection is the goal.
    """

    decorators = getattr(cls, "__pydantic_decorators__", None)
    if decorators is None:
        return

    field_validators = getattr(decorators, "field_validators", {})
    for name, decorator in field_validators.items():
        func = getattr(decorator, "func", None)
        info = getattr(decorator, "info", None)

        # 1. unconditional opt-in marker.
        if func is not None and getattr(func, _DISALLOWED_MARKER_ATTR, False):
            raise SpecError(
                f"{cls.__name__}.{name}: validator is marked "
                f"@pydantic_as_extractor_disallowed; validators must not parse raw "
                f"text (see docs/architecture.md §15 'Pydantic-as-Extractor').",
            )

        # 2. mode="before" validator whose signature accepts `str` is
        # almost certainly parsing raw text. mode="before" runs prior to
        # pydantic coercion, so a `str` argument *is* the raw value.
        mode = getattr(info, "mode", None)
        if mode == "before" and func is not None and _accepts_raw_str(func):
            raise SpecError(
                f"{cls.__name__}.{name}: mode='before' validator accepting `str` "
                f"parses raw text; pydantic validators run at seam F layer 2 on "
                f"normalized values only (see docs/architecture.md §15 "
                f"'Pydantic-as-Extractor').",
            )


def _accepts_raw_str(func: Any) -> bool:
    """return True when `func`'s first non-cls/self parameter annotation is `str`.

    conservative: if the signature cannot be inspected, we do not flag.
    matches both the runtime `str` class and the PEP 563 stringified
    form `"str"` (modules using `from __future__ import annotations` keep
    annotations as strings until explicitly resolved).
    """

    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return False

    params = [
        p
        for p in sig.parameters.values()
        if p.name not in ("self", "cls")
        and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if not params:
        return False
    first = params[0]
    # pydantic validators typically type the input as `str` when they
    # intend raw text. we do not unwrap unions / Optional — the narrow
    # detector deliberately under-reaches.
    if first.annotation is str:
        return True
    return isinstance(first.annotation, str) and first.annotation.strip() == "str"
