# ADR-0007: Storage shape — authority model and minimum skeleton

**Status:** Accepted
**Date:** 2026-04-23

## Context

extractx is a source-unit-scoped, run-oriented extraction engine. Its canonical runtime object is `ExtractionResult`; its replay artifact is `ReplayArtifact` (architecturally "bytewise-reversible under pinned producers" per `docs/architecture.md` §7 seam H); its sibling artifact for soft-compute forensics is `InterviewTranscript` (opt-in per ADR-0002 + ADR-0004).

As implementation reaches the first vertical execution slice (M8) and approaches the replay/forensics milestone (M9), a storage design question arises: how should persisted outputs be organized? Three common temptations conflict with the existing architecture:

1. **Partition canonical storage by domain** (`domain_alpha/`, `domain_beta/`, `images/`). Convenient for humans; makes domain boundaries authoritative.
2. **Partition canonical storage by seam/stage** (`runs/<run>/A/B/C/D/E/F/G/`). Mirrors execution internals directly in persistence.
3. **Warehouse-first** — final rows become authority; provenance and replay become secondary.

Each conflicts with a commitment already in the architecture:

- Domain is classification/metadata — not stable, not disjoint, not load-bearing for reconstruction. Making it a canonical partition couples durable storage to a taxonomy that will evolve.
- Seam/stage folders mirror transient implementation structure and calcify it into filesystem shape. Principle 11 ("audit trail ≠ runtime bus") and the §7 seam H design both place intermediate seam detail inside `ReplayArtifact`, not at the top level.
- Warehouse-first inverts §7 seam H's replay-under-pinning promise: `ReplayArtifact` is canonical authority for reconstruction; replacing it with derived rows undoes principle 13's canonical/derived discipline.

We need a storage shape that preserves provenance and replay, keeps domain boundaries as metadata, and stays backend-agnostic. The goal is to decide the authority boundaries now so M9's replay/storage work has a clean contract to land against — **no storage code in this ADR; implementation is deferred to M9**.

## Decision

### 1. Authority model

**Canonical (authoritative):**

- **`source`** — the raw source unit as provided to `extract(...)` or `run_extraction(...)`. Indisputably canonical; nothing else can reconstruct it. Identified by `SourceRef.content_hash`.
- **`spec`** — the `ExtractionSpec` used by a run, including its `version`. Canonical because replay needs the same spec that produced the run. Identified by `spec.version` (content hash per §7 seam B).
- **`replay`** — the `ReplayArtifact` for a run. Canonical **under the architecture's replay-under-pinning promise** (§7 seam H): given the captured producer-version pins for the producers that ran, replay reconstructs `ExtractionResult` under the supported replay equality contract.

**Cached / materialized projection (not authority):**

- **`result`** — a persisted `ExtractionResult` snapshot, if present. Fast-access cache; replay wins under drift. Consumers who read `result` accept that its freshness depends on the producer versions still being reachable. Consumers who need guaranteed-current result state replay.

**Sibling artifact (canonical only when captured, independent retention):**

- **`interview`** — `InterviewTranscript` artifacts per ADR-0002 + ADR-0004. Captured only when `ExecutorPolicy.capture_interview_transcripts=True`. Never embedded in `ReplayArtifact` (anti-pattern `Transcripts-In-Default-Replay-Artifact`). Lives with independent retention and access policy: interview carries prompt content which may include sensitive source excerpts; it must be separately deletable and separately access-controlled without corrupting replay.

**Derived / observability (not persisted as authority):**

- `ExecutionTrace` — executor-owned run trace. external OTEL-style reporting remains `Reporter`/collector-owned storage.
- `UsageEvent`s — embedded in `ReplayArtifact` per §7 seam H; no separate authority.
- `views` — projections over manifests for human consumption or evaluation workflows.

### 2. Minimum logical skeleton

The storage shape is **a logical mental model**, not a filesystem commitment. A backend (local filesystem, object store, content-addressable store, database-backed) implements the shape; this ADR constrains authority boundaries and minimum object kinds, not physical layout.

Logical default shape:

```
data/
  objects/
    source/<content-hash>.bin
    source/<content-hash>.meta.json
    spec/<spec-version>.json
    replay/<artifact-id>.msgpack
    result/<artifact-id>.json        # cached projection, optional
    interview/<artifact-id>.json     # opt-in, independent retention
  runs/
    <run-id>.json
  views/                             # optional, derived, may be unpersisted
```

Three top-level surfaces:

- **`objects/`** — content-addressed immutable storage for canonical, cached, and sibling objects. Subdivided by object kind. Each kind carries its own retention/access policy (§3 below).
- **`runs/`** — the operational manifest/index surface. One manifest per execution attempt. Human-readable run identity; the attachment point for metadata and tags; the index views build against.
- **`views/`** — derived projections over runs and objects. Always rebuildable from authority; never itself authority. **May remain unpersisted** until a concrete consumer justifies paying for the freshness machinery.

### 3. Retention and access policy per object kind

Different object kinds carry different retention and access requirements. The storage layout **must** allow per-kind policy; it must not collapse all `objects/*` into a single retention tier.

- `source` — permanent while any run references it; deletable when all referencing runs are deleted (and subject to user data-retention rules).
- `spec` — permanent; small and deduplicated.
- `replay` — permanent while the corresponding run is retained; deletion tied to run retention.
- `result` — cache; deletable and regenerable; retention independent of replay.
- `interview` — **separately deletable without corrupting replay**. Access control may be stricter than the rest of the store. Replay must function correctly in the absence of the sibling interview artifact.

This per-kind policy is the direct operationalization of ADR-0004's anti-pattern `Transcripts-In-Default-Replay-Artifact`.

### 4. Run manifest shape

`runs/<run-id>.json` is the operational record of one execution attempt. At minimum it references:

- `source_ref` — `SourceRef` or object id pointing at the stored source blob
- `spec_version` — pointer to the stored spec object
- `runtime_bindings_summary` — which capability impls were bound (for replay pinning)
- `policy` — `ExecutorPolicy` summary or hash
- `producer_versions` — pinned selector / planner / resolver / other `producer_version`s the run emitted
- `strategy` — `IndependentStrategy` | `IterativeStrategy`
- `outcome` — run-level outcome (`complete` | `partial` | `failed`)
- `replay_ref` — pointer to the stored `ReplayArtifact`
- `result_ref` — pointer to the cached `ExtractionResult` snapshot, if persisted
- `interview_ref` — pointer(s) to sibling `InterviewTranscript` artifact(s), if captured
- `tags` — metadata dimensions (`domain`, `dataset`, `source_kind`, `benchmark_split`, etc.). Free-form; not authoritative for reconstruction.

Manifests are the index surface. Views are built by scanning or querying them.

### 5. Run identity — two tokens, different roles

- **`run_id`** — unique per execution attempt. Fresh every time a persisting executor run is invoked through `extract(...)` or `run_extraction(...)`. Guarantees "this specific execution has a stable reference" regardless of idempotency.
- **`run_fingerprint`** — deterministic hash of `(source_ref, spec.version, producer_versions, policy_hash, strategy_id, runtime_bindings_summary)`. Identical fingerprint → identical run shape; useful for equivalence queries, dedup, and replay-is-redundant checks.

**Emit both.** `run_id` is the durable reference (fresh per execution); `run_fingerprint` is a derived equivalence token stored alongside. Two executions with the same fingerprint are architecturally replayable to the same `ExtractionResult`; consumers who want dedup can key on fingerprint.

`replay_artifact_ref` is stricter: it is the content hash of the serialized
replay artifact bytes. It is the right forensic reference to a specific artifact,
not a semantic idempotency key. Semantically equivalent runs may have different
artifact refs when artifact bytes differ because of operational metadata.

This mirrors the existing `spec.version` (content hash) vs Python schema class (identity) split: one is deterministic equivalence, the other is execution-time identity.

### 6. Domain as metadata, not canonical partition

Domain (`domain_alpha`, `domain_beta`, `images`, etc.) is a **tag on `runs/<run-id>.json`**, not a top-level partition. In order of weight:

- Domains are not stable: they rename, merge, and split as the product matures.
- Domains are not disjoint: a single source unit can serve multiple benchmarks or multiple downstream systems.
- Domains are not load-bearing for reconstruction: replay does not need to know the domain.
- Domain-as-partition couples durable storage to a taxonomy that evolves faster than storage.

A `views/by-domain/` projection (if persisted) or an equivalent query API covers the human-facing "show me domain_alpha runs" case without promoting domain to authority.

### 7. Stage/seam structure is not top-level

Intermediate seam detail (`CandidateSet`s, `Selection`s, `ProposedField`s, `ValidatedField`s, `UsageEvent`s, `InstanceState` versions) belongs inside `ReplayArtifact` per §7 seam H, not as top-level storage layout. Making seams top-level partitions would:

- Mirror execution internals into persistence (principle 11: audit trail is not runtime bus)
- Calcify transient implementation structure — a seam re-phase would ripple into filesystem reorganization
- Duplicate the design that `ReplayArtifact` already serves cleanly

### 8. Backend-agnostic framing

The skeleton above is a **logical mental model**. A filesystem-backed store implements it literally; an object-store-backed store (S3, GCS) flattens the directory structure into prefix strings; a database-backed store maps `runs/` to a table and `objects/` to content-addressed blob storage. This ADR commits to the authority model and the minimum object kinds, not to "it must be directories."

The phase-1 `ExtractxStore` protocol captures the minimum logical contract with `put_object(kind, id, bytes)`, `get_object(kind, id) -> bytes`, `put_manifest(run_id, manifest)`, `get_manifest(run_id) -> bytes`, and deterministic `list_run_ids()`. Its first backend, `LocalFilesystemStore`, implements the logical shape literally. Additional backend implementations are not this ADR's concern.

## Consequences

**Upside**

- Clear canonical/derived split: `source` + `spec` + `replay` are authority; `result` is cache; `interview` is sibling. No warehouse-first failure mode.
- Domain changes don't ripple into storage layout; taxonomy evolves freely.
- Seam refactors don't churn persistence; `ReplayArtifact` absorbs intermediate shape.
- Interview retention stays separable from replay retention (ADR-0004 discipline preserved).
- `run_id` vs `run_fingerprint` makes both use cases (execution reference / equivalence check) first-class without overloading one token.
- Backend-agnostic; doesn't lock extractx into a filesystem.
- Minimum skeleton is genuinely minimal — four object kinds + manifests.

**Tradeoff**

- Consumers reading `result` without checking for drift may silently load stale projections after producer version drift. Mitigation: each `result` artifact carries the `producer_version`s it was generated under; consumers that care about freshness compare against the current runtime's producers.
- `ReplayArtifact` canonicality depends on the architecture's replay-under-pinning promise actually holding. If that promise degrades (producer version drift beyond replayability, fixture rot), nothing is authority for reconstruction. Mitigation: a frozen producer-version registry can be added as another `objects/` kind if this becomes a real problem; not landing in this ADR.
- Views being optional means early tools have no "latest-successful" index to read without implementing their own manifest scan. Acceptable early; revisit when a real tool complains.
- `run_fingerprint` requires stable hashing of `runtime_bindings_summary`, which is not yet defined. This ADR assumes the execution-substrate thread (M8) will produce a stable summary. If that summary is unstable, fingerprint equivalence degrades until the summary tightens.
- Four object kinds is a surface the backend implementation must carry. A simpler "one bucket of content-addressed bytes" would be smaller but would not express per-kind retention policy.

## Alternatives considered

- **Partition canonical storage by domain at top level.** Rejected on the grounds above: taxonomy is not stable/disjoint/load-bearing. Domain belongs in `runs/<run-id>.json` tags.
- **Partition canonical storage by seam/stage at top level.** Rejected: calcifies transient internal structure. Intermediate seam detail lives inside `ReplayArtifact`.
- **Warehouse-first** — final rows become authority. Rejected: inverts §7 seam H's replay promise and principle 13's canonical/derived split. Loses provenance reconstruction.
- **Single-kind `objects/` without type subdirs.** Viable (matches Git's `.git/objects/` pattern) but per-kind retention/access policy becomes harder to express. Type subdirs (`objects/<kind>/`) buy clarity without losing content-addressability.
- **`ExtractionResult` as canonical, `ReplayArtifact` as debug aide.** Rejected. Architecture §7 seam H commits to replay-as-reconstruction-authority. Choosing otherwise would require rewriting seam H.
- **Omit `spec` as a stored object** (inline the spec into every run manifest). Rejected on dedup and size grounds: one spec against 100 sources should not be stored 100 times. Content-addressed spec storage is strictly smaller and clearer.
- **Persist `views/` in all cases.** Rejected: views are optional and rebuildable. Persisting them by default invites staleness. Land them only when a consumer justifies the freshness machinery.
- **Single `run_id` that is deterministic (content-hashed).** Rejected in favor of dual tokens: deterministic-only loses the "this specific execution attempt" reference when two attempts would dedup. Dual tokens give both guarantees.
- **Single `run_id` that is purely fresh (never deterministic).** Rejected: loses dedup and equivalence query cleanly. Dual tokens preserve both.

## Landed implementation

M9 operationalized the accepted storage shape on the supported path:

1. `ReplayArtifactWriter` persists canonical `ReplayArtifact` bytes.
2. `ExtractxStore` defines the backend-agnostic phase-1 storage seam.
3. `LocalFilesystemStore` implements `objects/source`, `objects/spec`, `objects/replay`, and `runs/`.
4. `RunManifest.from_artifact(...)` derives manifest overlap from the artifact at write time, including `run_id` and `run_fingerprint`.
5. `SpecSummary` is the persisted spec object for phase 1; live `ExtractionSpec` rehydration is source-driven through the registered pydantic schema class.

The result cache, interview sibling artifacts, persisted `views/`, additional backends, and per-kind retention/access enforcement remain deferred follow-on work.

## Related

- [ADR-0001](0001-pass-through-operational-metadata.md) — pass-through discipline for operational metadata; applies to `source.meta.json` parser-metadata passthrough
- [ADR-0002](0002-pydantic-ai-default-selector-and-interview.md) — interview capture mechanism; this ADR preserves its sibling-artifact separation
- [ADR-0004](0004-narrow-interview-scope-to-field-seams.md) — interview capture scope; storage respects the narrowed surface
- `docs/architecture.md` §7 seam H — `ReplayArtifactWriter` contract
- `docs/architecture.md` §7 seam B — `ExtractionSpec.version` composition
- `docs/architecture.md` §9 canonical objects — `SourceRef`, `ExtractionResult`, `ReplayArtifact`, `InterviewTranscript`, `ExecutionTrace`
- `docs/architecture.md` §15 anti-patterns — `Canonical/Derived Smear`, `Transcripts-In-Default-Replay-Artifact`
- `docs/architecture.md` §2 first principles — principle 11 ("audit trail ≠ runtime bus"), principle 13 (canonical vs derived classification), principle 21 (operational metadata passthrough)
