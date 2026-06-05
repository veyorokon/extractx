"""Replay-facing names for the ADR-0008 core vocabulary migration.

This module intentionally does not define replacement core models. Until
the core vocabulary branch lands, these names alias the current canonical
core objects so replay code can move to the new vocabulary without
forking the object model.
"""

from __future__ import annotations

from extractx.core.outcomes import Evidence, Extraction, Instance

__all__ = ["Evidence", "Extraction", "Instance"]
