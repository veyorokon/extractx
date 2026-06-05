# Task: process phase 1 — codify lane worktrees, section ownership, and evidence bundles

*This is a repo-process thread, not a feature thread. The goal is to operationalize the new multi-lane development model so parallel work increases validated throughput instead of just creating more merge churn. Keep this thread docs-only and narrow: codify the lane model, persistent worktree rules, docs section ownership, rebase-before-thread preflight, and the canonical evidence-bundle template. Do not turn this into a broad project-management rewrite.*

## Read first

- [`AGENTS.md`](../../AGENTS.md) — generic thread / seam / proof doctrine
- [`CODEX.md`](../../CODEX.md) — current repo-local workflow rules
- [`CLAUDE.md`](../../CLAUDE.md) — git / hook / tool policies
- [`docs/thread-orchestration.md`](../thread-orchestration.md) — current operating model
- [`docs/tasks/README.md`](README.md) — current task lifecycle wording
- recent process-driving task briefs for examples of evidence bundles / pushback shape:
  - [`api-phase-1-extract-function.md`](api-phase-1-extract-function.md)
  - [`replay-drift-gate-phase-1-validator-coverage.md`](replay-drift-gate-phase-1-validator-coverage.md)
  - [`seam-k-phase-1-typed-execution-trace.md`](seam-k-phase-1-typed-execution-trace.md) if present
  - [`docs-cleanup-post-m9-and-api-phase-1.md`](docs-cleanup-post-m9-and-api-phase-1.md) if present

## Goal

Codify the first operating-model upgrade so the team can run three lanes in parallel without re-explaining the rules in chat every time.

**"Done" in one sentence:** repo docs describe the three-lane model, persistent worktree rules, `docs/architecture.md` section ownership, rebase-before-thread preflight, one-pushback-round default with substantive-pushback exception, and a canonical evidence-bundle template.

## Scope

### 1. Codify the lane model

Requirements:

- update [`docs/thread-orchestration.md`](../thread-orchestration.md) to reflect the active operating model:
  - **Lane A** — primary product implementation
  - **Lane B** — secondary internal-correctness implementation
  - **Lane C** — sidecar (docs / eval / other low-conflict work)
- make explicit that **verification is a discipline applied to every lane**, not a lane by itself
- keep one integrator / many bounded workers

### 2. Persistent lane worktrees + rebase-before-thread

Requirements:

- document the persistent-worktree model for lane branches/worktrees
- pin worktree names:
  - **Lane B (replay/internal-correctness):** worktree at `extractx-replay`, branch `lane-replay`
  - **Lane C (docs/sidecar):** worktree at `extractx-docs`, branch `lane-docs`
  - **Lane A (primary product):** stays on the main checkout against `dev` for now; persistent worktree deferred until parallel demand justifies it
- pin the first preflight step for any persistent lane worktree:
  - `git fetch origin && git rebase origin/dev`
  - **rebase, not merge** — lane history must stay linear so cherry-pick replays into `dev` produce clean commits
  - if `rebase` conflicts, the lane stops, surfaces the conflict in chat, and does not proceed against a half-rebased tree
- worktree-local destructive commands (e.g., resetting a lane branch back to `origin/dev` after a failed cycle) are acceptable when stated as lane-local and intentional; never use them on `dev` itself

### 3. `docs/architecture.md` section ownership

Requirements:

- record the current section-ownership rule that prevents Lane B / Lane C collisions:
  - **Lane B owns:** trace / replay-equality half of §7 seam H, §7 seam K (trace-semantics wording only, no reporter widening), §9 `ExecutionTrace`
  - **Lane C owns:** storage-wording half of §7 seam H (for ADR-0007 promotion alignment only), §10, §13, §15, §17, ADR status, `CODEX.md`, and other docs-hygiene surfaces
  - **Lane A** does not touch `docs/architecture.md` in the current operating model unless explicitly granted
- §7 seam H is the only architecture section split between two lanes; both lanes treat the other half as out-of-scope
- if a lane discovers a needed edit outside its owned sections (or across the §7 seam H split boundary), it surfaces drift instead of editing through the boundary

### 4. Canonical evidence-bundle template

Requirements:

- create a lightweight repo-tracked process doc for the standard acceptance/evidence bundle, or add an equivalent explicit subsection to an existing process doc if that is cleaner
- pin the standard worker report shape:
  - **preflight** — `pwd`, `git rev-parse --abbrev-ref HEAD`, `git status`, `git log -1 --oneline`
  - **files changed** — list of paths touched
  - **implementation notes** — short narrative; load-bearing decisions only
  - **test delta** — exact format: `+N tests added, M passing total, K failing → 0 failing` (any non-zero failure terminates the bundle)
  - **proof** — exact gate list: `uv sync`, `uv run pytest`, `uv run ruff check`, `uv run pyright` — all four must pass; report each result explicitly
  - **drifts** — any out-of-scope observations the worker noticed but did not edit
  - **pushback-or-omit** — substantive pushback (per §5) or explicit "none"
  - **commit hash** — final commit on the lane branch
- make explicit that each lane can add lane-specific checklist items, but the base evidence-bundle skeleton is shared

### 5. Brief-freeze rule with substantive-pushback exception

Requirements:

- codify:
  - one pushback round by default
  - coordinator pins remaining ambiguities
  - substantive worker pushback re-opens the brief
- pin "substantive" precisely:
  - **substantive** = names a contract gap, an ownership collision, or a write-scope contradiction
  - **cosmetic** = naming, ordering, wording, or test-shape preference; cosmetic feedback is logged in the evidence bundle but does not re-open the brief
- make it explicit that this is a throughput rule, not a ban on real discovery

### 6. Cross-lane drift rollup

Requirements:

- create an append-only `docs/process/drift-log.md` with a brief header explaining its purpose
- codify the rule: when a worker reports drift in its evidence bundle on files outside its lane's write scope, the integrator (or coordinator) appends those drift items to `docs/process/drift-log.md` keyed by the originating thread
- Lane C cycles consume the drift log: each Lane C thread picks up open drift items in its scope and closes them by editing them out (line-deletes from the log) when the relevant doc is updated
- the drift log is not a TODO list for code work — it tracks docs / wording / status drift only
- if a drift item is actually a contract gap (not docs drift), it gets escalated to a new task brief instead of accumulating in the log

## Guardrails

- **docs-only thread**
- likely write scope: [`docs/thread-orchestration.md`](../thread-orchestration.md), [`CODEX.md`](../../CODEX.md) if needed for workflow alignment, a new `docs/process/evidence-bundle.md` (or equivalent template doc), a new empty `docs/process/drift-log.md` (append-only rollup; created with header only), and task-index references only if genuinely needed
- **no src/ edits**
- **no architecture-semantics changes**
- **no task brief rewrites** beyond adding index/reference links if helpful
- **no commits or pushes** unless separately asked

## Pushback discipline

If the process codification would conflict with already-landed repo rules:

- current process rule:
- observed contradiction:
- consequence if codified as written:
- proposed cleaner wording:
- whether this is process clarification vs policy change:
- proof target:

and stop short of inventing a new process regime silently.

## Deliverable

Docs-only process updates in the repo.

Include in the final report:

- exact files changed
- where the lane model is codified
- where the evidence-bundle template lives
- confirmation that `src/` diff is zero
- any process rule intentionally deferred

## Success criteria

- three-lane model is explicitly documented
- verification-as-discipline wording is explicit
- persistent lane worktree + rebase-before-thread rule is documented
- `docs/architecture.md` section ownership is documented
- canonical evidence-bundle template is documented
- brief-freeze rule with substantive-pushback exception is documented
- zero-line diff for `src/`

## Downstream consequences

- future brief drafting and dispatch becomes less chat-dependent
- lane workers can operate with less coordinator re-explanation
- acceptance evidence becomes more uniform across threads
- if this lands cleanly, the benchmark/eval sidecar can start under a clearer operating model

## Initial dispatch sequence

- this brief is one of the first three landing under the lane plan; expected order:
  1. **Lane B** (seam K phase 1) and **Lane C** (docs cleanup post-M9 + api phase 1) dispatch in parallel
  2. **Lane A** (`to_pydantic` materialization) holds until Lane C closes — `to_pydantic` will widen `extractx.__all__` and edit §13, both Lane-C-owned, so dispatching it concurrently with Lane C creates an §13 collision
  3. once Lane C closes its first cycle and Lane A picks up `to_pydantic`, Lane B may continue with the next seam-K thread under the same rules
