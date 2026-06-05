"""pydantic-native schema surface per docs/architecture.md §12.

public re-exports for the schema seam:

- `extract_field`  — `pydantic.Field(...)` wrapper that attaches typed
  extractx metadata (see `extract_field.py` / `metadata.py`).
- `from_pydantic`  — builder used by `ExtractionSpec.from_pydantic(Cls)`.
- `ExtractxFieldMetadata` and `EXTRACTX_METADATA_ATTR` are carried as
  plugin-public helpers so downstream seams (strategy, validation,
  debugging) can reach the typed metadata without importing from inside
  `schema/`.

`ExtractionSpec.from_pydantic(...)` lives on the core class as a
classmethod (see `core/objects.py`). its implementation lazily imports
`from_pydantic` here at call time to avoid a core → schema import cycle.
"""

from __future__ import annotations

from .extract_field import extract_field
from .from_pydantic import from_pydantic
from .inference import FieldTypeInfo, analyze_field_annotation
from .metadata import (
    EXTRACTX_METADATA_ATTR,
    ExtractxFieldMetadata,
)
from .object_validators import (
    ObjectValidatorMetadata,
    extractx_object_validator,
    get_object_validator_metadata,
)
from .rehydrate import rehydrate_spec
from .summary import SpecSummary, summarize_spec
from .validators import (
    detect_pydantic_as_extractor,
    pydantic_as_extractor_disallowed,
)

__all__ = [
    "EXTRACTX_METADATA_ATTR",
    "ExtractxFieldMetadata",
    "FieldTypeInfo",
    "ObjectValidatorMetadata",
    "SpecSummary",
    "analyze_field_annotation",
    "detect_pydantic_as_extractor",
    "extract_field",
    "extractx_object_validator",
    "from_pydantic",
    "get_object_validator_metadata",
    "pydantic_as_extractor_disallowed",
    "rehydrate_spec",
    "summarize_spec",
]
