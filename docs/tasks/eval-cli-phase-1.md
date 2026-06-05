# Task: Smoke CLI

The package command remains `extractx-eval`, but the `run` subcommand is a live
smoke runner, not a benchmark scorer.

## Goal

Run a local smoke manifest through production `extract(...)`, require replay,
and emit a JSON `SmokeReport`.

Done means:

```text
extractx-eval run <manifest> --schema schema_id=module:Class --store-root <dir>
```

returns `0` when every smoke run completes and every optional value check
matches, and returns `1` when a smoke run errors or a value check mismatches.

## Guardrails

- No schema auto-discovery.
- No dynamic imports from the manifest.
- No remote loading.
- No deterministic benchmark scoring in the CLI.
- No core `src/extractx` changes.

## Related

- [ADR-0020](../adr/0020-benchmark-primitives-over-benchmark-product.md)
