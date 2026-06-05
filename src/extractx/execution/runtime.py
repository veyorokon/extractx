"""`Runtime` capability container per docs/architecture.md §11 and §7 seam J.

phase-1 (M8 vertical slice) lands a real, constructible `Runtime` that
binds the documented capability protocols (`LLM`, `NLP`, `Fetch`,
`Budget`, `Reporter`). the algorithmic vertical slice landed in this
slice consumes none of the soft-compute capabilities, so:

- `llm` / `nlp` / `fetch` default to `None`. the executor does **not**
  raise `CapabilityError` for the supported regex-bound path even when
  these are unbound — no step in the slice declares them. (a later
  thread that lands soft-compute paths is expected to enforce the
  step-declares-capabilities rule per architecture §7 seam J.)
- `budget` defaults to a fresh `TokenCountBudget()` (the documented
  default per ADR-0001 / architecture §7 seam J). it is constructible
  without provider keys, has zero counters, and obeys the `Budget`
  protocol.
- `reporter` defaults to a `NullReporter()`. phase-1 strategy and
  executor do not thread events through it.

`Runtime.from_env()` is a thin assembler kept honest for the supported
phase-1 path: it constructs the same defaults as `Runtime()` and does
**not** read `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` because the
algorithmic slice does not consume them. the soft-compute env-reading
behavior the architecture describes lands with the soft-compute
capability thread; baking it in now would invent a code path the
landed seams never exercise (anti-pattern §15 "Duplicate Overlapping
Path").

drift acknowledgement: per the M8 brief's runtime-capability drift,
missing soft-compute capabilities are not a setup failure for this
slice. the executor surfaces those if a future supported path begins
to consume them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from extractx.selection.examples import SelectorPromptPolicy

from .budget import TokenCountBudget
from .reporter import NullReporter

__all__ = ["Runtime"]


@dataclass(frozen=True, slots=True)
class Runtime:
    """capability container bound at the call site.

    `Runtime` is the only place provider choice lives; every step
    declares its capability needs as typed protocol parameters and the
    executor injects from this container. phase-1 supports:

    - algorithmic strategies (regex candidate generation + singleton
      selector) that declare no capabilities — `llm`/`nlp`/`fetch`
      stay `None`.
    - the documented `Budget` and `Reporter` defaults so a successful
      run carries honest empty diagnostics rather than `None`-typed
      placeholders.

    `from_env()` is provided as a convenience constructor; phase-1
    treats it as equivalent to bare construction since the algorithmic
    vertical slice consumes no env-bound capabilities.
    """

    # `Any` typing on the soft-compute capabilities keeps `Runtime`
    # decoupled from the protocol shape today. the protocols are
    # imported from `extractx.core.contracts` by the steps that
    # actually declare them, not by `Runtime` itself.
    llm: Any | None = None
    nlp: Any | None = None
    fetch: Any | None = None
    prompt_recorder: Any | None = None
    deferred_provider: Any | None = None
    selector_prompt_assets: Any | None = None
    selector_prompt_policies: Mapping[str, SelectorPromptPolicy] = field(
        default_factory=lambda: {},
    )

    # the typed budget surface is the `Budget` protocol from
    # `extractx.core.contracts`. structural protocol matching means we
    # do not need to import the protocol class to satisfy it; storing
    # `Any` keeps user-supplied subclasses (e.g., a pricing-aware
    # subclass per ADR-0001) compatible without bloating the runtime
    # import surface. the default factory hands out a fresh
    # `TokenCountBudget()` so per-`Runtime` budget state is isolated.
    budget: Any = field(default_factory=TokenCountBudget)
    reporter: Any = field(default_factory=NullReporter)

    @classmethod
    def from_env(cls) -> Runtime:
        """construct a phase-1 `Runtime` from environment variables.

        algorithmic slice: this path does **not** read provider keys
        because no step in the slice consumes them. it is the same as
        bare `Runtime()` today and is preserved as a typed surface so
        the soft-compute thread can extend it without rebinding the
        public api.
        """

        return cls()
