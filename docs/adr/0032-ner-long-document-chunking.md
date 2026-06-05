# ADR-0032: NER Long-Document Chunking

**Status:** Accepted
**Date:** 2026-05-18

## Context

`NerCandidateStrategy` runs spaCy over `DocumentView.normalized_text`. spaCy's
default `nlp.max_length` is 1,000,000 characters and raises `ValueError`
`[E088]` for longer text. large documents can exceed that limit. A raw spaCy
runtime exception at seam C prevents extractx from producing candidates and
leaks an implementation limit to consumers.

## Decision

`NerCandidateStrategy` owns long-document handling. It processes oversized text
with deterministic chunking by default instead of requiring consumers to
truncate or pre-split documents.

`NerStrategyParams` includes:

```python
max_chars_per_chunk: int = 250_000
chunk_overlap_chars: int = 2_000
oversize_policy: Literal["chunk", "fail"] = "chunk"
```

When `oversize_policy="chunk"`, the strategy splits `normalized_text` into
overlapping chunks, runs spaCy per chunk, translates entity offsets back to the
original document coordinate space, and emits ordinary `CandidateSet` objects.

When `oversize_policy="fail"` and the document exceeds spaCy's configured
`max_length`, the strategy raises typed
`InfrastructureError("ner.document_too_long: ...")`.

## Invariants

- Chunking must preserve canonical source spans against the original
  `DocumentView`.
- `candidate_id` derivation remains based on strategy id, source span, and
  normalized structural payload.
- Overlap duplicates are deduplicated by `candidate_id`, never by normalized
  value.
- Raw spaCy `E088` must not escape the strategy.
- Empty candidate output remains an empty `CandidateSet`, not an exception.

## Consequences

Long documents become deterministic candidate-generation inputs. Chunk overlap
does some duplicate work, but preserves boundary-adjacent entities without
making consumers aware of spaCy's document limit.
