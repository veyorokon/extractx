# ADR-0024: Readable Bounded-ID Selector Prompts

**Status:** Accepted
**Date:** 2026-05-05

## Context

ADR-0008 made selector output observation-shaped: soft compute chooses bounded
candidate ids and never authors values, normalized values, spans, or domain
identity. ADR-0023 added the batch selector sibling so one soft-compute call can
choose candidate ids for many fields.

The first batch prompt body serialized the selector input as one minified JSON
object. That kept the machine contract intact, but made the human and model
surface noisy: candidate ids appeared once in a large `allowed_evidence_ids`
array and again inside candidate objects, field boundaries were hard to scan,
and prompt forensics required mentally parsing transport scaffolding instead of
the selection task.

## Decision

Selector prompt bodies should be readable bounded-id menus, not raw transport
payload dumps.

Both single-field and batch selectors should render the same presentation
doctrine:

- instructions first;
- schema / field context second;
- one explicit field block per field;
- one explicit candidate block per candidate;
- each selectable candidate id appears next to the candidate text and context
  it names;
- the model is told to return only candidate ids, never values or spans.

The hard bounded-id contract remains in structured output schema, metadata, and
runtime enforcement. Prompt readability is a presentation contract for the
soft-compute seam, not a replacement for deterministic validation.

Prompt bodies should preserve candidate boundaries. extractx should not render
one undifferentiated joined text blob as the primary selector prompt because
that blurs which text is selectable evidence and makes duplicate or overlapping
occurrences harder to reason about. Joined text may appear inside a candidate
block only when deterministic compaction has already established that the block
represents one selectable candidate.

## Prompt Shape

Batch selector prompt bodies use this broad shape:

```text
<task>
Choose the candidate_id that best answers each field. Do not write values.
</task>

<schema>
version: ...
description:
...
</schema>

<allowed_instance_ids>
inst_0
</allowed_instance_ids>

<fields>
<field id="total_due">
description: ...
value_kind: MONEY
cardinality: optional
python_type: decimal.Decimal
<candidates>
<candidate id="...">
text: approximately $15.24
entity_type: MONEY
source_kind: text
source_id: ner:...
<context>
... total due of approximately $15.24 in total ...
</context>
</candidate>
</candidates>
</field>
</fields>
```

The single-field selector should use the same field/candidate block vocabulary,
with one `<field>` block instead of a `<fields>` collection.

## Prompt-Local IDs

Canonical `Candidate.candidate_id` values remain the durable ids in replay,
metadata, scoring, and downstream provenance. They may be long content hashes.
Those hashes are poor LLM ergonomics, especially when hundreds of candidates
are present.

Selectors may render short prompt-local ids, for example:

```text
<candidate id="c017">
text: approximately $15.24
...
</candidate>
```

In that shape, the provider returns the prompt-local id (`c017`) and extractx
maps it back to the canonical `Candidate.candidate_id` before constructing the
canonical `Observation`.

If prompt-local ids are used:

- they are bounded to one rendered prompt;
- they must be deterministic for a given rendered candidate order;
- non-batch ids must be unique within the single field prompt;
- batch ids must be unique across the whole batch prompt and should carry a
  compact field prefix, for example `f003_c017`;
- the local-to-canonical id map must be recorded in prompt metadata;
- runtime enforcement must validate provider output against the prompt-local
  enum, then translate to canonical ids before crossing the selector seam;
- replay and scoring must continue to see only canonical candidate ids.

Batch prompts must expose prompt-local ids only on candidate blocks, not as
alternate field ids. The model should see one field identifier: the canonical
`field_id` it must return. The compact candidate prefix remains an internal
presentation handle carried in prompt metadata.

```text
<field id="invoice_date">
...
<candidate id="f003_c017">
text: November 26, 2024
</candidate>
```

This avoids field-local id collisions such as `c017` meaning different
canonical candidates under different fields in the same batch prompt.

## Candidate Compaction

Prompt rendering should be allowed to compact candidate presentation
deterministically before calling the provider. This addresses repeated or
overlapping spans without changing the selector contract.

The string compaction shape interns repeated context strings without merging
canonical candidates:

```text
<contexts>
<context id="ctx001">
... prospectus supplement dated March 24, 2023 ...
</context>
</contexts>

<candidate id="c041">
text: March 24, 2023
context_id: ctx001
</candidate>
```

The span-aware shape is stronger. When candidate context windows carry source
coordinates in the same anchor space, overlapping windows are merged into one
context window and candidate anchors are marked inline:

```text
<contexts>
<context id="ctx001" source_span="1000:1600">
A. ...
B. ...
C. ... <cand id="c001">March 24, 2023</cand> ...
D. ...
E. ... <cand id="c002">December 5, 2023</cand> ...
F. ...
</context>
</contexts>

<candidates>
<candidate id="c001">
text: March 24, 2023
context_id: ctx001
local_span: 285:299
</candidate>

<candidate id="c002">
text: December 5, 2023
context_id: ctx001
local_span: 430:446
</candidate>
</candidates>
```

Compaction is presentation-only unless a later ADR promotes a specific
candidate merge policy into candidate generation. In this ADR:

- canonical candidate sets remain unchanged;
- compaction may intern exact duplicate contexts, equivalent normalized
  contexts, or overlapping context windows for display;
- every selectable prompt-local id must still map to one canonical candidate id;
- if multiple canonical candidates are visually grouped, the group must make the
  selected canonical id unambiguous before provider output crosses the selector
  seam;
- compaction must be deterministic and inspectable through prompt metadata.

The implementation prefers simple deterministic wins first: collapse duplicate
contexts for the same canonical candidate and shorten displayed ids. Span-aware
compaction then merges context windows by interval:

1. collect each candidate context interval `(start, end)`;
2. sort by `(start, end)`;
3. merge intervals that overlap;
4. render the merged source slice once;
5. insert inline `<cand id="...">...</cand>` anchors at each selected
   candidate span;
6. render the candidate menu with `context_id` and `local_span`.

Small-gap merging is intentionally not part of the current contract because
prompt rendering only receives candidate windows, not the full normalized source
text needed to fill arbitrary gaps honestly.

## Contract

The prompt body may omit duplicated enforcement scaffolding when that
information is already present in the structured output schema or metadata. In
particular:

- prompt text should not include a separate `allowed_evidence_ids` array when
  each candidate block already carries its candidate id;
- the structured output schema still constrains `field_id`, `instance_id`, and
  candidate ids;
- selector contract enforcement still rejects fabricated ids after provider
  output;
- replay and scoring continue to consume canonical `Observation` objects, not
  prompt text.

Prompt capture records the rendered prompt at the selector seam for forensics.
Captured prompts are derived diagnostic artifacts, not canonical extraction
truth.

## Consequences

- Prompt forensics becomes direct: operators can inspect the same field and
  candidate menu the model saw.
- Batch and non-batch selectors share one mental model.
- Token count may decrease by removing duplicated id arrays, but readability is
  the primary reason for the decision.
- Prompt-local ids improve model ergonomics, but introduce an adapter step that
  must be tested as part of the selector contract.
- Candidate compaction can reduce repeated context bloat, but must remain
  deterministic and must not create a second source of candidate truth.
- Inline candidate anchors make merged contexts easier for models and humans to
  inspect, but require accurate local spans inside the merged context window.
- The prompt body remains an LLM-facing surface, so deterministic enforcement
  must continue to live outside the prompt.
- Tests should assert prompt-shape invariants so future changes do not regress
  to raw minified transport payloads.

## Implementation phases

- **Phase 1 — Batch prompt body:** render batch selector prompts as readable
  field/candidate blocks; keep structured output schema and runtime enforcement
  unchanged.
- **Phase 2 — Single-field prompt body:** update `SelectionPrompt` to use the
  same field/candidate block vocabulary for non-batch selectors.
- **Phase 3 — Prompt-local ids:** render short bounded ids in prompt bodies,
  validate provider output against those ids, and map them back to canonical
  candidate ids before constructing observations.
- **Phase 4 — String context interning:** add deterministic presentation
  compaction for duplicate or substring-equivalent candidate contexts, with
  prompt metadata proving the local-to-canonical and candidate-to-context
  mapping.
- **Phase 5 — Span-aware context windows:** carry context-window source spans
  into prompt rendering, merge overlapping intervals, render merged context
  windows once, mark candidate anchors inline, and keep the candidate menu
  independently selectable by prompt-local id.

## Alternatives considered

- **Minified JSON payload:** rejected as the primary prompt body. It is easy for
  code to produce but hard for people to inspect and unnecessarily exposes
  transport shape to the model.
- **Pretty-printed JSON:** better than minified JSON, but still centers
  transport structure instead of the selection task. It also preserves the
  duplicated `allowed_evidence_ids` list.
- **One joined text blob per field:** rejected as the primary prompt body. It
  hides candidate boundaries and makes it unclear which exact id the model
  should select. Candidate blocks may contain joined or repeated context only
  after deterministic compaction has established the selectable boundary.
- **Free-form prose candidates:** rejected. Candidate blocks must remain
  structured enough that candidate ids, text, and context stay visually and
  semantically attached.

## Related

- [ADR-0008: Observation-Shaped LLM Extraction](0008-observation-shaped-llm-extraction.md)
- [ADR-0023: Batch Selector Observations](0023-batch-selector-observations.md)
