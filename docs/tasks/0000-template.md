# Task: [Short title — what the agent should determine]

*Optional one-line framing: where this task sits relative to other work, or which ADR/decision it unblocks.*

## Read first

List of docs the exec agent must read before starting. Include the research docs it builds on, the ADRs it might affect, and any strategic docs whose context matters. The exec agent starts cold — this list is load-bearing.

- `docs/research/...md`
- `docs/adr/NNNN-...md`
- `docs/...md`

## Goal

One or two sentences. What decision or information gap does this research close? What does "done" look like in the most compressed form?

## Scope

Numbered investigation areas. Each a specific question or subject the agent should address. Be concrete about what you want — "map X's terms of service" beats "research X."

### 1. [Investigation area]

- Specific sub-questions
- Verify/check this specific claim
- Name sources when possible (lab names, providers, jurisdictions)

### 2. [Investigation area]

- ...

### 3. Recommendation

Always include a "recommendation" section in scope. The agent should synthesize findings into a clear go / investigate-further / pass, with per-branch next steps.

## Guardrails

What the agent should **not** do. Common items:

- No sending of outreach to real people (human-sent only, per `execution-machine.md`)
- No partnerships, contracts, or account signups
- No legal advice — flag where a lawyer hour is needed
- Spend cap (e.g., "$100 total research tooling")
- No commitments on behalf of LabGrid
- Desk research vs. direct inquiry — specify which is allowed

## Deliverable

Where the research output goes. Almost always `docs/research/<matching-slug>.md`. Specify the structure if it should differ from the research template.

## Success criteria

Checklist-shaped. Each item should be testable: either the output answers it or it doesn't.

- Specific claim verified or debunked with citations
- At least N items mapped per category
- Per-question findings with sources
- Clear recommendation with downstream next steps
- Gaps and open questions named explicitly

## Downstream consequences

What depends on this research? Which ADRs does it inform or block? Which strategic docs might need revision based on findings? Helps the agent understand why the research matters and where to focus rigor.

- ADR-NNNN: depends on [this finding]
- `docs/...md`: section X may need revision if [this finding]
