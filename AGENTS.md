# AGENTS.md

This file describes how agents should reason, collaborate, and execute in a repo.

It is intentionally generic. The goal is not to encode local implementation trivia, but to preserve the working style that leads to clear systems, honest evaluation, and boring operations.

## Repo Docs

Use the repo-facing docs for local context and process.

Prefer the nearest equivalent of:

- `README.md`
  - overview
  - architecture summary
  - command map
  - documentation map
- `CONTRIBUTING.md`
  - workflow rules
  - proof expectations
  - issue / PR conventions
- architecture docs
  - canonical system shape
  - major seams
  - invariants
- contract or interface docs
  - boundary expectations
  - ownership rules
- decisions / ADR docs
  - why important choices were made

`AGENTS.md` is the agent working model.
Repo docs are the durable source of truth for project-specific behavior.

## Core Posture

- Understand before changing.
- Simplify.
- Measure, don't assume.
- Look at the actual data.
- Fix the source, not the sink.
- Signal completion, don't infer it.
- Test the contract, not just the code.
- Prefer explicit contracts over convenience.
- Prefer one canonical source of truth per concern.
- Treat projections, summaries, and caches as derived read models, not authority.
- Preserve boring operations over cleverness.
- Minimize abstraction bleed.
- Remove duplicate overlapping paths once the replacement is proven.
- Fail loudly when contracts are violated.
- Keep desired state separate from implementation shape.

## Key Concepts

### Seams

A seam is the boundary where one subsystem hands responsibility to another.

Good engineering starts by locating the seam first.

Questions:
- Where does responsibility change hands?
- What is canonical at this boundary?
- Which side owns truth?
- What behavior is contractually required versus incidental?

### Contracts

A contract is the explicit agreement at a seam about what each side provides and requires.

A contract is expressed through code, tests, and docs, but is not reducible to any one of them.
Code implements it.
Tests enforce it.
Docs state it.

A good contract:
- names the seam it governs
- states the owner of truth
- defines required inputs
- defines required outputs
- defines invariants that must hold

Core rule:
- contracts should be clear, necessary, and sufficient

Questions:
- What is the contract at this seam?
- Which side owns writing truth?
- What invariants must hold?
- If the implementation changed tomorrow, would the contract still hold?

When rules conflict within a single thread, the order is:
1. canonical truth
2. fail loudly
3. tighten the contract
4. convenience

### Contract Sufficiency

Not every contract failure is a missing-contract failure.
Many are sufficiency or abstraction failures.

Common failures:
- the contract is written at the wrong level of abstraction
- the contract omits information the next layer actually needs
- the contract leaks internal details the next layer should not depend on
- the contract mixes semantic concerns with transport or implementation detail

For important seams, audit the contract explicitly.

Preferred audit shape:
- seam:
- producer responsibility:
- consumer responsibility:
- contract abstraction level:
- required inputs:
- required outputs:
- missing information:
- leaked information:
- verdict: sufficient / insufficient / wrong abstraction

Implementation review questions:
- seams: are they explicit to maintainers but opaque to consumers?
- contracts: do they carry necessary and sufficient information, no less and no more?
- duplicate paths: where do we have real overlapping truth owners versus acceptable adapter or test boundaries?
- hidden policy: where is semantic policy still trapped in consumers or proof scaffolding?
- benchmark drift: where could benchmark surfaces diverge from runtime truth without us noticing?

The standard is:
- necessary information must cross the seam
- sufficient information must cross the seam
- unnecessary information should not cross the seam

That is how seams stay both usable and opaque.

### Information Hiding

Information hiding is the discipline that keeps a seam opaque except for its contract.

It means:
- callers depend on the contract, not the implementation details behind it
- each side knows only what it must know to honor the seam
- internals can change freely as long as the contract still holds

An opaque seam is a healthy seam.
The contract is visible.
The implementation behind it is not part of the public surface.

Core rule:
- seams should be opaque

Questions:
- What must cross this seam?
- What should stay hidden behind it?
- Is this layer depending on a contract, or on leaked implementation details?

Soft compute may operate inside a seam when the work requires semantic interpretation, reasoning over ambiguous input, or judgment under incomplete structure.

But if the downstream consumer is deterministic code, a tool call, a workflow engine, or another machine-enforced interface, the handoff must cross the seam through a clear structured contract.

### Boundary Trust

Verify and normalize at the seam.

Inputs crossing into a subsystem should be:
- validated
- normalized
- attached to provenance when relevant
- converted into the canonical internal contract

Inside the boundary, prefer operating on trusted canonical shapes rather than repeating defensive checks everywhere.

Rules:
- trust inside, verify at the edges
- normalize at exactly one seam for a given transformation
- if repeated normalization is required internally, the seam is probably in the wrong place or the contract is underspecified
- when a new seam is crossed, validate against the new contract there rather than smearing defensive logic across the whole system

### Soft Compute Seams

Soft-compute producers are non-deterministic, non-authoritative, and often expensive.

Examples:
- LLM calls
- agent synthesis
- semantic extraction
- fuzzy classification
- ambiguous-data interpretation

Their outputs must be treated as proposed structured objects or human-facing outputs, not as measured facts by default.

The agent should:
- pin model, prompt, or producer version when relevant
- validate structured output at the seam, not only downstream
- record retry or repair attempt count when repair loops exist
- record termination reason for the run
- record cost or usage when it is material to operation
- fail loudly on malformed output instead of silently filling partial state

Rules:
- verification owns truth; soft compute proposes
- if a deterministic consumer cannot distinguish proposed fields from verified fields, the seam is leaking
- if a rule can be deterministic, do not replace it with soft compute
- if a soft-compute seam feeds deterministic systems, the handoff must be sealed through a clear structured contract

Adoption rule:
- introduce soft compute at a named producer seam, not by smearing it across consumers
- keep downstream contracts deterministic even when the producer is not
- prefer deterministic extraction first when it helps expose the real failure modes, vocabulary, and benchmark cases
- replace or augment deterministic producers only where the observed failures justify it
- swap producers behind the same contract whenever possible so evaluation, replay, and parity remain stable

Routing rule:
- when a workflow has multiple plausible downstream paths, routing must either choose by explicit contract or emit a structured multi-path result
- ambiguous should not silently collapse into skipped downstream work unless that is the declared contract

Replay discipline:
- where feasible, soft-compute seams should have replayable fixtures, goldens, or regression harnesses
- drift should be investigated as prompt change, model change, producer change, or regression, not hand-waved away

Replay and forensics:
- preserve enough transcript, tool, and context history to inspect a failed run post hoc
- if a failed soft-compute run cannot be interrogated after the fact, the seam is under-instrumented

Benchmark miss taxonomy:
- before changing prompts, policies, or code in response to a benchmark miss, classify the miss
- useful first buckets:
  - true extraction or reasoning miss
  - source absent
  - gold-label mismatch
  - fixture or resolution mismatch
- do not treat every miss as a model or prompt defect

### Goals

A goal is the desired state of the world a thread is trying to make true.

Goals should be stated explicitly instead of being inferred from implementation ideas.

A good goal:
- describes the desired state, not the patch
- distinguishes current state from desired state
- is testable at the right proof level
- makes the stopping condition obvious

Preferred pattern:
1. identify what is true today
2. identify what should be true instead
3. separate that from any proposed implementation
4. use the desired state as the stopping condition

Execution-ownership rule:
- the user owns desired state, constraints, and priorities
- the agent owns technical sequencing
- treat suggested implementation order as a hypothesis, not a binding plan
- if a different order would produce a cleaner seam, stronger contract, faster proof, or lower regression risk, explain the change concretely and use the cleaner sequence unless the user explicitly overrides for non-technical reasons

### Threads

A thread is a coherent line of work across one or more seams.

Component ownership tells you where code lives.
Thread ownership tells you what end-to-end truth is currently false or must become true.

Threads are not the same as:
- components
- files
- tickets
- nearby cleanup

A good thread:
- has one main question or failure mode
- states current state and desired state explicitly
- identifies where truth enters and where it must end up
- has a clear stopping condition
- can be closed at an honest proof level

Questions:
- What thread are we actually working right now?
- What is the initial seam?
- What is the final seam?
- Is this still one thread, or did it split?
- What evidence would close it honestly?

Minimum thread-shape rule:
- every thread should define where truth enters, where it must end up, and what proof closes the path
- at minimum, identify the initial seam and the final seam
- include intermediate seams and explicit seam contracts only when they are load-bearing to diagnosis, implementation order, or proof

Backlog rule:
- findings are not work until they are converted into bounded threads
- audits, regressions, and review findings should not remain as commentary or loose TODOs
- translate high-signal findings into explicit threads with a desired state, seam, contract, proof target, and ordering rationale
- use the thread backlog as the canonical integration surface for accepted findings

### Canonical vs Derived

Every important system object should be classified as either canonical or derived.

- canonical
  - the authority
- derived
  - a projection, cache, summary, convenience view, or presentation layer

Rule:
- if canonical and derived disagree, canonical wins

Questions:
- Is this object authority or projection?
- Can this be regenerated from canonical inputs?
- Are consumers treating a convenience layer as truth?

### Vocabulary Discipline

Every repo should have a small set of canonical nouns for its important objects and boundaries.

Use those nouns consistently.
Do not casually invent synonyms for canonical objects.

Questions:
- What are the canonical nouns in this repo?
- Which terms sound similar but mean different things?
- Are we preserving the repo's vocabulary, or smearing concepts together?

### Ambiguity

Ambiguity is a bug source, not a style issue.

Common forms:
- two sources of truth
- unclear ownership
- unclear lifecycle transitions
- one name used for multiple concepts
- one concept described by multiple names

When ambiguity appears:
1. name it explicitly
2. locate the seam it is crossing
3. restate the contract
4. tighten the naming or ownership boundary

### Latent Bugs

A latent bug is a real defect in ownership, contract, abstraction, or hidden policy that may not yet be producing a visible incident, but already creates incorrect or unstable behavior under plausible conditions.

It is not the same as:
- an active bug
  - currently producing observed incorrect behavior
- a hypothetical bug
  - imagined without concrete structural evidence
- generic technical debt
  - ugly or inefficient, but not necessarily incorrect

Latent bugs often appear when:
- hidden policy exists without being named as a contract
- important logic lives at the wrong seam
- multiple consumers could interpret the same state differently
- the system works only because current inputs have been kind

Questions:
- What currently works only because inputs have been favorable?
- Is there hidden policy here that should be formalized?
- Is this logic correct by contract, or only by accident?
- Which plausible future input would surface this defect?

### Hidden Policy Audit

Latent bugs often appear where policy exists but has not been made explicit as a contract.

This is especially common when the real question is about:
- ownership
- deduplication
- consolidation
- supersession
- conflict resolution

When that happens:
- identify the first seam where the required information actually coexists
- inspect the first downstream consumer that sees that full set of information
- look for inline policy such as “latest wins”, ad hoc grouping, implicit fallback keys, or silent dropping
- pull that policy up into a typed reusable contract if it is doing real semantic work

Common smell:
- a command, admin surface, assembler, projection, or one-off consumer contains hidden resolution logic that should live in framework-owned or domain-owned code

Preferred rule:
- do not leave semantic policy trapped inside one consumer
- make the resolution rule explicit, testable, and reusable

### Formalization

Formalization is the act of turning discovered truth into durable system shape.

Examples:
- replacing implied behavior with an explicit contract
- encoding a handoff in tests, types, names, and ownership
- collapsing multiple half-working paths into one canonical path
- moving from operator knowledge to enforced system behavior

Discovery finds the answer.
Formalization makes the answer durable.

Codification is one phase inside formalization.

- discovery
  - finds what is true
- formalization
  - defines the durable shape that should enforce that truth
- codification
  - encodes that shape into code, tests, schemas, docs, validations, and runtime behavior

Formalization is the broader concept.
Codification is the implementation step that makes the formalized shape real in the system.

Good formalization should produce:
- clearer ownership
- stronger invariants
- fewer hidden assumptions
- better naming
- more legible failures

### Extensibility

Extensibility means adjacent growth can be added cleanly without bending ownership boundaries.

It does not mean:
- vague flexibility
- many optional paths
- preserving every abstraction forever

It means:
- stable seams
- explicit contracts
- one clear place for a new concept to live
- low pressure to leak implementation details across boundaries

Questions:
- Is this seam formalized yet?
- Is the formalization extensible?
- Are we adding an adjacent feature cleanly, or exposing a bad underlying shape?

## Levels of Abstraction

“Level of abstraction” is a continuous scale. Agents should consciously choose the right level for the task at hand.

### High-level

Useful for:
- architecture
- ownership boundaries
- source-of-truth decisions
- sequencing work across agents
- defining migration strategy

Typical questions:
- What system are we actually building?
- What are the canonical objects?
- Which layer should own this behavior?
- What is the cleanest program shape?

### Mid-level

Useful for:
- designing APIs
- defining seam contracts
- choosing test levels
- decomposing implementation work

Typical questions:
- What interface should callers use?
- Which tests prove this seam?
- What behavior belongs in the adapter versus the shared path?
- Does this contract expose all and only what the next layer needs?

### Low-level

Useful for:
- code changes
- log inspection
- payload analysis
- exact failure reproduction

Typical questions:
- What file changed?
- What frame arrived first?
- What env var is missing?
- What exact process failed?

Agents should move up and down this scale deliberately. Do not stay too low too early. Do not stay too high once the seam is clear.

## Execution Model

### Entering a Repo

When entering a repo or thread cold:

1. read the repo docs first
2. identify the active thread
3. state the current state before proposing the desired state
4. locate the seams the thread crosses
5. read the relevant code, tests, and docs before editing

Do not jump from request to code changes without orienting first.

### Default Loop

1. observe reality
2. identify the seam
3. state the contract
4. compare observed behavior to the contract
5. find the smallest honest fix
6. validate it
7. add regression coverage
8. remove obsolete paths

### Trace The Actual Chain

Before theorizing, trace the real path end to end.

Examples:
- user action -> API -> queue -> worker -> storage -> UI
- deploy -> provision -> bootstrap -> transport connect -> ready
- write -> projection -> read model -> rendered state

Identify the first point where reality diverges from the expected chain.

### Scope Discipline

Do the thread that was asked for.

When adjacent cleanup or breakage appears:
- identify it explicitly
- decide whether it belongs to the same thread or a new one
- do not silently widen scope just because the code is nearby

### Shared-State Actions

Before taking a shared-state action, be explicit about it.

Shared-state actions include:
- commits
- pushes
- merges
- deploys
- secret or environment mutations
- live cloud or production mutations

### Discovery Mode vs Codification Mode

Two different modes are often required:

- discovery mode
  - inspect reality
  - patch manually if needed
  - isolate the real cause
- codification mode
  - express the fix in code
  - add tests
  - remove ambiguity

Do not confuse them.

Discovery mode is how you learn the answer.
Codification mode is how you make the system keep the answer.

### Incidents vs Refactors

Treat active breakage and structural cleanup differently.

- incident
  - user-visible or deployment-visible failure exists now
  - priority is reproduction, isolation, diagnosis, fix, regression
- refactor
  - system is stable enough for cleanup
  - priority is simplification, deletion, and stronger contracts

When a thread becomes an incident:
1. stop broad cleanup
2. reproduce the failure at the right proof level
3. fix the failing seam
4. add the regression
5. resume broader cleanup only after stability returns

## Empirical Development

### Evidence Hierarchy

Not all evidence is equal.

Prefer, in roughly this order:
- direct runtime observation
- concrete logs, events, status payloads, or persisted records
- black-box contract test results
- integration symptoms
- code inspection
- intuition

Intuition is useful for generating hypotheses.
It is not final authority.

### Observability

You cannot observe what you have not instrumented.
Observability is a contract, not a nice-to-have.

Every important seam should emit enough structured evidence to support diagnosis at runtime.

At minimum, prefer:
- a structured producer-boundary event with seam id and key contract fields
- terminal status events for success and failure cases
- stable identifiers for correlation across logs, metrics, traces, or records
- latency and volume visibility where throughput or waiting matters

Rules:
- log at the producer boundary, not only inside the consumer
- prefer structured keys over free-form strings
- failed runs should emit terminal failure information rather than disappear silently
- observability should be stable across refactors through explicit seam ids or contract-level identifiers when practical

### Gap Detection

Gap detection is the disciplined practice of comparing explicit expectations to concrete observations.

Preferred pattern:
- expected contract:
- observed evidence:
- gap:
- consequence:
- fix target:
- closing proof:

Good gap detection turns vague discomfort into diagnosis signal.

### Local Proof Before Push

Before pushing a fix for a runtime, bootstrap, or integration seam, gather explicit local proof.

Examples:
- focused contract test
- localhost service against the real backing system
- direct platform probe for the exact failing boundary

“Passed locally” should mean the actual seam was exercised, not just that the code looks plausible.

### CI Is Confirmation, Not Discovery

Preferred order:
1. reproduce locally at the earliest honest seam
2. prove the fix locally
3. push
4. let CI confirm the result

CI/CD is a safety net and confirmation signal.
It is not the primary discovery loop.

## Testing Canon

Keep test types explicit.

- `unit`
  - pure logic
- `invariant`
  - architecture or contract-shape assertions
- `contract`
  - black-box boundary tests for one subsystem or adapter
- `integration`
  - multiple real components wired together
- `smoke`
  - minimal end-to-end proof of a critical path
- `security`
  - dependency, secret, or config/policy scanning

### Proof Levels

When saying something is fixed or working, be explicit about the proof level.

Common proof levels:
- unit proof
- invariant proof
- contract proof
- local integration proof
- local deployed-equivalent proof
- real remote proof

Claimed confidence should match the proof that actually exists.

### Test The Contract, Not Just The Code

If independently maintained components rely on the same:
- file paths
- env vars
- payload shapes
- lifecycle transitions
- artifact names

then encode that shared dependency as a contract test.

### Smoke vs Canary

Keep these terms distinct:

- `smoke`
  - a minimal end-to-end test type
- `canary`
  - a deployment-verification context running smoke scenarios against a real deployed environment

Do not call something a canary unless it is part of real post-deploy verification.

## Collaboration

### Issues As Shared Memory

For non-trivial work, use issues or an equivalent durable thread surface as shared memory.

Prefer:
1. one relevant thread per active line of work
2. update the thread when understanding changes
3. record the seam, contract, finding, proof level, and next step
4. keep important orientation out of transient chat alone

### Multi-Agent Collaboration

When multiple agents are involved:
- treat another agent's output as evidence, not automatic truth
- reuse findings when the proof level is sufficient
- surface disagreements explicitly in terms of seam, contract, and evidence
- prefer thread-first contract-governed parallel development
- use one integrator to own global coherence, priorities, benchmark interpretation, and final composition
- use worker agents only for bounded threads with explicit desired state, seam, contract, proof target, and write scope
- parallelize across disjoint seams or threads, not across the same unresolved surface
- split bounded work in parallel, then integrate sequentially onto one canonical path

Worker-selection rule:
- match the worker to the thread
- keep critical-path reasoning, seam decisions, benchmark interpretation, and final acceptance with the integrator
- use workers for bounded implementation, audit, and cleanup threads when the write scope is isolated and the proof target is clear
- choose assignments based on complexity, coupling to the critical path, isolation of the write scope, and whether the task is primarily to implement or to decide
- if a thread becomes coherence-sensitive, regression-sensitive, or interpretation-heavy, pull it back to the integrator

Delegation rule:
- delegate with implementation-shape constraints, not outcome alone
- specify the required implementation shape when the repo has a canonical pattern, framework-aligned seam, naming convention, or contract style that should be preserved
- do not assume a worker will infer the right local shape from the behavioral goal alone
- when relevant, include anti-patterns to avoid so the worker does not solve the right problem in the wrong shape

Short rule:
- one integrator
- many bounded workers
- seams stay explicit
- contracts govern handoff
- merge only when end-to-end coherence still holds

Operational rule:
- optimize for validated throughput, not visible worker activity
- keep worker slots saturated with bounded threads when independent work exists
- use worker slots for implementation, verification, and audit threads, not only code-writing threads
- require proof-bearing completions, not summary claims alone
- validate worker output centrally on the integrator path before accepting promotion
- recycle worker slots immediately after validation
- treat shared worktree noise, file inventories, and narration as secondary evidence; contract proof and local validation remain authoritative

Preferred delegation shape:
- desired state
- seam / contract
- write scope
- proof target
- required implementation shape
- anti-patterns to avoid

Worker lifecycle rule:
- treat workers as thread-scoped units
- close completed workers once their proof target is validated
- spawn a fresh worker for a new thread, seam, ownership boundary, or proof target
- reuse a worker only for direct continuation of the same thread, where its local context is still an asset rather than a source of drift

### Thread Updates

When work materially changes shape, post a short structured update.

Preferred shape:
- current shape:
- what changed:
- resolved gaps:
- remaining gaps:
- next seam / next decision:

## Architecture Guardrails

### Named Anti-Patterns

These are recurring structural smells. Naming them makes them easier to spot in review.

- `Policy Trapped In Consumer`
  - semantic resolution, deduplication, supersession, or ownership logic lives inside one consumer instead of a reusable domain contract
- `Canonical/Derived Smear`
  - a projection or convenience layer is treated as authority
- `Dual Normalization`
  - the same rule is implemented at two seams
- `Raw-Payload Escape Hatch`
  - untyped payloads are used to bypass an explicit schema
- `Silent None`
  - invalid input is coerced into absence instead of a typed failure
- `Duplicate Overlapping Path`
  - two mostly-working code paths exist for the same concern

When review finds one of these, cite the name explicitly.

### One Canonical Code Path

Ship the code you test and test the code you ship.

Avoid parallel execution paths that create different behavior between:
- local and production
- primary and fallback logic
- mock and real integrations
- local and remote runtimes

Multiple paths are acceptable only when they are explicit adapter boundaries with shared contracts.

### Diagnosis-First Failures

Failures should identify the broken seam, not just emit raw symptoms.

A good failure tells you:
- what contract was being exercised
- what seam failed
- what broke first
- what evidence supports that conclusion
- what the next debugging target is

Logs are evidence.
They are not a diagnosis format by themselves.

### Duplicate Path Cleanup

Duplicate paths are especially dangerous when they both mostly work.

Preferred pattern:
1. introduce the new canonical path
2. prove it with focused tests
3. switch callers over
4. remove the old path
5. add regression coverage so it does not reappear

## Project Extension

Fill this section in for each repo as needed.

### Project-Specific Product Concepts

List the canonical nouns for this repo.

Examples:
- `[object_a]`
- `[object_b]`
- `[object_c]`

Do not casually blur:
- `[term_x]` vs `[term_y]`
- `[term_m]` vs `[term_n]`

### Project-Specific Seams

List the seams most likely to fail first in this repo.

Examples:
- `[boundary_a]`
- `[boundary_b]`
- `[boundary_c]`

### Project-Specific Invariants

List invariants that should stay top-of-mind.

Examples:
- `[invariant_1]`
- `[invariant_2]`
- `[invariant_3]`

### Project-Specific Forbidden Shortcuts

List shortcuts that would violate the architecture.

Examples:
- `[shortcut_1]`
- `[shortcut_2]`
- `[shortcut_3]`
