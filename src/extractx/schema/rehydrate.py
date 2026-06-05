"""`rehydrate_spec` — rebuild a runnable `ExtractionSpec` from a
persisted `SpecSummary` plus the registered live `schema_cls`.

per docs/tasks/m9-phase-2-replay-re-execution.md §2 and the M9 phase-2
hard pin #4: rehydration calls `ExtractionSpec.from_pydantic(schema_cls)`
with **no policy args**, then asserts composition stability and field
shape against the persisted summary. policy fields on `SpecSummary`
(`prompt_policy`, `validation_policy`, `grouping_policy`, `budget`) are
forensic record / cross-check surface, **not** the rehydration source.

failure surface (typed `InfrastructureError` with pinned prefixes):

- `"spec_rehydrate.manual_unsupported: ..."` — `summary.source_schema_ref`
  is `None`. manual replay is deferred to a follow-on thread that
  introduces a public `register_for_replay(...)` api.
- `"spec_rehydrate.missing_class: ..."` — caller passed a `schema_cls`
  whose qualname does not match what the registry has for
  `summary.spec_version`. callers are expected to look up `schema_cls`
  via `lookup_schema_cls(summary.spec_version)` before calling here.
- `"spec_rehydrate.version_mismatch: ..."` — the rehydrated spec's
  composed `version` diverges from `summary.spec_version`. a load-bearing
  drift surface for `from_pydantic`'s hash composition.
- `"spec_rehydrate.field_drift: ..."` — the rehydrated spec's field-id
  tuple diverges from `summary.field_summaries`'s field-id tuple.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from extractx.core.exceptions import InfrastructureError
from extractx.core.objects import ExtractionSpec

from ._schema_cls_registry import lookup_schema_cls

if TYPE_CHECKING:
    from .summary import SpecSummary

__all__ = ["rehydrate_spec"]


def _qualname_of(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def rehydrate_spec(
    summary: SpecSummary,
    *,
    schema_cls: type[BaseModel],
) -> ExtractionSpec:
    """rebuild a runnable `ExtractionSpec` from `summary` and live `schema_cls`.

    rehydration strategy (load-bearing): call
    `ExtractionSpec.from_pydantic(schema_cls)` with no policy args and
    trust the registry-resolved live class to produce the same spec the
    original `from_pydantic` call did. `summary.field_summaries` is the
    field-shape sanity-check surface; it is **not** the rehydration
    source.

    raises `InfrastructureError` with one of the four pinned prefixes
    (`spec_rehydrate.manual_unsupported`, `spec_rehydrate.missing_class`,
    `spec_rehydrate.version_mismatch`, `spec_rehydrate.field_drift`).
    """

    # manual specs (`source_schema_ref is None`) are deferred — replay
    # of manual specs requires a public registration api that is a
    # follow-on coordinator-owned thread.
    if summary.source_schema_ref is None:
        raise InfrastructureError(
            "spec_rehydrate.manual_unsupported: phase 2 supports "
            f"pydantic-backed specs only (spec_version="
            f"{summary.spec_version!r})",
        )

    # caller is expected to have looked up `schema_cls` via
    # `lookup_schema_cls(summary.spec_version)`. cross-check that the
    # registry's mapping for this `spec_version` agrees with the
    # provided class — otherwise the caller smuggled an unrelated
    # class in and we surface that as a missing-class error rather
    # than running `from_pydantic` against the wrong type.
    registered = lookup_schema_cls(summary.spec_version)
    if registered is None:
        raise InfrastructureError(
            "spec_rehydrate.missing_class: spec_version="
            f"{summary.spec_version!r} has no live schema class "
            "registered in this process; build the spec via "
            "ExtractionSpec.from_pydantic(...) before calling "
            "rehydrate_spec",
        )
    if registered is not schema_cls:
        raise InfrastructureError(
            "spec_rehydrate.missing_class: provided schema_cls "
            f"qualname={_qualname_of(schema_cls)!r} does not match "
            "the class registered under "
            f"spec_version={summary.spec_version!r} "
            f"(registered qualname={_qualname_of(registered)!r})",
        )

    # rebuild via the canonical seam-B entry point. no policy args —
    # `from_pydantic`'s default policy materialization is the same one
    # the original call used (M9 phase-2 hard pin #4). passing
    # `summary.prompt_policy` / etc here would re-introduce the
    # version-composition ambiguity the brief warns about (the
    # persisted summary carries materialized defaults whereas the
    # original call passed `None`).
    spec = ExtractionSpec.from_pydantic(schema_cls)

    # composition-stability check. silent drift in `from_pydantic`'s
    # hash composition would otherwise let a mismatched spec quietly
    # rehydrate.
    if spec.version != summary.spec_version:
        raise InfrastructureError(
            "spec_rehydrate.version_mismatch: rehydrated "
            f"spec.version={spec.version!r} != "
            f"summary.spec_version={summary.spec_version!r}",
        )

    # field-shape sanity check. closes the silent-drift surface where
    # `from_pydantic`'s field-extraction logic could diverge between
    # original-run time and replay time (e.g. removed annotation,
    # renamed field).
    rehydrated_field_ids = tuple(f.field_id for f in spec.fields)
    summary_field_ids = tuple(s.field_id for s in summary.field_summaries)
    if rehydrated_field_ids != summary_field_ids:
        raise InfrastructureError(
            "spec_rehydrate.field_drift: rehydrated "
            f"field_ids={list(rehydrated_field_ids)!r} != "
            f"summary field_ids={list(summary_field_ids)!r}",
        )

    return spec
