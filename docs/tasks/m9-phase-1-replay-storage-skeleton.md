# Task: implement M9 phase 1 — replay/storage skeleton

*This is M9 phase 1. After F.layer3 the canonical extraction lifecycle is complete on the supported path: A → C → D → E → F.layer1+2 → G.resolver → F.layer3 → `ExtractionResult`. The missing infrastructure is **persistence and replay**. Keep this thread narrow: a real `ReplayArtifact`, a phase-1 `SpecSummary` for round-trip-safe spec persistence, an executor-owned `ReplayArtifactWriter`, a minimum `ExtractxStore` with one local-filesystem backend, run manifests with `run_id` + `run_fingerprint` (manifest derived from artifact at write time), and a round-trip proof. No interview storage, no domain views, no async executor, no iterative persistence, no richer reporter, no exemplar / acceptance machinery, no second backend, no widening of seam J / `Runtime`.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; storage / replay notes; forbidden shortcuts
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam H in full** (ReplayArtifactWriter contract and invariants; **note the load-bearing storage-seam pin: "persisted replay / source / spec / cached result objects sit behind a backend-agnostic storage seam; logical refs are canonical, backend paths are adapter internals"**), **§7 seam J** (capability list — `LLM`, `NLP`, `Fetch`, `Budget`, `Reporter`; storage is **not** a seam-J capability and does not widen it in this thread), **§9 canonical objects** for `ReplayArtifact`, `ExtractionResult`, `SourceRef`, `UsageEvent`, **§10 three-tier public surface** (replay format is internal; `ReplayArtifact` is plugin-public; `ReplayArtifactWriter` impl is internal), **§11 execution model** (executor is the only writer of run-level state), **§13 public api surface** (no widening of `run_extraction(...)` signature; **benchmark/evaluation discipline pins reuse of `run_extraction(...)` — no parallel pipelines**), **§15 anti-patterns** (`Canonical/Derived Smear`, `Transcripts-In-Default-Replay-Artifact`, `Reshape-Operational-Metadata`, **`Benchmark-Only Execution Path`**, **`Flattened Benchmark Miss`**), **§16 project layout**, **§17 proof table entries for seam H**
- [`docs/adr/0007-storage-shape-authority-and-minimum-skeleton.md`](../adr/0007-storage-shape-authority-and-minimum-skeleton.md) — **load-bearing**. authority model (`source`, `spec`, `replay` canonical; `result` cache; `interview` sibling), minimum logical skeleton (`objects/` + `runs/` + optional `views/`), per-kind retention/access, dual run identity (`run_id` + `run_fingerprint`), backend-agnostic framing
- [`docs/adr/0001-pass-through-operational-metadata.md`](../adr/0001-pass-through-operational-metadata.md) — `UsageEvent.raw_usage` is unshaped passthrough; phase-1 algorithmic slice emits zero `UsageEvent`s but the artifact carries the field shape honestly
- [`docs/adr/0004-narrow-interview-scope-to-field-seams.md`](../adr/0004-narrow-interview-scope-to-field-seams.md) — interview is sibling, never embedded. phase-1 does **not** capture interview; the artifact has no interview slot to populate
- [`docs/tasks/m8-phase-1-serial-independent-vertical-slice.md`](m8-phase-1-serial-independent-vertical-slice.md) — current executor shape, `_StrategyOutput`, `_assemble_result`. M9 wires storage onto this without changing the M8 path's behavior when storage is unbound
- [`docs/tasks/seam-f-layer3-phase-1-instance-validation.md`](seam-f-layer3-phase-1-instance-validation.md) — final instances reaching `ExtractionResult.instances` are post-layer-3; replay must capture them in that final shape
- [`src/extractx/replay/{artifact,writer,reader}.py`](../../src/extractx/replay) — current empty stub modules; this is the landing site
- [`src/extractx/execution/executor/serial.py`](../../src/extractx/execution/executor/serial.py) — current executor; M9 wires write + manifest emission here. **storage is injected via `SerialExecutor.__init__(storage=...)`, not via `Runtime`.** seam J / `Runtime` does not widen
- [`src/extractx/execution/runtime.py`](../../src/extractx/execution/runtime.py) — **read-only** in this thread. `Runtime` is unchanged

## Goal

implement the replay/storage skeleton so that a successful M8-supported run can be persisted, reloaded, and reconstructed:

- a real canonical `ReplayArtifact` carrying source ref, spec version, intermediates (`CandidateSet`s, `Selection`s, `ValidatedField`s), final post-layer-3 `InstanceResult`s, pre-resolver negatives, usage events, narrow producer-version map, policy summary, and runtime bindings summary
- a phase-1 `SpecSummary` (round-trip-safe canonical type) carrying just the spec fields that genuinely serialize/deserialize without losing identity. `SpecSummary` is the persisted spec object; `ExtractionSpec` itself is not round-trippable in phase 1 because of live `python_type` / binding-class / callable references
- an executor-owned `ReplayArtifactWriter` that produces this artifact for one run
- an `ExtractxStore` protocol + a `LocalFilesystemStore` concrete backend implementing the ADR-0007 minimum skeleton (`objects/source`, `objects/spec`, `objects/replay`, `runs/`)
- run manifests carrying both `run_id` (per-execution-attempt token) and `run_fingerprint` (deterministic equivalence token), **derived from the artifact at write time** so manifest and artifact never drift
- a reader path: `read_replay(store, artifact_id) -> ReplayArtifact` and `reconstruct_extraction_result(artifact, *, artifact_id) -> ExtractionResult`
- proof: a full M8 run, persisted, reloaded, reconstructed, and shown to satisfy three explicitly-named equalities under the supported algorithmic path

without widening `run_extraction(...)` signature, without an async executor, without iterative or interview support, without a second backend, without persisting `result/` or `views/`, without seam re-execution during replay reconstruction, **and without widening seam J / `Runtime`**.

**"done" in one sentence:** an M8-supported `run_extraction(...)` invoked against a `SerialExecutor(storage=LocalFilesystemStore(...))` writes one canonical `ReplayArtifact`, one source blob, one `SpecSummary` blob, and one run manifest under the ADR-0007 layout; reading the artifact and reconstructing `ExtractionResult` yields a value structurally equal to the run's emitted result, the artifact bytes round-trip identically through serialize/deserialize, and the manifest is field-identical to the values already on the artifact.

## Three named equalities (load-bearing, used throughout this brief)

the architecture's "bytewise reconstructs" wording at §7 seam H is operationalized for phase 1 as three explicitly-named equality claims. every proof target below references one of these names; do not conflate them:

1. **artifact-bytes round-trip equality** — `serialize(artifact) → blob1; deserialize(blob1) → artifact2; serialize(artifact2) → blob2`. assert `blob1 == blob2`. requires deterministic serialization output (msgspec defaults; no custom hooks; sorted dict keys via `model_dump(mode="python")` then `msgspec.msgpack.Encoder`).
2. **artifact-structural equality** — `read_replay(store, id) == original_artifact` under pydantic structural equality (`BaseModel.__eq__`).
3. **result-structural equality** — `reconstruct_extraction_result(read_replay(store, id), artifact_id=id) == original_result` under pydantic structural equality.

phase-1 honors the architecture's promise via **(1) + (3)**: artifact bytes round-trip, and reconstructed result is structurally equal to the emitted one. literal-byte-stream equality on a serialized `ExtractionResult` is **not** a phase-1 requirement (`ExtractionResult` is not stored in a single canonical bytestream in phase 1; the result cache is deferred per ADR-0007).

## Scope

numbered implementation areas. do each in order.

### 1. canonical `ReplayArtifact` shape

land `ReplayArtifact` as a frozen pydantic `BaseModel` in `src/extractx/replay/artifact.py`.

requirements:

- canonical fields (load-bearing; do **not** widen this list silently):
  - `schema_version: Literal["v1"]` — artifact format version pin (per architecture §7 seam H invariant: "self-describing")
  - `extractx_version: str` — package version at write time
  - `source_ref: SourceRef`
  - `document_id: str`
  - `spec_version: str` — content-hash identity; `pydantic_schema_hash` is **not** a separate field (architecture's "self-describing" claim is satisfied by `schema_version` + `extractx_version` + `spec_version`)
  - `strategy: Literal["independent", "iterative"]`
  - `outcome: Literal["complete", "partial", "failed"]`
  - `producer_versions: Mapping[str, str]` — narrow phase-1 keys, sourced only from landed class-level versions:
    - `"candidate_strategy"` — `RegexCandidateStrategy.producer_version`
    - `"selector"` — `SingletonSelector.producer_version`
    - `"resolver"` — `DeterministicInstanceResolver.algorithmic_code_hash()`
    - **no** `"planner"` (not invoked in M8), **no** `"validator"` (per-call versions live on `ValidatedField.field_validation_version` already inside the artifact), **no** `"strategy"` / `"executor"` (no class-level versions today; widening is a separate thread)
  - `policy_summary: PolicySummary`
  - `runtime_bindings_summary: str`
  - `candidate_sets: tuple[CandidateSet, ...]` — full per-field, in `spec.fields` declaration order; carried unchanged per ADR-0005
  - `selections: tuple[Selection, ...]` — one per `CandidateSet` consumed (or empty when seam D was not invoked for that field)
  - `validated_fields: tuple[ValidatedField, ...]` — final layer-2 outputs in seam-F call order
  - `pre_resolver_negatives: tuple[NegativeOutcome, ...]` — those produced before `G.resolver`, in field/proposal order
  - `final_instances: tuple[InstanceResult, ...]` — exactly the post-layer-3 instances that flow into `ExtractionResult.instances`
  - `usage_events: tuple[UsageEvent, ...]` — empty in the algorithmic slice; the slot is honest about ADR-0001 passthrough discipline
  - `trace: ExecutionTrace` — same `ExecutionTrace` carried by the emitted result
- frozen, `extra="forbid"`
- model lives in `src/extractx/replay/artifact.py`; export from `src/extractx/replay/__init__.py`
- promote to plugin-public per §10 (already listed in §10 plugin-public table); end-user surface (`extractx.__init__`) does **not** widen in this thread

implementation-shape constraints:

- do **not** embed `InterviewTranscript` (anti-pattern `Transcripts-In-Default-Replay-Artifact`)
- do **not** embed prompt text or raw llm response bodies (architecture §7 seam H invariant); `UsageEvent.raw_usage` rides through unchanged per ADR-0001
- do **not** invent additional intermediate types; if the data needed for reconstruction is not on one of the listed canonical objects today, do not synthesize it in this thread
- do **not** carry `replay_artifact_ref` inside the artifact (would create a cycle with `ExtractionResult.replay_artifact_ref`)
- do **not** carry an embedded `ExtractionResult` — reconstruction composes the result from these listed fields
- do **not** add `pydantic_schema_hash` (would duplicate `spec_version`)

### 2. canonical `SpecSummary` shape

land `SpecSummary` as a frozen pydantic `BaseModel` in `src/extractx/schema/summary.py` (new module). this is the persisted spec object; the live `ExtractionSpec` is not round-trippable in phase 1 because `FieldSpec.python_type`, `StrategyBinding.cls`, `SorterBinding.cls`, `ValidationBinding.normalizer`, and `ValidationBinding.field_validators` carry live class / callable references that do not survive json (or any portable serialization) without a registry.

requirements:

- canonical fields:
  - `summary_version: Literal["v1"]`
  - `spec_version: str` — same content-hash as `ExtractionSpec.version`
  - `source_schema_ref: SchemaRef | None`
  - `prompt_policy: PromptPolicy`
  - `validation_policy: ValidationPolicy`
  - `grouping_policy: GroupingPolicy`
  - `budget: BudgetSpec`
  - `field_summaries: tuple[FieldSummary, ...]` — one per `FieldSpec`, in declaration order
- `FieldSummary` (sibling frozen model in the same module) carries:
  - `field_id: str`
  - `description: str`
  - `value_kind: ValueKind`
  - `cardinality: Cardinality`
  - `priority: int`
  - `depends_on: tuple[str, ...]`
  - `python_type_qualname: str` — `f"{cls.__module__}.{cls.__qualname__}"` for the spec's `python_type`. opaque string; not re-imported in phase 1
  - `strategy_binding_summary: BindingSummary | None` — `kind`, `cls_qualname`, `params: Mapping[str, Any]` (params must be JSON-safe — primitives, `Mapping`, `Sequence` of same; reuse the seam-F layer-1 `_is_json_safe` rule)
  - `validation_binding_summary: ValidationBindingSummary | None` — `normalizer_qualname: str | None`, `field_validator_qualnames: tuple[str, ...]`
  - `grouping_binding_summary: GroupingBindingSummary | None` — `role`, `distance_metric_name`, `distance_metric_params: Mapping[str, Any]`
  - `prompt_binding_summary: PromptBindingSummary | None` — `template_id`, `params`
  - `sorter_binding_summary: BindingSummary | None` — `cls_qualname`, `params`
- a small helper `summarize_spec(spec: ExtractionSpec) -> SpecSummary` lives next to the type
- the helper raises `InfrastructureError` (with prefix `"spec_summary.unsafe_params: ..."`) if any binding's `params` mapping is not JSON-safe — this is a defect surfacing, not a typed negative
- exports go from `src/extractx/schema/summary.py` and `src/extractx/schema/__init__.py`. **`SpecSummary` is internal in phase 1**; do not add to plugin-public or tier-1

implementation-shape constraints:

- `SpecSummary` is the persisted form, not a "lightweight `ExtractionSpec`." it is **not** rehydratable back to a runnable `ExtractionSpec` in phase 1; that requires a class registry that resolves qualnames back to live classes, which is a future thread (seam-replay re-execution)
- do **not** strip fields silently — every `ExtractionSpec` / `FieldSpec` field that is not round-trip-safe gets a deterministic qualname-string surrogate in `SpecSummary` (or its absence is documented)
- do **not** persist live class references via `pickle` — phase 1 does not introduce binary spec serialization with class references
- the round-trip proof for spec is **`summarize_spec(spec) → blob → SpecSummary → blob → bytes-equal`**, not "spec round-trips to a structurally-equal `ExtractionSpec`." that downgrade is honest and load-bearing

### 3. `ExtractxStore` protocol + `LocalFilesystemStore` backend

land both in a new module `src/extractx/storage/`.

`src/extractx/storage/__init__.py`, `src/extractx/storage/protocol.py`, `src/extractx/storage/local.py`.

requirements:

- protocol surface (in `protocol.py`):
  - `put_object(kind: ObjectKind, content_id: str, blob: bytes) -> None`
  - `get_object(kind: ObjectKind, content_id: str) -> bytes`
  - `put_manifest(run_id: str, manifest_blob: bytes) -> None`
  - `get_manifest(run_id: str) -> bytes`
  - `list_run_ids() -> tuple[str, ...]` — deterministic alphanumeric ordering
- `ObjectKind = Literal["source", "spec", "replay"]`. **`"result"` and `"interview"` are not kinds in phase 1.**
- `LocalFilesystemStore` (in `local.py`) implements the protocol over a root path:
  - `objects/source/<content-hash>.bin` (raw bytes)
  - `objects/source/<content-hash>.meta.json` (parser metadata; `{}` in phase 1)
  - `objects/spec/<spec-version>.json` (json-serialized `SpecSummary`, **not** `ExtractionSpec`)
  - `objects/replay/<artifact-id>.msgpack` (msgspec-serialized `ReplayArtifact`)
  - `runs/<run-id>.json` (json-serialized `RunManifest`)
- writes are atomic: write to `<path>.tmp` then `os.replace(...)` to final path. **phase-1 atomicity assumes POSIX semantics within a single filesystem; cross-filesystem and Windows-specific atomicity are out of scope.** raise `InfrastructureError` on the canonical write failures
- writes are idempotent on identical bytes (same content-hash = same bytes); collision with different bytes raises loudly
- reads raise `InfrastructureError` on missing key (do not return `None` — caller must know the key)
- `InfrastructureError` is the **sole** public exception class for storage failures in phase 1; do **not** introduce a `StorageError` sibling. message convention pins the cause via prefixed strings:
  - `"storage.missing_object: ..."` — `get_object` on absent key
  - `"storage.missing_manifest: ..."` — `get_manifest` on absent key
  - `"storage.collision: ..."` — `put_object` with same key but different bytes
  - `"storage.write_failed: ..."` — io / permission failure
  - `"storage.atomic_violation: ..."` — `os.replace` failed in a way the caller can't fix

implementation-shape constraints:

- no second backend (no s3, no gcs, no db). the protocol exists so a future thread can add one without re-shaping callers
- no caching layer
- no listing surface beyond `list_run_ids()` (no glob, no tag filter; views are deferred)
- no `result` or `interview` paths
- no retention/deletion api in phase 1 — the protocol may be extended later
- no per-kind access policy enforcement in phase 1 (the layout supports it; enforcement lands when interview lands)

### 4. canonical `RunManifest` shape, derived from artifact at write time

land in `src/extractx/execution/manifest.py` (new module).

requirements:

- frozen pydantic `BaseModel`
- canonical fields:
  - `manifest_version: Literal["v1"]`
  - `run_id: str` — fresh per execution attempt; uuid4 in phase 1
  - `run_fingerprint: str` — `stable_hash` over the deterministic tuple `(source_ref.content_hash, spec_version, sorted_producer_versions_items, policy_summary_dump, strategy, runtime_bindings_summary)`. the helper `compute_run_fingerprint(artifact: ReplayArtifact) -> str` lives next to `RunManifest`
  - `source_ref: SourceRef`
  - `spec_version: str`
  - `replay_ref: str` — artifact id (content hash of serialized artifact bytes)
  - `result_ref: str | None = None` — phase-1 always `None`; field reserved per ADR-0007 §4
  - `interview_refs: tuple[str, ...] = ()` — phase-1 always empty; field reserved per ADR-0007 §4
  - `runtime_bindings_summary: str`
  - `policy_summary: PolicySummary`
  - `producer_versions: Mapping[str, str]`
  - `strategy: Literal["independent", "iterative"]`
  - `outcome: Literal["complete", "partial", "failed"]`
  - `tags: Mapping[str, str] = Field(default_factory=dict)` — phase-1 default-empty
- the manifest is **derived** from the artifact at write time. provide a classmethod-or-helper `RunManifest.from_artifact(artifact: ReplayArtifact, *, run_id: str, replay_ref: str) -> RunManifest` that copies every overlapping field from the artifact into the manifest. **the manifest must never be assembled from raw run state independently of the artifact** — single source of truth at write time
- `manifest_version="v1"` is fixed; bumping is a future-thread concern
- export only from `src/extractx/execution/__init__.py` (not tier-1)

implementation-shape constraints:

- no domain partition, no benchmark filtering — domain is metadata only per ADR-0007 §6
- no run-id timestamp prefix games; uuid4 keeps execution identity orthogonal to wall clock
- no run-fingerprint shortcuts that drop fields — the full deterministic tuple is documented and proof-tested
- no manual field-by-field manifest construction at the executor's call site; only `RunManifest.from_artifact(...)` is allowed

### 5. `PolicySummary` and `runtime_bindings_summary`

these two scalars live on both `ReplayArtifact` and `RunManifest` so equivalence checks read the same shape from either side. consistency is preserved by §4's "manifest derived from artifact" rule.

requirements:

- `PolicySummary` (frozen pydantic model, in `src/extractx/execution/policy.py` next to `ExecutorPolicy`):
  - `strategy: Literal["independent", "iterative"]`
  - `on_validation_failure: Literal["fail"]` — phase-1 only
  - `capture_interview_transcripts: bool` — phase-1 always `False` (gated upstream)
- `runtime_bindings_summary` is a `stable_hash` over a deterministic tuple describing the bound capabilities. phase-1 algorithmic slice has no soft providers; the summary is `stable_hash(("algorithmic_v1",))` — a constant pin documenting that this run had no LLM/NLP/Fetch capabilities bound. when the soft-compute thread lands, this composition widens

implementation-shape constraints:

- do not synthesize a richer `PolicySummary` than phase-1 needs; widening it later is a coordinator-owned thread
- do not read provider keys / api endpoints into `runtime_bindings_summary` (those are not part of the bindings shape)
- do not compute `runtime_bindings_summary` from live attribute introspection on the `Runtime` object — pin a documented composition function
- `runtime_bindings_summary` must **not** be re-computed at manifest assembly time from a live `Runtime`; copy from the artifact (which captured it at run time)

### 6. `ReplayArtifactWriter` + reader

land in `src/extractx/replay/writer.py` and `src/extractx/replay/reader.py`.

requirements:

- writer:
  - `class ReplayArtifactWriter:` with `serialize(artifact: ReplayArtifact) -> bytes` and `compute_artifact_id(blob: bytes) -> str` methods
  - serialization backend: `msgspec` (the architecture's documented default at §7 seam H). add `msgspec` as a runtime dependency
  - **deterministic encoding pin:** use **msgspec defaults** with **no custom enc/dec hooks**. concrete pattern: `msgspec.msgpack.encode(artifact.model_dump(mode="python"))` for serialize; `ReplayArtifact.model_validate(msgspec.msgpack.decode(blob))` for deserialize. mappings serialize in pydantic's documented stable order; tuples preserve order. no `decimal_format` overrides, no `enc_hook`, no `dec_hook`
  - `compute_artifact_id` is `stable_hash(blob)` (hex digest); same bytes produce same id
  - the writer is **not** a protocol; it is a concrete class. seam-H protocol promotion is a later-thread concern (drift §1)
- reader:
  - `class ReplayArtifactReader:` with `deserialize(blob: bytes) -> ReplayArtifact`
  - reader rejects unknown `schema_version` with `InfrastructureError("replay.unknown_schema_version: ...")`
- both are stateless / pure
- export from `src/extractx/replay/__init__.py`

implementation-shape constraints:

- no compression layer in phase 1 (msgspec output goes straight to disk)
- no streaming / chunked write surface
- no comparison-mode harness — phase-1 lands replay mode only per architecture §7 seam H invariant
- no live-provider rerun / divergence classification
- no `enc_hook` / `dec_hook` arguments to msgspec — defaults only; that is the determinism pin

### 7. executor wiring (`SerialExecutor` opt-in persistence — storage on the executor, not on `Runtime`)

extend `SerialExecutor.__init__(...)` and `SerialExecutor.execute(...)` to optionally persist the run.

requirements:

- add `SerialExecutor.__init__(self, *, storage: ExtractxStore | None = None) -> None`. **storage is an executor-owned infrastructure binding, not a step capability — it does not go on `Runtime` and does not widen seam J's capability list**
- when `storage is None`: behavior is identical to current M8 (no persistence; `ExtractionResult.replay_artifact_ref = ""`)
- when `storage is not None`:
  1. after the executor builds the in-memory `ExtractionResult` per the M8 path, build a `ReplayArtifact` from the gathered run state (intermediates, final instances, producer versions, policy, runtime bindings summary)
  2. serialize the artifact via `ReplayArtifactWriter`; compute `artifact_id`
  3. persist source bytes via `store.put_object("source", source_ref.content_hash, raw_bytes)`
  4. compute `summary = summarize_spec(spec)`; persist via `store.put_object("spec", spec.version, summary_json_bytes)`. **the persisted `objects/spec/<version>.json` is `SpecSummary`, not `ExtractionSpec`**
  5. persist replay via `store.put_object("replay", artifact_id, artifact_bytes)`
  6. build `RunManifest.from_artifact(artifact, run_id=uuid4_str, replay_ref=artifact_id)` (single source of truth from the artifact)
  7. persist manifest via `store.put_manifest(run_id, manifest_json_bytes)`
  8. **rebuild** `ExtractionResult` immutably with `replay_artifact_ref = artifact_id` (replacing the `""` set during in-memory assembly)
  9. return the rebuilt result
- the failure-path semantics (`outcome="failed"`, `instances=()`) still persist a replay artifact + manifest. failed runs are first-class storage citizens
- persistence happens **after** in-memory assembly, never before; if a write raises, the in-memory result is discarded and the exception propagates as `InfrastructureError` (phase-1 escape — no silent fallback)
- `run_extraction(...)` constructs a `SerialExecutor()` without storage by default — so no public-api caller observes persistence behavior unless they construct their own executor with storage. this preserves the M8 default exactly

implementation-shape constraints:

- do **not** widen `run_extraction(...)` signature
- do **not** add a `persist: bool` knob on `ExecutorPolicy`; the storage binding presence is the trigger
- do **not** add `Runtime.storage` (this thread does **not** widen seam J)
- do **not** persist intermediates outside the artifact (the artifact is the only multi-stage container)
- do **not** persist `ExtractionResult` separately as `objects/result/` in phase 1 — result cache is deferred per ADR-0007 §1

### 8. reconstruction path

land in `src/extractx/replay/reader.py` alongside the reader class.

requirements:

- top-level helpers:
  - `read_replay(store: ExtractxStore, artifact_id: str) -> ReplayArtifact`
  - `read_manifest(store: ExtractxStore, run_id: str) -> RunManifest`
  - `read_spec_summary(store: ExtractxStore, spec_version: str) -> SpecSummary`
  - `reconstruct_extraction_result(artifact: ReplayArtifact, *, artifact_id: str) -> ExtractionResult`
- `reconstruct_extraction_result` composes:
  ```
  ExtractionResult(
      document_id=artifact.document_id,
      spec_version=artifact.spec_version,
      outcome=artifact.outcome,
      strategy=artifact.strategy,
      instances=artifact.final_instances,
      trace=artifact.trace,
      replay_artifact_ref=artifact_id,
  )
  ```
- the caller supplies `artifact_id` (since the artifact intentionally does not carry its own id — content addressing means `id == hash(bytes)`, computed at read time). expose a small helper `compute_artifact_id_from_bytes(blob: bytes) -> str` that mirrors the writer's id computation
- phase-1 reconstruction does **not** re-execute seams. seam-replay re-execution (where intermediates are re-fed through C → D → E → F → G to verify the captured `final_instances` are reproducible under pinning) is deferred to a later M9-extension thread (drift §2)

implementation-shape constraints:

- no LLM / live-provider stubs in this thread
- no msgspec → pydantic shape mismatch handling beyond what `ReplayArtifact.model_validate(...)` already gives
- no graceful degradation on truncated reads — `InfrastructureError` propagates with a `"replay.malformed: ..."` or `"replay.truncated: ..."` prefix

### 9. round-trip proof

add tests under `tests/replay/`, `tests/storage/`, `tests/schema/`, `tests/execution/`, `tests/integration/`.

minimum proof targets, organized by named equality:

**(1) artifact-bytes round-trip equality:**
- `serialize(artifact) → blob1; deserialize(blob1) → artifact2; serialize(artifact2) → blob2`. assert `blob1 == blob2`
- assertion holds for both a `complete`-outcome run and a `partial`-outcome run
- assertion holds for an `outcome="failed"` run (artifact still serializes round-trip stable)

**(2) artifact-structural equality:**
- `read_replay(store, id) == original_artifact` under pydantic structural equality

**(3) result-structural equality:**
- `reconstruct_extraction_result(read_replay(store, id), artifact_id=id) == original_result` under pydantic structural equality
- holds for `complete`, `partial`, and `failed` outcomes

**`SpecSummary` round-trip:**
- `summarize_spec(spec) → s1; serialize → b1; deserialize → s2; serialize → b2`. assert `b1 == b2` and `s1 == s2`
- explicit non-claim: phase-1 does **not** assert `SpecSummary` round-trips to a structurally-equal `ExtractionSpec`. that downgrade is acknowledged in drift §3

**`RunManifest` is derived from artifact:**
- every manifest field that also appears on the artifact has an identical value
- `RunManifest.from_artifact(artifact, run_id=..., replay_ref=...)` is the only manifest-construction site exercised by the executor (white-box: no other manifest constructor call in `serial.py`)

**determinism:**
- identical inputs (`source bytes`, `spec`, `policy`, no soft providers) produce identical `run_fingerprint` across two runs (the `run_id` differs; the fingerprint matches)
- identical inputs produce byte-identical artifact bytes (artifact-bytes equality across two independent serializations of the same run)

**executor wiring:**
- `SerialExecutor()` (no storage) → `ExtractionResult.replay_artifact_ref == ""` (M8 parity); no filesystem writes occur
- `SerialExecutor(storage=LocalFilesystemStore(tmp_path))` → `replay_artifact_ref` is non-empty and matches `compute_artifact_id_from_bytes(replay_blob)` written under `objects/replay/`
- failed runs (`outcome="failed"`, `instances=()`) also persist artifact + manifest
- **all proof tests reach the executor via real `run_extraction(...)` (or a constructed `SerialExecutor.execute(...)` mirroring it)** — no benchmark-only / test-only execution path is introduced, per architecture §15 `Benchmark-Only Execution Path` anti-pattern

**authority boundaries:**
- `objects/source/<source_hash>.bin` exists and round-trips byte-equal to the input bytes
- `objects/spec/<spec_version>.json` round-trips to a structurally-equal `SpecSummary` (not `ExtractionSpec` — see drift §3)
- `objects/replay/<artifact_id>.msgpack` exists with bytes equal to `serialize(artifact)`
- `objects/result/` does **not** exist (phase-1 cache is deferred)
- `objects/interview/` does **not** exist
- `views/` does **not** exist

**no widening of seam J:**
- white-box: import `extractx.execution.runtime`; assert `Runtime` has **no** `storage` field. the runtime surface is unchanged from M8

**reconstruction does not re-execute seams:**
- white-box: monkey-patch (or count-call) the four algorithmic seam classes (`RegexCandidateStrategy`, `SingletonSelector`, `LayeredProposalValidator`, `DeterministicInstanceResolver`) and assert zero invocations during `read_replay` + `reconstruct_extraction_result`

**storage failure-message prefixes:**
- `get_object` on missing key raises `InfrastructureError` whose message starts with `"storage.missing_object: "`
- `put_object` collision raises with prefix `"storage.collision: "`
- `get_manifest` on missing run_id raises with prefix `"storage.missing_manifest: "`

**stub honesty:**
- `ExtractionResult.usage()` and `.interview()` continue to raise `NotImplementedError` (phase-1 storage does not unblock those paths)
  - current implementation note: ADR-0015 later replaced `.usage()` with a captured usage-event projection; `.interview()` remains stubbed
- `InstanceResult.to_pydantic()` continues to raise `NotImplementedError`
- the `ReplayArtifact` model has no method named `to_pydantic` and no method that returns an `ExtractionResult` directly (reconstruction is a top-level helper, not a method on the artifact)

## Explicit drifts to acknowledge in the implementation

surface these in code comments or the final report; do not silently invent around them:

1. **seam-H protocol drift**
   - architecture §7 seam H names `ReplayArtifactWriter` as a contract surface; phase-1 lands it as a concrete class (no `Protocol`-typed indirection). promotion to a writer protocol with multiple backends is deferred
2. **reconstruction-without-seam-replay drift**
   - architecture §7 seam H invariant says replay reconstructs `ExtractionResult` bytewise under pinning. phase-1 reconstructs by composing from the captured executor outputs (intermediates, final instances, trace) — it does **not** re-execute seams from intermediates. true seam-replay re-execution lands in a later M9-extension thread; phase-1 carries the intermediates so that future thread has the data it needs without a second migration. the architecture's "bytewise" wording is operationalized as the three named equalities above (artifact-bytes round-trip + result-structural equality)
3. **`ExtractionSpec` not round-trippable; `SpecSummary` is the persisted form**
   - `FieldSpec.python_type`, binding `cls` references, and binding callable references do not survive json. `objects/spec/` stores `SpecSummary` with qualname-string surrogates, not the live `ExtractionSpec`. the round-trip proof for spec is on `SpecSummary` only. promoting `SpecSummary → ExtractionSpec` rehydration (via a class registry that resolves qualnames to live classes) is a future thread (likely tied to seam-replay re-execution)
4. **single-backend drift**
   - ADR-0007 §8 commits to backend-agnostic framing. phase-1 lands `LocalFilesystemStore` only; the `ExtractxStore` protocol exists so future s3/gcs/db backends drop in without re-shaping callers
5. **runtime-bindings-summary drift**
   - phase-1 algorithmic slice has no soft-compute capabilities; `runtime_bindings_summary` is a constant pin. soft-compute composition is owned by the soft-compute capability thread
6. **interview deferred**
   - executor already gates `capture_interview_transcripts=True` to `InfrastructureError` (M8 brief §2). manifest's `interview_refs` is `()`; replay carries no transcript slot
7. **result cache deferred + future-thread guardrail**
   - manifest's `result_ref` is always `None`; `objects/result/` is not created. consumers requesting cached results get `None` and fall back to replay (the canonical authority per ADR-0007 §1)
   - **future-thread guardrail (load-bearing for the next M-thread):** when a future thread adds `objects/result/` cache, it must **not** re-derive `ExtractionResult` content independently — the cache must be the output of `reconstruct_extraction_result(replay)` serialized to a stable form. otherwise canonical/derived smear surfaces (anti-pattern §15 `Canonical/Derived Smear`)
8. **failed-run persistence**
   - failed runs (`outcome="failed"`, `instances=()`) still persist a replay artifact + manifest. this is a positive design choice — failed runs are the most diagnostically valuable
9. **executor-owned storage (no `Runtime` widening)**
   - storage is bound on the executor (`SerialExecutor(storage=...)`), not on `Runtime`. `Runtime` is reserved for step capabilities (`LLM`, `NLP`, `Fetch`, `Budget`, `Reporter`); storage is executor-owned infrastructure for persistence and does not belong on the seam-J capability surface
10. **POSIX / single-filesystem atomicity**
    - phase-1 atomic writes assume `os.replace` semantics within one filesystem on a POSIX kernel. cross-filesystem stores and Windows-specific atomicity edge cases are out of scope for phase 1
11. **`InfrastructureError` is the sole storage exception class**
    - storage failures use `InfrastructureError` with prefixed messages (`"storage.missing_object: "`, `"storage.collision: "`, etc.) so callers can pattern-match on prefix when they need to distinguish causes. no `StorageError` sibling is introduced in phase 1
12. **deterministic msgspec encoding**
    - artifact serialization uses msgspec defaults with no custom hooks (`enc_hook=None`, `dec_hook=None`). pattern: `msgspec.msgpack.encode(artifact.model_dump(mode="python"))`. determinism depends on pydantic's stable mapping ordering inside `model_dump`; if a future pydantic version perturbs that order, the artifact-bytes equality proof surfaces the regression loudly

## Guardrails

- **write scope:** `src/extractx/replay/{artifact,writer,reader,__init__}.py`, `src/extractx/storage/{__init__,protocol,local}.py`, `src/extractx/schema/summary.py`, `src/extractx/schema/__init__.py` (export `SpecSummary` and `summarize_spec`), `src/extractx/execution/manifest.py`, `src/extractx/execution/policy.py` (add `PolicySummary`), `src/extractx/execution/executor/serial.py` (wire storage + persistence + result rebuild), `src/extractx/execution/__init__.py` (export `RunManifest`, `PolicySummary`), `pyproject.toml` (add `msgspec` dependency), focused tests
- **dependency change allowed:** `msgspec` may be added as a runtime dep (architecture-named default per §7 seam H). no other dep changes
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly
- **no `Runtime` changes whatsoever** (`src/extractx/execution/runtime.py` is read-only). storage lives on `SerialExecutor`
- **no resolver / planner / validator / strategy code changes**
- **no widening of `run_extraction(...)` signature**
- **no widening of `extractx.__init__` tier-1 exports** (`ReplayArtifact` is plugin-public per §10; `SpecSummary`, `RunManifest`, `PolicySummary`, `ExtractxStore`, `LocalFilesystemStore` are internal in phase 1)
- **no widening of seam J capability list** (no new step capability)
- **no `result` cache implementation** (deferred per ADR-0007)
- **no `interview` storage** (deferred per ADR-0004)
- **no `views/` implementation** (deferred per ADR-0007)
- **no second storage backend**
- **no async executor**
- **no iterative-strategy persistence**
- **no reporter step-event threading**
- **no acceptance / exemplar / benchmark machinery**
- **no `pickle` / `cloudpickle` / class-reference serialization** in `SpecSummary`
- **no commits or pushes** unless separately asked

## Pushback discipline

if a hard pin contradicts code reality (e.g. the landed `RegexCandidateStrategy.producer_version` exists under a different attribute name, or msgspec defaults turn out non-deterministic in this pydantic version), do **not** silently work around it. instead, in the final report under a `## Pushback` heading, write a structured block:

- current contract:
- observed gap or contradiction:
- consequence if implemented as written:
- proposed cleaner pattern:
- seam / ownership impact:
- whether this is clarification vs architecture change:
- proof target:

…and stop coding. the coordinator will adjudicate.

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/replay/{artifact,writer,reader,__init__}.py`
- `src/extractx/storage/{__init__,protocol,local}.py`
- `src/extractx/schema/summary.py`
- `src/extractx/execution/manifest.py`
- minimal `SerialExecutor` + `ExecutorPolicy` edits

include in your final report:

- exact files changed
- artifact field list as landed (vs the brief's list — surface drift if any)
- `SpecSummary` field list as landed
- store layout as landed (paths, file extensions)
- exact `compute_run_fingerprint` composition function signature + tuple shape
- the round-trip equality test paths (which files, which assertions for each of the three named equalities)
- whether `msgspec` was added cleanly or if any platform issue surfaced
- confirmation that `Runtime` was not modified (file diff stat: `runtime.py` unchanged)
- any follow-on that should become a coordinator-owned thread instead of widening this one (likely candidates: seam-replay re-execution, second backend, result cache, interview storage, views, async executor, `SpecSummary → ExtractionSpec` rehydration via class registry)

## Success criteria

- `ReplayArtifact` is a real frozen canonical type with all listed fields (no `pydantic_schema_hash`; narrow `producer_versions` keys)
- `SpecSummary` is a real frozen canonical type with all listed fields; `summarize_spec(spec)` produces it deterministically
- `ExtractxStore` protocol + `LocalFilesystemStore` cover the ADR-0007 minimum skeleton (`source`, `spec`, `replay`, `runs`)
- `RunManifest` carries `run_id` + `run_fingerprint` per ADR-0007 §5 and is built **only** via `RunManifest.from_artifact(...)` at the executor
- `SerialExecutor(storage=...)` opt-in writes one source blob, one `SpecSummary` blob, one replay artifact, one run manifest per persisted run
- `Runtime` is unchanged; seam J does not widen
- `ExtractionResult.replay_artifact_ref` is non-empty after a persisted run, empty after a non-persisted (`SerialExecutor()`) run
- failed runs persist artifact + manifest first-class
- three named equality proofs pass:
  - artifact-bytes round-trip
  - artifact-structural
  - result-structural
- `SpecSummary` round-trip proof passes
- determinism proof passes: identical inputs → identical `run_fingerprint` and identical artifact bytes
- authority-boundary proof passes: `result/`, `interview/`, `views/` are not created
- storage failure-message prefixes verified
- `Runtime`-not-widened proof passes (`runtime.py` unchanged in diff)
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`

## Downstream consequences

- once this lands, the supported path is end-to-end persistable and reproducible
- next clean threads (in priority order, all coordinator-owned, none folded into this one):
  1. **seam-replay re-execution** — re-run captured intermediates through C → D → E → F → G under pinning; assert reproduction of `final_instances` byte-for-byte. this thread also unlocks `SpecSummary → ExtractionSpec` rehydration via a class registry
  2. **second storage backend** — s3 or content-addressable store behind `ExtractxStore`
  3. **result cache** — `objects/result/<artifact-id>.json` write-on-demand under an explicit policy flag, populated **only** by serializing `reconstruct_extraction_result(replay)` (per drift §7 future-thread guardrail)
  4. **interview storage** — sibling artifact with independent retention per ADR-0004
  5. **views** — derived projections per ADR-0007 §2 (rebuildable; never authority)
  6. **async executor** + iterative-strategy persistence
- do not fold any of those into this thread
