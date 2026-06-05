# Thread Orchestration

This document describes a repo-specific operating model for research-heavy or ambiguity-heavy codebases.

Use it when the work benefits from:

- one decisive critical path
- parallel bounded worker threads
- continuous audit and verification sidecars
- strong benchmark and transcript interpretation

Files are implementation surfaces.
Threads are units of work.

## Core Model

Operate in threads, not files.

A thread is one desired-state or failure-mode question crossing one or more seams.

Component ownership tells you where code lives.
Thread ownership tells you what end-to-end truth is currently false, which seams it crosses, and what proof would make it true.

Examples:

- gold-label honesty
- benchmark fan-out through the queue seam
- document field contract
- announcement-date extraction gap
- transcript/debug surface

Each thread should be stated in terms of the contract it is trying to make true.

Minimum doctrine:

- every thread should define where truth enters, where it must end up, and what proof closes the path
- at minimum, identify the initial seam and the final seam
- include intermediate seams and explicit seam contracts only when they are load-bearing

## Component Ownership vs Thread Ownership

Component-owned work sounds like:

- patch the queue module
- update market state
- clean up the admin surface

That is file- or module-centered.

Thread-owned work sounds like:

- producer ownership is not enforced
- point-in-time correctness leaks future information
- pricing and closing facts are smeared together
- the agent cannot reliably find settlement terms

That usually crosses:

- schema
- domain logic
- runtime adapters
- persistence
- tests
- docs
- benchmarks and evaluation

Component ownership asks:

- where does this code live?

Thread ownership asks:

- what end-to-end truth is false?
- where does it first go wrong?
- what seam owns the fix?

That is why thread ownership is the stronger organizing principle for ambiguity-heavy and seam-heavy systems.

## Thread Types

Classify threads early.

- `truth/data`
  - asks what is actually true in source material, artifacts, or runtime state
- `contract/seam`
  - asks what boundary must hold and what the canonical handoff should be
- `implementation`
  - makes an already-understood contract true in code
- `observability/debug`
  - makes failures diagnosable and replayable
- `benchmark/evaluation`
  - measures whether behavior is actually improving

This prevents solving:

- a data problem with prompt changes
- a prompt problem with fixture edits
- a contract problem with local patching at the sink

## Thread Record Shape

Use the light shape by default:

- desired state
- current state
- initial seam
- final seam
- proof target

Add only when they are load-bearing:

- intermediate seams
- seam contracts
- criticality
- write scope
- owner
- blockers
- ordering rationale

If a thread cannot be described even in the light shape, it is probably still too vague.

## Main Agent Role

Put the main agent at the orchestration layer.

The main agent owns:

- thread selection
- thread ordering
- seam decisions
- benchmark interpretation
- transcript and forensic interpretation
- global contract integrity
- final acceptance
- integration of concurrent work

The main agent should edit directly when:

- the fix is small and clearly local
- concurrent work must be integrated
- ambiguity must be resolved centrally
- benchmark or transcript evidence must be interpreted as part of the fix

## Worker Role

Workers are bounded executors, not replacement orchestrators.

Good worker tasks:

- fix benchmark fan-out using the existing queue seam
- trace and fix an announcement-date extraction gap
- implement a bounded schema split with explicit ownership
- add observability spans on a declared surface

Bad worker tasks:

- improve extraction
- audit the codebase
- clean this area up
- make benchmarks better

Every worker should get:

- one narrow goal
- one clear thread
- likely file ownership
- explicit seam and contract
- proof expectations
- notice that they are not alone in the codebase

## Delegation Rule

Delegate by seam and write ownership, not by topic vagueness.

Good delegation has:

- isolated or mostly isolated write scope
- clear desired state
- explicit proof target
- minimal need for integrator-level interpretation

Keep work on the main agent when it is:

- critical-path reasoning
- coherence-sensitive
- regression-sensitive across multiple seams
- benchmark-interpretation-heavy
- fundamentally about deciding, not implementing

## One Critical Path, Parallel Sidecars

Parallelism is useful only when one path stays decisive.

Always ask:

- what is the actual blocker to larger-scale progress?
- which thread changes the go / no-go decision?

The main thread should remain explicit.

In parallel, run sidecar threads for:

- implementation
- verification
- audit
- observability
- benchmark forensics

Parallel sidecars should reduce uncertainty around the critical path, not create multiple competing priorities.

## Lane Model

Use three named lanes when parallel work is active:

- **Lane A — primary product implementation.** This lane owns the decisive product path for the current cycle.
- **Lane B — secondary internal-correctness implementation.** This lane owns replay, trace, determinism, internal contract tightening, and similar correctness work when it can proceed without blocking Lane A.
- **Lane C — sidecar.** This lane owns docs, evaluation, benchmark forensics, and other low-conflict support work.

Verification is a discipline applied to every lane, not a lane by itself. Each lane carries its own proof target and reports a standard evidence bundle when complete.

The coordination model stays one integrator / many bounded workers. The integrator owns lane selection, ordering, final acceptance, cross-lane drift, and conflicts between lane scopes.

## Persistent Lane Worktrees

Lane worktrees are persistent execution surfaces for long-lived lane branches:

- **Lane A:** main checkout on `dev` for now. A persistent Lane A worktree is deferred until parallel demand justifies it.
- **Lane B:** worktree `extractx-replay`, branch `lane/replay`.
- **Lane C:** worktree `extractx-docs`, branch `lane/docs`.

Before starting any thread in a persistent lane worktree, run:

```bash
git fetch origin && git rebase origin/dev
```

Use rebase, not merge. Lane history must stay linear so cherry-pick replays into `dev` produce clean commits.

If the rebase conflicts, the lane stops, surfaces the conflict in chat, and does not proceed against a half-rebased tree.

Worktree-local destructive commands are allowed only when stated as lane-local and intentional. For example, resetting `lane/docs` or `lane/replay` back to `origin/dev` after a failed lane cycle is acceptable when the operator names the lane branch and purpose first. Do not use destructive reset commands on `dev`.

## Architecture Section Ownership

`docs/architecture.md` is globally canonical, but current lane work uses section ownership to avoid collisions.

Current ownership:

- **Lane B owns:** trace / replay-equality half of §7 seam H, §7 seam K trace-semantics wording only, and §9 `ExecutionTrace`.
- **Lane C owns:** storage-wording half of §7 seam H for ADR-0007 promotion alignment only, §10, §13, §15, §17, ADR status, `CODEX.md`, and other docs-hygiene surfaces.
- **Lane A owns:** no `docs/architecture.md` sections in the current operating model unless explicitly granted.

§7 seam H is the only architecture section split between two lanes. Lane B and Lane C treat the other half as out of scope.

If a lane discovers a needed edit outside its owned sections, or across the §7 seam H split boundary, it reports drift instead of editing through the boundary.

## Evidence Bundles

Every worker completion reports the shared evidence-bundle skeleton in [`docs/process/evidence-bundle.md`](process/evidence-bundle.md).

Each lane may add lane-specific checklist items, but the base skeleton is shared. A non-zero failure in the proof gates terminates the bundle; the worker reports the failure instead of presenting the thread as complete.

## Brief Freeze And Pushback

Use one pushback round by default. After that round, the coordinator pins remaining ambiguities and freezes the brief so workers can execute without repeated renegotiation.

Substantive worker pushback re-opens the brief. Substantive means the worker names one of:

- a contract gap
- an ownership collision
- a write-scope contradiction

Cosmetic feedback does not re-open the brief. Cosmetic means naming, ordering, wording, or test-shape preference. Cosmetic feedback is logged in the evidence bundle when useful, but execution continues under the frozen brief.

This is a throughput rule, not a ban on real discovery. A worker who finds a contract gap, ownership collision, or write-scope contradiction should surface it directly.

## Cross-Lane Drift

Worker-reported drift outside the lane write scope is collected in [`docs/process/drift-log.md`](process/drift-log.md).

The integrator or coordinator appends drift items to the log keyed by the originating thread. Lane C cycles consume the log: each Lane C thread picks up open drift items in its scope and closes them by deleting the resolved lines when the relevant doc is updated.

The drift log is not a TODO list for code work. It tracks docs, wording, and status drift only.

If a drift item is actually a contract gap, it is escalated to a new task brief instead of accumulating in the drift log.

## Findings Become Threads

Findings are not work until they are converted into bounded threads.

Audits, regressions, and review findings should not remain as commentary or loose TODOs.

The integrator should:

1. verify the finding has signal
2. classify it by thread type
3. define desired state, seam, contract, and proof target
4. decide whether it belongs on the current critical path or as a sidecar
5. place it in the canonical thread backlog

Use the thread backlog as the canonical integration surface for accepted findings.

## Truth Before Formalization

Formalize only after truth is known.

First:

- inspect source material
- inspect persisted artifacts
- inspect transcripts or traces
- inspect benchmark output
- verify the real behavior

Then:

- tighten schema
- update contract
- patch stage policy
- add regression coverage

Do not encode assumptions into the framework before the truth thread is closed.

## Evidence Hierarchy

Prefer, in roughly this order:

- source truth
  - document text, raw observation, primary artifact
- persisted canonical artifacts
- transcript or replay evidence
- runtime logs and spans
- code inspection
- intuition

This is how you avoid fixing the wrong layer.

## Raw Truth vs Derived State

In research-heavy systems, repeatedly force the distinction between:

- what the source explicitly says
- what the system derives for downstream use
- what the system resolves as the operative state

This prevents semantic smear and overloaded fields.

## Replay, Forensics, And Benchmark Misses

Transcript and replay surfaces are first-class debugging tools.

If a worker or agent run fails:

- preserve enough transcript, tool, and context history to inspect it later
- compare the failed run against known-good runs when possible
- use replay to distinguish prompt, policy, fixture, or source-truth problems

Before changing prompts, policies, or code in response to a benchmark miss, classify the miss:

- true extraction or reasoning miss
- source absent
- gold-label mismatch
- fixture or resolution mismatch

Do not treat every miss as a model defect.

## Honest Closure

Close threads at proof level, not intuition level.

Common proof levels:

- unit
- contract
- integration
- smoke

A thread is closed only when the right proof exists for that thread.

Examples:

- a gold-label honesty thread closes when rescoring proves the mismatch disappears without changing the model
- an extractor thread closes when benchmark rerun plus regression test prove the missing field now lands correctly

## Thread States

Useful default states:

- discovered
- scoped
- active
- blocked
- awaiting proof
- closed
- superseded

This keeps the backlog from turning into a note pile.

## Worker Lifecycle

Treat workers as thread-scoped units.

- new thread -> new worker
- same thread refinement -> reuse is acceptable
- validated completion -> close the worker

Spawn a fresh worker for a new thread, seam, ownership boundary, or proof target.

Reuse a worker only for direct continuation of the same thread, where its local context is still an asset rather than a source of drift.

## Escalation Back To The Integrator

Pull a thread back to the main agent when:

- the scope assumption is no longer true
- benchmark interpretation becomes central to the decision
- regressions now cross multiple seams
- the fix changes canonical vocabulary or lifecycle semantics
- the worker is solving the right problem in the wrong shape

## Short Version

- operate in threads, not files
- one critical path, parallel sidecars
- truth before formalization
- delegate by seam and write ownership
- findings become bounded threads
- close work at honest proof levels
