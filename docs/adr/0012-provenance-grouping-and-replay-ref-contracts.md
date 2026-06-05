# ADR-0012: Stabilize provenance, grouping diagnostics, and replay ref semantics

**Status:** Accepted
**Date:** 2026-04-30

## Context

Consumer systems need a stable way to trace sealed extractx evidence into their
own provenance trails. They also need to inspect why extractx separated one
extraction instance from another without treating grouping diagnostics as
business identity.

`Extraction.replay_artifact_ref` is also easy to overinterpret. It is useful as
a forensic artifact reference, but it is not the same thing as semantic
extraction equivalence.

## Decision

Promote `ProposalProvenance` to the stable v1 evidence provenance contract:

```python
class ProposalProvenance(BaseModel):
    strategy_id: str
    candidate_id_refs: tuple[str, ...] = ()
    selector_producer_version: str | None = None
    grounded_producer_version: str | None = None
```

Add typed grouping diagnostics:

```python
class GroupingDiscriminator(BaseModel):
    field_id: str
    candidate_id_refs: tuple[str, ...] = ()
    authority: Literal[
        "boundary_defining",
        "source_anchor_continuity",
        "candidate_cooccurrence",
        "instance_plan_prior",
    ]
```

`GroupingEvidence.discriminators` exposes the fields and candidate ids that
participated in grouping or separating an instance. This is diagnostic. Domain
identity remains consumer-owned and should be derived from sealed `Evidence`
values, not from `GroupingEvidence`.

Clarify replay identity:

- `replay_artifact_ref` is a content hash of serialized replay artifact bytes.
- same bytes imply the same ref.
- semantically equivalent runs may still have different refs if artifact bytes
  include different operational metadata.
- `RunManifest.run_fingerprint` is the deterministic run-equivalence token.

## Consequences

Consumers can chain provenance through extractx without reaching into replay
artifacts for basic producer identity.

Consumers can inspect grouping diagnostics without parsing opaque
`clustering_signals`.

Consumers that need forensic exactness may store `replay_artifact_ref`.
Consumers that need equivalence or dedup should use `run_fingerprint` or a
purpose-built downstream hash over stable fields.

## Related

- [`0007-storage-shape-authority-and-minimum-skeleton.md`](0007-storage-shape-authority-and-minimum-skeleton.md)
- [`0008-observation-shaped-llm-extraction.md`](0008-observation-shaped-llm-extraction.md)
- [`0010-instance-candidate-strategy-seam.md`](0010-instance-candidate-strategy-seam.md)
