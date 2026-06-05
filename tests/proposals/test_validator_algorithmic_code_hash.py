"""focused proof for `extractx.proposals.validation.algorithmic_code_hash`.

covers replay drift-gate phase 1 proof targets:

1. helper exists and is module-level (not a class property / classmethod)
2. helper returns a stable string (idempotent across calls)
3. prefix matches the seam-D / seam-G.resolver pattern (`code:`)
4. helper composition mirrors the reference seams' qualname-based shape
   (a subclass with a different qualname produces a different hash via
   the same composition pattern)
5. white-box: `LayeredProposalValidator` does NOT gain a class-level
   `producer_version` attribute, property, or class method — only the
   module-level `algorithmic_code_hash()` helper is added in this thread
"""

from __future__ import annotations

import inspect

from extractx.core.versions import algorithmic_producer_version, stable_hash
from extractx.proposals import validation as validation_module
from extractx.proposals.validation import (
    LayeredProposalValidator,
    algorithmic_code_hash,
)

# --------------------------------------------------------------------------
# proof target 1 — module-level callable, exported from `__all__`
# --------------------------------------------------------------------------


def test_algorithmic_code_hash_is_module_level() -> None:
    """the helper lives at module scope on `extractx.proposals.validation`
    and is exported from `__all__`. it is NOT a method on the
    `LayeredProposalValidator` class."""

    # module-level surface
    assert hasattr(validation_module, "algorithmic_code_hash")
    assert validation_module.algorithmic_code_hash is algorithmic_code_hash
    assert "algorithmic_code_hash" in validation_module.__all__

    # callable + zero-arg signature mirroring the reference seams
    assert callable(algorithmic_code_hash)
    sig = inspect.signature(algorithmic_code_hash)
    assert len(sig.parameters) == 0


# --------------------------------------------------------------------------
# proof target 2 — stable string across calls
# --------------------------------------------------------------------------


def test_algorithmic_code_hash_is_stable() -> None:
    """two calls return byte-identical strings (no per-call material)."""

    first = algorithmic_code_hash()
    second = algorithmic_code_hash()
    assert isinstance(first, str)
    assert first == second


# --------------------------------------------------------------------------
# proof target 3 — prefix matches the seam-D / seam-G.resolver pattern
# --------------------------------------------------------------------------


def test_algorithmic_code_hash_prefix_is_code() -> None:
    """the helper composes through `algorithmic_producer_version(...)`
    so the wire shape is `code:{sha256_hex_digest}` (architecture §4)."""

    value = algorithmic_code_hash()
    assert value.startswith("code:"), value
    # `algorithmic_producer_version` wraps a sha256 hex digest from
    # `stable_hash`. confirm the suffix is a 64-char hex string.
    suffix = value[len("code:"):]
    assert len(suffix) == 64
    assert all(c in "0123456789abcdef" for c in suffix)


# --------------------------------------------------------------------------
# proof target 4 — composition mirrors the qualname-based reference shape
# --------------------------------------------------------------------------


def test_algorithmic_code_hash_matches_reference_composition() -> None:
    """the helper composes identically to seams C / D / G.resolver:
    `algorithmic_producer_version(stable_hash("{module}.{qualname}"))`."""

    expected = algorithmic_producer_version(
        stable_hash(
            f"{LayeredProposalValidator.__module__}."
            f"{LayeredProposalValidator.__qualname__}",
        ),
    )
    assert algorithmic_code_hash() == expected


def test_subclass_qualname_would_produce_different_hash() -> None:
    """white-box: a subclass with a distinct qualname would produce a
    different `algorithmic_code_hash` under the same composition shape.

    we don't subclass `LayeredProposalValidator` and re-bind the helper
    (the helper is closed over the base class's qualname by design); we
    confirm the composition itself is qualname-sensitive by hashing a
    distinct qualname through the same shape and observing divergence.
    """

    class _SubclassedValidator(LayeredProposalValidator):
        pass

    base_hash = algorithmic_producer_version(
        stable_hash(
            f"{LayeredProposalValidator.__module__}."
            f"{LayeredProposalValidator.__qualname__}",
        ),
    )
    sub_hash = algorithmic_producer_version(
        stable_hash(
            f"{_SubclassedValidator.__module__}."
            f"{_SubclassedValidator.__qualname__}",
        ),
    )
    assert base_hash != sub_hash
    # and the helper's value follows the base-class composition (it is
    # bound to `LayeredProposalValidator`, not `cls`)
    assert algorithmic_code_hash() == base_hash


# --------------------------------------------------------------------------
# proof target 5 — no class-level `producer_version` property added
# --------------------------------------------------------------------------


def test_layered_proposal_validator_has_no_producer_version_attribute() -> None:
    """white-box pin: this thread adds the module-level helper only. it
    does NOT add a `producer_version` attribute, property, classmethod,
    or class method on `LayeredProposalValidator`. (seam G.resolver
    currently has both shapes — that pre-existing inconsistency is
    parked for a coordinator-owned harmonization thread, not unwound
    here.)
    """

    # nothing named `producer_version` on the class itself or its
    # MRO chain (other than `object`'s standard methods).
    for klass in LayeredProposalValidator.__mro__:
        if klass is object:
            break
        assert "producer_version" not in vars(klass), (
            f"{klass.__name__} unexpectedly defines `producer_version` — "
            "the replay drift-gate phase-1 thread pinned module-level "
            "helper only."
        )

    # `inspect.getmembers` covers descriptors / properties / classmethods.
    member_names = {name for name, _ in inspect.getmembers(LayeredProposalValidator)}
    assert "producer_version" not in member_names, (
        "LayeredProposalValidator unexpectedly exposes a "
        "`producer_version` member — the replay drift-gate phase-1 "
        "thread pinned module-level helper only."
    )

    # instances also do not carry it
    instance = LayeredProposalValidator()
    assert not hasattr(instance, "producer_version")
