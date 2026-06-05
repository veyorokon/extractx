"""phase-1 deterministic algorithmic selector.

see docs/architecture.md §7 seam D, §8 soft-compute discipline (phase 1
intentionally does not invoke soft compute), and §17 proof table.

phase-1 policy (fixed, no deviation):

- zero candidates    -> Observation(outcome="NO_CANDIDATES",
                                  selected_candidate_ids=())
- exactly one        -> Observation(outcome="SELECTED",
                                  selected_candidate_ids=(sole_id,))
- more than one      -> Observation(outcome="AMBIGUOUS",
                                  selected_candidate_ids=(all input ids,
                                                          in CandidateSet
                                                          order))

explicit non-goals for this phase-1 selector (all owned by later
threads):

- llm-backed selection, `extras/pydantic_ai/` integration
- prompt rendering / `Prompt` implementations
- interview capture or `.interview()` rehydration
- `UsageEvent` emission (algorithmic producers do not emit provider usage)
- seam E cardinality mapping (that is the `SelectionAdapter`'s job)
- algorithmic abstention heuristics
- conditioning on `ContextPack.candidate_overflow` (the signal may be
  present; phase-1 intentionally does not act on it)

`InstanceState` is accepted on the call surface to match the seam-D
protocol but does not influence this first selector's deterministic
output — same `(field_spec, candidate_set, context_pack, instance_state)`
produces a byte-identical `Observation` across repeated calls.

`producer_version` composition follows the existing regex-strategy
pattern (see `extractx.candidates.generators.regex.algorithmic_code_hash`):
`stable_hash("{cls.__module__}.{cls.__qualname__}")` fed to
`algorithmic_producer_version(...)`, producing a `code:{code_hash}`
string. no model id, no prompt-template hash, no timestamp.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from extractx.core.objects import Observation
from extractx.core.versions import algorithmic_producer_version, stable_hash

from ..selector import enforce_observation_contract

if TYPE_CHECKING:
    from extractx.core import CandidateSet, ContextPack, FieldSpec, InstanceState

__all__ = [
    "AMBIGUOUS_REASON_LABEL",
    "SingletonSelector",
    "algorithmic_code_hash",
]


AMBIGUOUS_REASON_LABEL = "algorithmic_multi_candidate"
"""static label attached to `Observation.reason` when the selector emits
`AMBIGUOUS`.

phase-1 policy (see the task brief): `reason` is `None` for `SELECTED`
and `NO_CANDIDATES`; for `AMBIGUOUS` we attach this fixed static label so
downstream diagnostics can tell phase-1 ambiguity apart from an llm
selector's ambiguity signal without parsing prose. the label does not
carry any prose derived from candidate content.
"""


class SingletonSelector:
    """deterministic algorithmic `Selector` per phase-1 policy.

    structural `Selector` subtype — no base class required. the class
    deliberately holds no configurable state: identity is carried by
    `producer_version`, which is composed from the class's qualname so
    any subclass with different behavior produces a different
    `producer_version` automatically.
    """

    def __init__(self) -> None:
        # cache the producer version string once at construction so
        # `select(...)` is pure and cheap. `algorithmic_code_hash()` is
        # itself deterministic, so callers can also rebuild the expected
        # value without having an instance on hand.
        self._producer_version = algorithmic_code_hash()

    @property
    def producer_version(self) -> str:
        """return the `code:{code_hash}` string stamped on every emitted
        `Observation`. exposed for tests and for diagnostics that want to
        correlate an `Observation` with its producer without round-tripping
        through the `Observation` object."""

        return self._producer_version

    def select(
        self,
        field_spec: FieldSpec,
        candidate_set: CandidateSet,
        context_pack: ContextPack,
        instance_state: InstanceState | None = None,
        *,
        instance_ids: tuple[str, ...] = ("inst_0",),
    ) -> Observation:
        """apply the fixed phase-1 policy and emit an `Observation`.

        `field_spec`, `context_pack`, and `instance_state` are accepted
        to match the seam-D protocol but do not influence the
        deterministic output of this first selector. see the module
        docstring for the non-goals that justify that.
        """

        # touch the surface parameters so the structural protocol match
        # is exercised at runtime and linters do not flag them as
        # unused. they are load-bearing for protocol conformance (every
        # `Selector` must accept them) even though this impl does not
        # condition on them.
        del field_spec, context_pack, instance_state
        instance_id = instance_ids[0] if instance_ids else None

        candidates = candidate_set.candidates
        n = len(candidates)
        if n == 0:
            raw = Observation(
                instance_id=instance_id,
                field_id=candidate_set.field_id,
                outcome="NO_CANDIDATES",
                selected_candidate_ids=(),
                reason=None,
                producer_version=self._producer_version,
            )
        elif n == 1:
            raw = Observation(
                instance_id=instance_id,
                field_id=candidate_set.field_id,
                evidence_id=candidates[0].candidate_id,
                outcome="SELECTED",
                selected_candidate_ids=(candidates[0].candidate_id,),
                reason=None,
                producer_version=self._producer_version,
            )
        else:
            # preserve `CandidateSet.candidates` order exactly; no
            # re-sorting, no tie-break logic, no "first-candidate-wins"
            # collapse into SELECTED.
            raw = Observation(
                instance_id=instance_id,
                field_id=candidate_set.field_id,
                outcome="AMBIGUOUS",
                selected_candidate_ids=tuple(c.candidate_id for c in candidates),
                reason=AMBIGUOUS_REASON_LABEL,
                producer_version=self._producer_version,
            )

        # route through the shared selector-boundary enforcement. this
        # is redundant for this impl (the policy above cannot fabricate
        # ids) but it is the path llm-backed selectors will share, and
        # keeping both selectors on the same enforcement seam is part of
        # the seam-D contract.
        return enforce_observation_contract(raw, candidate_set)


def algorithmic_code_hash() -> str:
    """return the phase-1 selector's `producer_version` string.

    mirrors the pattern in
    `extractx.candidates.generators.regex.algorithmic_code_hash` so the
    two algorithmic producer-version sites compose their code_hash the
    same way: `stable_hash("{cls.__module__}.{cls.__qualname__}")`
    wrapped through `algorithmic_producer_version`.

    exposed at module scope so future versioning threads can consume it
    without reaching inside the selector class, and so tests can assert
    the wire value without instantiating the selector.
    """

    digest = stable_hash(
        f"{SingletonSelector.__module__}.{SingletonSelector.__qualname__}",
    )
    return algorithmic_producer_version(digest)
