# [Research title]

*Working doc. Investigation as of YYYY-MM-DD. [Optional one-sentence framing: "follow-up to X" / "landscape pass on Y" / "implementation viability of Z"]*

## Bottom line

**One paragraph. The most important finding stated plainly — go / no-go / qualified yes / conditions / open question — followed by the one or two specific decisions or ADRs it forces.**

Anyone reading only this section should be able to decide what to do next. Don't bury the lead.

## [First concrete finding — table or prose]

| Column | Column | Column | Determination |
|---|---|---|---|
| Item | ... | ... | ✅ / ❌ / pending |

Tabular findings when the research spans multiple items (libraries, providers, seams, impl approaches). Include a "determination" column so the table itself carries the verdict. Prose per item below the table for anything that doesn't fit.

## [Investigation area 1 — named after scope item from the brief]

Findings per sub-question. Cite sources inline — URLs, document references, library versions, code snippets. Don't summarize when the raw fact is short; give the reader the raw fact.

**Key insights:**
- Bold call-outs for load-bearing findings
- Things that change downstream decisions or seam contracts

## [Investigation area 2]

...

## [Investigation area N — implications]

Concrete edits this research suggests for specific docs. Not "this might affect X" — "change line Y in X from A to B." The research is only valuable if it's actionable.

- **Edit to `docs/architecture.md`:** [specific change]
- **Edit to `CODEX.md`:** [specific change]
- **New ADR needed:** [topic]
- **Existing ADR needs revisit:** [ADR-NNNN, why]
- **New task to queue:** [slug and brief]

## Recommendation

- **Go:** if [conditions], do [specific next actions]
- **Investigate further:** if [conditions], follow up with [specific brief]
- **Pass:** if [conditions], document why and set aside

Each branch should be specific enough that the next action is obvious from the finding.

## What this doc does not cover (explicit gaps)

Honest list of what wasn't investigated in this pass. Keeps future research efficient (don't redo this) and keeps the claims in the doc honest (don't overreach beyond what was actually checked).

- [Topic]: not covered because [reason]
- [Topic]: partially covered; [what's missing]
- [Library / provider / seam]: not individually verified
