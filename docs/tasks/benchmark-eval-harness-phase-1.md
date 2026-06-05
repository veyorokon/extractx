# Task: Benchmark Primitives Phase 1

ADR-0020 supersedes the earlier live eval-harness wording. The sibling package
now has two separate surfaces:

- benchmark primitives: deterministic fixtures, reports, and future scorers;
- live smoke: production `extract(...)` run that requires replay and can be
  value-checked afterward.

## Goal

Land the first benchmark primitive slice without turning live extraction into a
benchmark API.

Done means `packages/extractx_eval` exposes:

- `BenchmarkFixture`, `GoldInstance`, `GoldField`, and `GoldEvidence`;
- `FixturePack` and `load_fixture_pack(...)` for JSONL + raw-document fixture
  packs;
- serializable `BenchmarkReport`, `BenchmarkCaseRow`, `BenchmarkFieldRow`, and
  aggregate rows;
- root isolation tests proving `src/extractx` does not import `extractx_eval`.

## Guardrails

- No live LLM call in benchmark scoring.
- No benchmark-only execution path.
- No domain thresholds or pass/fail policy.
- No core `src/extractx` dependency on `extractx_eval`.
- Evidence text and `SourceSpan` expectations are both first-class fixture
  shapes.

## Related

- [ADR-0020](../adr/0020-benchmark-primitives-over-benchmark-product.md)
