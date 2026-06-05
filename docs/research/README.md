# Research

Investigations that inform decisions. One file per investigation. Findings are raw inputs to ADRs and tasks — they are not themselves decisions.

## Convention

- **Filename:** `<slug>.md`, matching the originating task brief slug (`docs/tasks/<slug>.md` → `docs/research/<slug>.md`).
- **Template:** [`0000-template.md`](0000-template.md).
- **Structure:** Bottom line first, then per-investigation-area findings, then implications, then recommendation, then explicit gaps.

## What goes here

- library or ecosystem evaluations (e.g., "which llm client library should we wrap for the default Selector")
- prior-art surveys (e.g., "how do existing schema-first extraction frameworks handle multi-instance grouping")
- behavioral probes against real libraries or services (e.g., "does `pydantic-ai` expose `raw_usage` untranslated so our `UsageEvent` passthrough invariant holds")
- any investigation whose output is a finding, not code

## What does not go here

- implementation notes — those live with the code or in commits
- decisions — those live in `docs/adr/`
- open TODOs — those become tasks, not floating notes

## Lifecycle

1. A decision point or gap surfaces (in conversation, in a task, in a code review).
2. Coordinator drafts a `docs/tasks/<slug>.md` brief pointing at this directory for the output.
3. Exec agent investigates and writes `docs/research/<slug>.md`.
4. Coordinator reviews, and either:
   - converts the finding into an ADR (if it commits the architecture),
   - converts the finding into a new task (if it unblocks implementation),
   - or shelves it (documented gap, not ready to decide).
5. The research doc stays as historical context — it is not deleted when the decision is made.

## Relationship to ADRs

Research produces **findings**; ADRs produce **decisions**. Every ADR should link to the research it's built on (if any). Not all research produces an ADR — some is background context for a judgment call.

## Index

Listed in the order added. Continuation docs link back to their predecessor.

_(no research yet — this list grows as investigations are completed)_
