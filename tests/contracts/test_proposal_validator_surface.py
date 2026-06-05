"""contract test for the seam-F `ProposalValidator` protocol surface.

proof targets:

layers 1+2 (seam-F phase-1 candidate-and-field-validation brief):

- `ProposalValidator.validate(...) -> ValidatedField | NegativeOutcome |
  ValidationFailure` exists on the protocol surface with the exact
  parameter names the brief names.
- the phase-1 `LayeredProposalValidator` satisfies the protocol
  structurally.

layer 3 (seam-F layer-3 phase-1 instance-validation brief):

- `ProposalValidator.validate_instance(...) -> Instance |
  ValidationFailure` exists on the **same** protocol surface (no
  sibling `InstanceLayerValidator` protocol).
- the phase-1 `LayeredProposalValidator` still satisfies the widened
  protocol structurally.

this file guards only the shape of the seam. behavioral proof (layer 1
span validity, layer 2 normalization dispatch, single normalization
site, layer 3 pydantic precedence, manual pass-through, escalation,
`ValidationFailure` typing) lives in `tests/proposals/`.
"""

from __future__ import annotations

import inspect

from extractx.core.contracts import ProposalValidator
from extractx.proposals import LayeredProposalValidator


class TestProposalValidatorProtocolSurface:
    def test_validate_is_a_declared_protocol_member(self) -> None:
        # the named method on the protocol — if this reference disappears,
        # seam F has lost its callable boundary.
        assert hasattr(ProposalValidator, "validate")

    def test_validate_signature_matches_the_seam_f_phase1_contract(self) -> None:
        # architecture §7 seam F + brief: `validate(proposed, field_spec,
        # document_view, schema_cls=None) -> ValidatedField |
        # NegativeOutcome | ValidationFailure`. parameter names are part
        # of the protocol surface; a drift from keyword-capable to
        # positional-only (or a renaming) is caught here once rather
        # than at every validator implementation.
        sig = inspect.signature(ProposalValidator.validate)
        assert list(sig.parameters.keys()) == [
            "self",
            "proposed",
            "field_spec",
            "document_view",
            "schema_cls",
        ]
        # `schema_cls` is optional with default None; this is the phase-1
        # dispatch hinge.
        schema_cls_param = sig.parameters["schema_cls"]
        assert schema_cls_param.default is None

    def test_layered_validator_satisfies_protocol_structurally(self) -> None:
        validator: ProposalValidator = LayeredProposalValidator()
        # structural subtype check — the assignment above is the proof.
        # we also assert it has the method explicitly for legibility.
        assert hasattr(validator, "validate")

    # ------------------------------------------------------------------
    # layer 3 — same protocol, second method (no sibling protocol)
    # ------------------------------------------------------------------

    def test_validate_instance_is_a_declared_protocol_member(self) -> None:
        # canonical seam-F layer 3 lives on the same `ProposalValidator`
        # protocol per the layer-3 brief. there is **no** sibling
        # `InstanceLayerValidator` protocol — if a future thread
        # introduces one, this assertion catches the drift.
        assert hasattr(ProposalValidator, "validate_instance")

    def test_no_sibling_instance_layer_protocol_was_introduced(self) -> None:
        # the layer-3 brief pins: extend the existing protocol, do not
        # add a new sibling. importing such a name from
        # `extractx.core.contracts` should fail.
        from extractx.core import contracts as _contracts

        assert not hasattr(_contracts, "InstanceLayerValidator")

    def test_validate_instance_signature_matches_layer3_brief(self) -> None:
        # brief: `validate_instance(instance_result, spec,
        # schema_cls=None) -> Instance | ValidationFailure`.
        sig = inspect.signature(ProposalValidator.validate_instance)
        assert list(sig.parameters.keys()) == [
            "self",
            "instance_result",
            "spec",
            "schema_cls",
        ]
        schema_cls_param = sig.parameters["schema_cls"]
        assert schema_cls_param.default is None

    def test_layered_validator_satisfies_widened_protocol(self) -> None:
        # structural conformance: the same `LayeredProposalValidator`
        # satisfies the widened `ProposalValidator` protocol (now
        # carrying both `validate` and `validate_instance`).
        validator: ProposalValidator = LayeredProposalValidator()
        assert hasattr(validator, "validate")
        assert hasattr(validator, "validate_instance")
