"""selection subsystem per docs/architecture.md §7 seam D.

houses `Selector` implementations, the shared selector-boundary
enforcement helper, `ContextPack` builders, and `Prompt` templates.

phase-1 public surface (internal — not tier-1 in `extractx.__init__`):

- `SingletonSelector` — deterministic algorithmic selector (empty /
  singleton / ambiguous policy).
- `enforce_selection_contract` — id-only enforcement applied to every
  selector's raw output before it leaves the seam. both algorithmic and
  llm-backed selectors share this one code path.
- `SelectorContractError` — raised when an impl violates the id-only
  contract or emits a disallowed outcome shape.

the llm-backed default `Selector` ships in `extras/pydantic_ai/` per
ADR-0002 and is out of scope for phase 1.
"""

from __future__ import annotations

from .algorithmic import (
    AMBIGUOUS_REASON_LABEL,
    CategoryRule,
    CategorySignal,
    CategorySignalStrength,
    RuleBasedCategorySelector,
    SingletonSelector,
    algorithmic_code_hash,
)
from .classification_context import RegexWindowClassificationContextStrategy
from .examples import (
    DocumentClassificationReducerPolicy,
    ExpectedObservation,
    SelectorDemo,
    SelectorDemoSet,
    SelectorExample,
    SelectorPromptAssetResolver,
    SelectorPromptPolicy,
    SelectorScore,
    export_selector_examples_jsonl,
    load_selector_examples_jsonl,
    score_selector_observation,
)
from .selector import (
    SelectorContractError,
    enforce_batch_observation_contract,
    enforce_selection_contract,
)

__all__ = [
    "AMBIGUOUS_REASON_LABEL",
    "CategoryRule",
    "CategorySignal",
    "CategorySignalStrength",
    "DocumentClassificationReducerPolicy",
    "ExpectedObservation",
    "SelectorDemo",
    "SelectorDemoSet",
    "SelectorContractError",
    "SelectorExample",
    "SelectorPromptAssetResolver",
    "SelectorPromptPolicy",
    "SelectorScore",
    "RuleBasedCategorySelector",
    "RegexWindowClassificationContextStrategy",
    "SingletonSelector",
    "algorithmic_code_hash",
    "enforce_batch_observation_contract",
    "enforce_selection_contract",
    "export_selector_examples_jsonl",
    "load_selector_examples_jsonl",
    "score_selector_observation",
]
