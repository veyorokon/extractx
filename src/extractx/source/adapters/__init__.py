"""generic `DocumentAdapter` implementations per docs/architecture.md §16.

phase 1 (linearizable subcontract, ADR-0006) ships three adapters:
`TextAdapter`, `MarkdownAdapter`, `HtmlAdapter`. `pdf.py` remains an empty
placeholder; the paginated-visual subcontract is a separate thread and is
not implemented here.

domain adapters (web_forms, clinical, invoices) live in sibling packages, not
under this module.
"""

from __future__ import annotations

from .html import HtmlAdapter
from .markdown import MarkdownAdapter
from .text import TextAdapter

__all__ = [
    "HtmlAdapter",
    "MarkdownAdapter",
    "TextAdapter",
]
