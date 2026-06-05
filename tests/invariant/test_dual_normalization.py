"""invariant test for §15 `Dual Normalization`.

seam F layer 2 is the single normalization site (docs/architecture.md
§7 seam F + §15). seam E (`CardinalitySelectionAdapter` in
`proposals/adapter.py`) must never call `ValidationBinding.normalizer`,
`pydantic.TypeAdapter.validate_python`, `BaseModel.model_validate`, or
any other normalization entry point — doing so would be `Dual
Normalization`.

this file is a static (AST-level) check over the seam-E module's source
text. a runtime behavioral check is not sufficient because a future
refactor could normalize only on a rare code path; an AST-level check
catches the introduction at code-review time.
"""

from __future__ import annotations

import ast
import pathlib

import extractx.proposals.adapter as _adapter_module

_ADAPTER_FILE = pathlib.Path(_adapter_module.__file__)


class TestDualNormalizationStaticCheck:
    def test_adapter_source_does_not_mention_normalization_entry_points(
        self,
    ) -> None:
        # forbidden tokens: names that would indicate a normalization
        # call being smuggled into seam E. we check at the source-text
        # level so any reintroduction surfaces at review time.
        source = _ADAPTER_FILE.read_text(encoding="utf-8")
        forbidden = (
            "normalizer",  # ValidationBinding.normalizer invocation
            "TypeAdapter",  # pydantic coercion entry point
            "model_validate",  # BaseModel.model_validate invocation
            "validate_python",  # TypeAdapter.validate_python invocation
        )
        # strip out docstrings/comments? no — we want the strongest
        # signal. if docstrings ever need to reference these names, the
        # test is a conversation trigger, not a false positive.
        present = [token for token in forbidden if token in source]
        assert present == [], (
            f"proposals/adapter.py references forbidden normalization entry "
            f"points {present!r} — seam E must not normalize (§15 "
            f"`Dual Normalization`). normalization is seam F layer 2's "
            f"exclusive site."
        )

    def test_adapter_does_not_import_validation_module(self) -> None:
        # defense in depth: even if the tokens do not appear directly,
        # importing `proposals.validation` from `proposals.adapter` is
        # structurally wrong at seam E.
        source = _ADAPTER_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.endswith("proposals.validation") or mod.endswith(
                    "extractx.proposals.validation",
                ):
                    offenders.append(mod)
                if "validation" in mod and mod.endswith("proposals.validation"):
                    offenders.append(mod)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("proposals.validation"):
                        offenders.append(alias.name)
        assert offenders == [], (
            f"proposals/adapter.py imports validation module(s) {offenders!r} "
            f"— seam E must not depend on seam F layer 2."
        )
