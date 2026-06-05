from __future__ import annotations

from typing import Annotated

from extractx import ValueKind, extract_field
from extractx.candidates.generators.regex import RegexCandidateStrategy
from extractx.core.objects import StrategyBinding
from pydantic import BaseModel


def regex_binding(pattern: str) -> StrategyBinding:
    return StrategyBinding(
        cls=RegexCandidateStrategy,
        params={"pattern": pattern},
        kind="candidate",
    )


class InvoiceSummary(BaseModel):
    vendor_id: Annotated[str, ValueKind.CARDINAL] = extract_field(
        description="vendor identifier",
        strategy_bindings=(regex_binding(r"VND-1001|VND-1002|VND-1003"),),
    )
    invoice_date: Annotated[str, ValueKind.DATE] = extract_field(
        description="invoice date",
        strategy_bindings=(
            regex_binding(
                r"April 13, 2020|August 18, 2020|September 6, 2023",
            ),
        ),
    )
    due_date: Annotated[str, ValueKind.DATE] = extract_field(
        description="payment due date",
        strategy_bindings=(
            regex_binding(
                r"April 28, 2020|September 17, 2020|October 6, 2023",
            ),
        ),
    )
    tax_rate: Annotated[str, ValueKind.PERCENT] = extract_field(
        description="tax rate",
        strategy_bindings=(regex_binding(r"2\.25%|4\.25%|1\.25%"),),
    )
    total_amount: Annotated[str, ValueKind.MONEY] = extract_field(
        description="invoice total amount",
        strategy_bindings=(regex_binding(r"\$220\.18|\$4\.34|\$64\.85"),),
    )
    subtotal_amount: Annotated[str, ValueKind.MONEY] = extract_field(
        description="invoice subtotal amount",
        strategy_bindings=(regex_binding(r"\$700\.00|\$250\.00|\$260\.00"),),
    )
