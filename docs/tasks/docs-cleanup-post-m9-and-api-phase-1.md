# Task: docs cleanup after M9 + api phase 1 — status promotion and architecture drift alignment

*This is Lane C cycle 1: docs-only cleanup after M9 phase 1, M9 phase 2, replay drift-gate phase 1, and api-redesign phase 1. The goal is to clear stale wording and status drift that keeps bleeding into new briefs, without touching code and without colliding with the seam-K thread. This thread owns docs hygiene and status alignment only. It does not define new contracts, widen the api, or preempt seam-K-owned architecture sections.*

## Read first

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / proof doctrine
- [`CODEX.md`](../../CODEX.md) — current repo-local operating guide
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§10 three-tier public surface**, **§13 public api surface**, **§15 anti-patterns**, **§17 done criteria**; note that **§7 seam H** and **§9 `ExecutionTrace`** are owned by the seam-K thread and are out of scope here
- [`docs/adr/README.md`](../adr/README.md) — ADR index
- [`docs/adr/0007-storage-shape-authority-and-minimum-skeleton.md`](../adr/0007-storage-shape-authority-and-minimum-skeleton.md) — current status and landed authority model
- [`docs/tasks/README.md`](README.md) — task index
- [`docs/thread-orchestration.md`](../thread-orchestration.md) — lane/process context
- landed task briefs for context only:
  - [`m9-phase-1-replay-storage-skeleton.md`](m9-phase-1-replay-storage-skeleton.md)
  - [`m9-phase-2-replay-re-execution.md`](m9-phase-2-replay-re-execution.md)
  - [`replay-drift-gate-phase-1-validator-coverage.md`](replay-drift-gate-phase-1-validator-coverage.md)
  - [`api-phase-1-extract-function.md`](api-phase-1-extract-function.md)

## Goal

Bring repo-facing docs back into alignment with already-landed code and accepted direction, while staying out of seam-K-owned contract edits.

**"Done" in one sentence:** docs reflect the landed M9 + drift-gate + api-phase-1 facts, ADR-0007 is promoted if the cleanup confirms the M9 family has fully operationalized it, task indexes are current, and no src/ file changes occur.

## Scope

### 1. Promote ADR-0007 if code reality supports it

Requirements:

- verify that the landed code now operationalizes ADR-0007 on the supported path closely enough to justify status promotion from `Proposed` to `Accepted`
- if yes:
  - update [`docs/adr/0007-storage-shape-authority-and-minimum-skeleton.md`](../adr/0007-storage-shape-authority-and-minimum-skeleton.md) status header to `Accepted`
  - update the matching status wording in [`docs/adr/README.md`](../adr/README.md)
- if no, surface the blocking gap in the final report and do **not** promote it

Implementation-shape constraints:

- promotion here is status alignment only
- do **not** rewrite ADR-0007’s decision substance unless a clearly stale sentence is directly contradicted by landed code

### 2. Clean owned sections of `docs/architecture.md`

This lane owns these `docs/architecture.md` sections:

- **§7 seam H — storage-wording half only (for ADR-0007 promotion alignment)**
- **§10 three-tier public surface**
- **§13 public api surface**
- **§15 anti-patterns**
- **§17 done criteria**

Requirements:

- update those sections to reflect landed facts. known-stale claims to fix in this pass:
  - **§10:** “single function” wording predates `extract(...)` + `run_extraction(...)`; the landed surface is two symbols, not one
  - **§13:** `extract(...)` is missing from the public api surface section; add it as the schema-first happy path with `run_extraction(...)` retained as the engine path
  - **§15:** anti-pattern wording predates the captured-keyed drift-gate compat posture; align with how `ReplayDriftGate` actually iterates `producer_versions`
  - **§17:** “bytewise” replay equality wording overstates what `assert_replay_result_equal` proves — it excludes `replay_artifact_ref` and (pre-seam-K) excludes `trace.events`; narrow honestly without preempting seam K
  - **§7 seam H storage half:** align with landed `ExtractxStore` / `LocalFilesystemStore` / `RunManifest.from_artifact` reality if ADR-0007 is promoted
- do **not** edit the trace / replay-equality half of §7 seam H, §7 seam K trace-semantics wording, or §9 `ExecutionTrace` — seam K owns those in parallel
- if you find a stale claim outside the owned sections (or in the trace half of §7 seam H), record it under `## Drift` and leave it untouched

Implementation-shape constraints:

- the §7 seam H edit is allowed **only** for storage-wording alignment driven by ADR-0007 promotion; no trace, replay-equality, or `ExecutionTrace` wording belongs to this lane
- do **not** preempt seam-K wording changes

### 3. Align `CODEX.md` and task indexes

Requirements:

- update [`CODEX.md`](../../CODEX.md) where it is stale relative to landed M9/api-phase-1 facts, but only for already-decided behavior. expected sections to touch:
  - the seam map / row table where seams H, K, and storage wording are stale
  - the replay debugging / replay-fixture recipe where it predates source-driven re-execution and the captured-keyed drift gate
  - the public surface mention so it names `extract(...)` alongside `run_extraction(...)`
  - if other CODEX.md sections look stale, record under `## Drift` and leave untouched
- update [`docs/tasks/README.md`](README.md) so the index reflects:
  - replay drift-gate phase 1
  - api phase 1
  - seam K phase 1 brief if present
  - this docs-cleanup brief
- keep wording factual; no new architecture claims

### 4. Zero-code docs hygiene only

Requirements:

- keep this thread docs-only
- ensure no `src/` edits, no tests, no runtime behavior changes
- if a doc fix appears to require code interpretation beyond already-landed facts, surface it as drift instead of improvising

## Guardrails

- **write scope:** `docs/adr/0007-storage-shape-authority-and-minimum-skeleton.md`, `docs/adr/README.md`, owned sections of [`docs/architecture.md`](../architecture.md) (storage half of §7 seam H + §10 + §13 + §15 + §17), [`CODEX.md`](../../CODEX.md), [`docs/tasks/README.md`](README.md)
- **no src/ edits**
- **do not edit** the trace / replay-equality half of `docs/architecture.md` §7 seam H, §7 seam K trace-semantics wording, or §9 `ExecutionTrace`; seam K owns those
- **no new architecture claims**
- **no changes** to task briefs other than adding index entries where appropriate
- **no commits or pushes** unless separately asked

## Pushback discipline

If status promotion or wording cleanup runs into a real contradiction:

- current contract:
- observed gap or contradiction:
- consequence if edited as written:
- proposed cleaner pattern:
- whether this is docs drift vs architecture change:
- proof target:

and stop short of improvising.

## Deliverable

Docs-only edits in the repo.

Include in the final report:

- exact files changed
- whether ADR-0007 was promoted or intentionally left `Proposed`
- exact `docs/architecture.md` sections edited
- any stale claims found outside owned sections
- explicit confirmation that `src/` diff is zero

## Success criteria

- ADR-0007 status is honestly aligned with landed code
- owned `docs/architecture.md` sections reflect landed api/replay/storage facts
- `CODEX.md` and task indexes are current
- zero-line diff for `src/`

## Downstream consequences

- future briefs stop carrying repetitive “doc wording stale” drift notes for already-landed facts
- benchmark and smoke sidecars can start from a cleaner docs baseline
- seam K remains free to land its owned architecture wording without docs-lane collision
