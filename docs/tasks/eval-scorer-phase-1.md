# Task: Smoke Value Check

ADR-0020 separates live smoke from deterministic benchmark scoring. The old
exact scorer is now the smoke value-check projection.

## Goal

Compare expected final values against canonical `Extraction.instances`
reconstructed from replay.

Done means `smoke_check_values(smoke_result, smoke_case)` returns a
`ValueCheckResult` with typed value diffs:

- `missing_field`
- `unexpected_field`
- `value_mismatch`
- `instance_count_mismatch`

## Guardrails

- Value checking is deterministic and replay-backed.
- The live smoke run does not carry materialized extraction objects.
- No candidate scoring or replay-stage diagnosis in this surface.
- No fuzzy matching or normalization inside the checker.

## Related

- [ADR-0020](../adr/0020-benchmark-primitives-over-benchmark-product.md)
