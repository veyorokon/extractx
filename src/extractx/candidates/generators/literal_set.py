"""schema-derived Literal candidate generation for document classification."""

from __future__ import annotations

from extractx.core import (
    Candidate,
    CandidateSet,
    DocumentView,
    FieldSpec,
    InstanceHint,
    SourceSpan,
    StructuralStatus,
)
from extractx.core.exceptions import SpecError
from extractx.core.objects import StrategyBinding
from extractx.core.versions import algorithmic_producer_version, stable_hash

from ..candidate_set import build_candidate_set, candidate_id_for
from ._binding import binding_for_strategy

__all__ = [
    "LITERAL_SET_CONTRACT_ID",
    "LiteralSetCandidateStrategy",
    "algorithmic_code_hash",
]


LITERAL_SET_CONTRACT_ID = "literal_set_strategy_v1"


class LiteralSetCandidateStrategy:
    """emit one structured candidate per string `Literal[...]` arm.

    This is the document-level classification source. The candidates are
    grounded in the schema rather than document prose, so each candidate
    carries a synthetic zero-length document-head span and a passing
    structural status by construction.
    """

    def generate(
        self,
        field_spec: FieldSpec,
        document_view: DocumentView,
        instance_hint: InstanceHint | None = None,
    ) -> CandidateSet:
        self._assert_binding_targets_self(
            binding_for_strategy(
                field_spec,
                LiteralSetCandidateStrategy,
                "LiteralSetCandidateStrategy",
            ),
            field_spec,
        )
        if field_spec.literal_values == ():
            raise SpecError(
                "LiteralSetCandidateStrategy: field "
                f"{field_spec.field_id!r} has no literal_values; "
                "use it only with string Literal[...] CATEGORY fields",
            )

        strategy_id = _strategy_id(field_spec)
        span = SourceSpan(
            source_ref=document_view.source_ref,
            text_anchor_space="normalized_text",
            byte_start=0,
            byte_end=0,
        )
        candidates = tuple(
            Candidate(
                candidate_id=candidate_id_for(
                    strategy_id=strategy_id,
                    source_span=span,
                    normalized_structural_payload={
                        "field_id": field_spec.field_id,
                        "literal": value,
                    },
                ),
                text=value,
                source_kind="structured",
                source_id="literal_set",
                source_span=span,
                normalized_hint=value,
                structured_payload={"literal": value},
                structural_status=StructuralStatus(
                    passed=True,
                    contract_id=LITERAL_SET_CONTRACT_ID,
                ),
            )
            for value in field_spec.literal_values
        )
        return build_candidate_set(
            field_id=field_spec.field_id,
            document_id=document_view.document_id,
            candidates=candidates,
            strategy_id=strategy_id,
            instance_hint=instance_hint,
        )

    def _assert_binding_targets_self(
        self,
        binding: StrategyBinding | None,
        field_spec: FieldSpec,
    ) -> None:
        if binding is None:
            raise SpecError(
                "LiteralSetCandidateStrategy: field "
                f"{field_spec.field_id!r} has no matching strategy_bindings entry",
            )
        if binding.kind != "candidate":
            raise SpecError(
                "LiteralSetCandidateStrategy: StrategyBinding.kind must be "
                f"'candidate', got {binding.kind!r}",
            )
        cls = binding.cls
        if cls is not LiteralSetCandidateStrategy and not issubclass(
            cls,
            LiteralSetCandidateStrategy,
        ):
            raise SpecError(
                "LiteralSetCandidateStrategy: StrategyBinding.cls names "
                f"{cls!r}, not LiteralSetCandidateStrategy",
            )


def _strategy_id(field_spec: FieldSpec) -> str:
    return "literal_set:v1:" + stable_hash(
        {
            "field_id": field_spec.field_id,
            "literal_values": field_spec.literal_values,
        },
    )


def algorithmic_code_hash() -> str:
    digest = stable_hash(
        f"{LiteralSetCandidateStrategy.__module__}.{LiteralSetCandidateStrategy.__qualname__}",
    )
    return algorithmic_producer_version(code_hash=digest)
