"""Candidate-strategy binding lookup helpers."""

from __future__ import annotations

from extractx.core.exceptions import SpecError
from extractx.core.objects import FieldSpec, StrategyBinding


def binding_for_strategy(
    field_spec: FieldSpec,
    strategy_cls: type,
    strategy_name: str,
) -> StrategyBinding:
    """Return the binding targeting `strategy_cls` for `field_spec`."""

    match: StrategyBinding | None = None
    for binding in field_spec.strategy_bindings:
        if binding.cls is not strategy_cls and not issubclass(binding.cls, strategy_cls):
            continue
        if match is not None:
            raise SpecError(
                f"{strategy_name}: field {field_spec.field_id!r} has multiple "
                f"strategy_bindings entries targeting {strategy_name}",
            )
        match = binding
    if match is None:
        raise SpecError(
            f"{strategy_name}: field {field_spec.field_id!r} has no matching "
            "strategy_bindings entry",
        )
    return match
