"""stable content-hash helpers and `producer_version` composition helpers.

see docs/architecture.md §4 (canonical vocabulary, `producer_version`
definition) and §8 (soft-compute discipline).

`producer_version` shape:
- soft producer: `"{model_id}|{prompt_template_hash}|{code_hash}"`
- algorithmic producer: `"code:{code_hash}"` with model and prompt_template
  fields null when surfaced through the object layer

this module provides two categories of helper:

1. `stable_hash(obj)` — deterministic content hash for json-serializable
   values. used by spec version composition, candidate id composition, and
   the other hash sites that downstream seams need without reinventing the
   canonicalization rule.
2. `algorithmic_producer_version` / `soft_producer_version` — compose the
   two documented shapes from their components.

these helpers are pure: no env access, no filesystem access, no wall-clock
time. downstream code hashing relies on json canonicalization (sorted keys,
utf-8, no trailing whitespace), which is deterministic for the value types
that reach this helper.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

__all__ = [
    "algorithmic_producer_version",
    "soft_producer_version",
    "stable_hash",
]


def stable_hash(value: Any) -> str:
    """return a deterministic sha256 hex digest over a json-serializable value.

    json canonicalization uses sorted keys, utf-8 encoding, and no
    indentation. non-json primitive types (tuples) are normalized to lists
    via `_to_json_safe` before encoding so that `hash((1, 2)) == hash([1, 2])`
    at the json layer. callers who want tuples and lists to hash differently
    should pre-normalize or use a different canonicalization.

    raises `TypeError` if the value cannot be json-encoded.
    """

    payload = json.dumps(
        _to_json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def algorithmic_producer_version(code_hash: str) -> str:
    """compose the documented algorithmic-producer `producer_version` shape.

    see docs/architecture.md §4: `code:{code_hash}` with model and
    prompt_template fields null when surfaced through the object layer.
    """

    if not code_hash:
        raise ValueError("algorithmic_producer_version: code_hash must be non-empty")
    return f"code:{code_hash}"


def soft_producer_version(
    *,
    model_id: str,
    prompt_template_hash: str,
    code_hash: str,
) -> str:
    """compose the documented soft-producer `producer_version` shape.

    see docs/architecture.md §4 and §8: `"{model_id}|{prompt_template_hash}|{code_hash}"`.
    all three components must be non-empty for soft producers — if any is
    absent, the producer is algorithmic and should use
    `algorithmic_producer_version` instead.
    """

    for field_name, field_value in (
        ("model_id", model_id),
        ("prompt_template_hash", prompt_template_hash),
        ("code_hash", code_hash),
    ):
        if not field_value:
            raise ValueError(
                f"soft_producer_version: {field_name} must be non-empty; use "
                "algorithmic_producer_version for algorithmic producers",
            )
    return f"{model_id}|{prompt_template_hash}|{code_hash}"


def _to_json_safe(value: Any) -> Any:
    """recursively normalize a value into a json-serializable form.

    tuples and frozensets become lists; mappings become dicts; everything
    else is passed through. this is a narrow helper for `stable_hash` and
    is not a general-purpose serializer — it intentionally does not know
    about pydantic models (callers that want to hash a pydantic model
    should pass `model.model_dump(mode="json")` explicitly).
    """

    if isinstance(value, tuple):
        t = cast("tuple[Any, ...]", value)
        return [_to_json_safe(v) for v in t]
    if isinstance(value, list):
        lst = cast("list[Any]", value)
        return [_to_json_safe(v) for v in lst]
    if isinstance(value, frozenset):
        fs = cast("frozenset[Any]", value)
        return sorted(_to_json_safe(v) for v in fs)
    if isinstance(value, set):
        s = cast("set[Any]", value)
        return sorted(_to_json_safe(v) for v in s)
    if isinstance(value, dict):
        items = cast("dict[Any, Any]", value)
        return {str(k): _to_json_safe(v) for k, v in items.items()}
    return value
