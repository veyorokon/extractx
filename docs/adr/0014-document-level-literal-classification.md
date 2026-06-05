# ADR-0014: Document-Level Literal Classification

## Status

Accepted.

## Context

A consumer may need document-level classification before full field extraction:

```python
verdict: Annotated[
    Literal["invoice", "receipt", "irrelevant"],
    ValueKind.CATEGORY,
] = extract_field(...)
```

This is not spatial evidence extraction. The bounded candidate set is the
`Literal[...]` arms declared by the schema. The LLM classifies the whole
document by choosing one or more bounded ids; it does not author values.

extractx already supports the surrounding machinery: schema-first specs,
bounded `CandidateSet`s, selector output as `Observation`, deterministic
validation, replay, and pydantic materialization. The missing path is a
candidate strategy and prompt for schema-derived category candidates.

## Decision

Add `ValueKind.CATEGORY` for Literal-set classification fields.

Schema inference preserves string `Literal[...]` arms on `FieldSpec` as
`literal_values`. For `ValueKind.CATEGORY` fields with literal values and no
explicit strategy binding, `from_pydantic` installs
`LiteralSetCandidateStrategy`.

`LiteralSetCandidateStrategy` emits one structured `Candidate` per literal arm:

```python
Candidate(
    text=<literal>,
    source_kind="structured",
    source_id="literal_set",
    source_span=SourceSpan(
        text_anchor_space="normalized_text",
        byte_start=0,
        byte_end=0,
    ),
    normalized_hint=<literal>,
    structured_payload={"literal": <literal>},
    structural_status=StructuralStatus(
        passed=True,
        contract_id="literal_set_strategy_v1",
    ),
)
```

The zero-length normalized-text span is a synthetic document-head anchor. It
preserves the `Candidate.source_span` invariant without pretending the literal
label appears in the document text.

The generic `DeterministicSelectionGate` remains unchanged:

- 0 passing structured candidates: selector/no-candidates path
- 1 passing structured candidate: deterministic auto-selection
- N passing structured candidates: selector over the bounded set

A one-arm `Literal["x"]` is therefore a schema constant and auto-selects.
There is no source-specific carveout for literal sets.

Add `ClassificationPrompt` as a separate concrete prompt implementation under
the existing `Prompt` protocol. It renders whole-document context and bounded
literal candidates. The selector and selector-binding contracts do not change.

Widen the pydantic-ai selector DTO to accept `selected_candidate_ids` so
`list[Literal[...]]` fields can use the same selector path for multi-select
classification.

## Consequences

Consumers can run document triage and field extraction through the same
`extract(document, Schema)` machinery. The only difference is schema shape:
`Literal[...] + ValueKind.CATEGORY` routes to document classification, while
spatial scalar fields continue to route through regex/NER/clause/table
candidate sources.

The `LiteralSetCandidateStrategy` is schema-derived structured generation. It
does not introduce a new source kind and does not bypass validation. The
selected literal still crosses seam F and is validated against the pydantic
field annotation.

## Alternatives Rejected

- **`mode="classification"` on `extract_field`.** The annotation and
  cardinality already encode the mode. A parallel declaration would create a
  second source of truth.
- **Optional or absent candidate spans.** This would weaken the existing
  candidate contract. Synthetic document-head spans keep the contract intact.
- **Branching inside `SelectionPrompt`.** Classification asks a different
  question and needs whole-document context. A separate prompt implementation
  keeps the prompt contract explicit.
