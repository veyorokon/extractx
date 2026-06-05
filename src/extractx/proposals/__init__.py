"""proposal subsystem per docs/architecture.md §7 seams E and F.

phase-1 public surface (internal — not tier-1 in `extractx.__init__`):

- `CardinalitySelectionAdapter` — deterministic seam-E adapter that turns
  a `Observation + CandidateSet + FieldSpec` into `tuple[ProposedField, ...]`
  or one typed `NegativeOutcome`, per the seam-E cardinality table.
- `SelectionAdapterContractError` — local `ValueError` subtype raised when
  a structural invariant at seam E is violated (field-id mismatch, missing
  selected id, duplicate selected id).
- `LayeredProposalValidator` — deterministic seam-F phase-1 validator
  that runs layer 1 (candidate shape + source-span validity per
  ADR-0006) and layer 2 (the single normalization site per §15
  `Dual Normalization`). layer 3 (cross-field, post-`G.resolver`) is
  out of scope for phase 1 per ADR-0003 and lands in a later thread.
- `ProposalValidatorContractError` — local `ValueError` subtype raised
  when a `FieldSpec` reaches the validator in a shape seam B should
  have rejected at spec load (missing `validation_binding` on the
  manual path, mismatched `schema_cls` on the pydantic-backed path).

`provenance.py` helpers remain stubs and will be wired by their owning
task.
"""

from __future__ import annotations

from .adapter import CardinalitySelectionAdapter, SelectionAdapterContractError
from .validation import LayeredProposalValidator, ProposalValidatorContractError

__all__ = [
    "CardinalitySelectionAdapter",
    "LayeredProposalValidator",
    "ProposalValidatorContractError",
    "SelectionAdapterContractError",
]
