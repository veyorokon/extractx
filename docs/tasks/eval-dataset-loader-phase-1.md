# Task: Smoke Dataset Loader

ADR-0020 reframed the old live eval manifest as a smoke manifest. This task now
documents that surface.

## Goal

Load local smoke manifests into `SmokeCase` objects.

Done means callers can use:

```python
load_smoke_dataset(
    manifest_path,
    schema_registry={...},
    store_factory=...,
)
```

The manifest version is `extractx_eval.smoke_dataset.v1`.

## Guardrails

- Explicit schema registry only; no schema imports from the manifest.
- Caller-owned `store_factory`.
- Reject duplicate case IDs.
- Reject absolute paths and path escapes.
- No benchmark scoring in the loader.
- No core `src/extractx` changes.

## Related

- [ADR-0020](../adr/0020-benchmark-primitives-over-benchmark-product.md)
