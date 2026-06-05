# Task: implement seam F phase 1 candidate-layer + field-layer validation

*This is seam F phase 1. Make the `ProposalValidator` seam real as two honest, per-`ProposedField` layers: layer 1 (candidate shape + source-span validity per ADR-0006) and layer 2 (the single normalization site per §7 seam F and the `Dual Normalization` anti-pattern). Layer 3 is out of scope for phase 1 and is owned by a later thread that lands after `G.resolver`. This thread proves the seam contract without smuggling in instance-layer validation, retry orchestration, or executor/reporter wiring.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic seam / contract / thread / proof doctrine
- [`CODEX.md`](../../CODEX.md) — repo-local guide; canonical nouns; seam F summary; forbidden shortcuts (note: `Dual Normalization`, `Pydantic-as-Extractor`, `Silent None`, `Lifecycle-Object Conflation`)
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies, git rules, hook rules
- [`docs/architecture.md`](../architecture.md) — read **§7 seam F in full** (structural note, producer/consumer, layers 1/2/3, invariants, hidden); **§7 seam E** (to understand the `ProposedField` tuple this seam consumes); **§7 seam B** (`SpecError` triggers, specifically the `Pydantic-as-Extractor` rejection at spec load — seam F assumes spec-load already filtered); **§9 canonical objects** for `ProposedField`, `ValidatedField`, `NegativeOutcome`, `ValidationFailure`, `ValidationBinding`; **§10 three-tier public surface** (`ValidatedField` is plugin-public; `ProposalValidator` is internal machinery); **§13 public api surface** (`ValidationFailure` routed through `ExecutorPolicy` is declared but not yet landed); **§15 anti-patterns** (`Dual Normalization`, `Silent None`, `Pydantic-as-Extractor`, `Lifecycle-Object Conflation`); **§16 project layout** (`src/extractx/proposals/validation.py`); **§17 proof table seam F entries**
- [`docs/adr/0003-single-canonical-layer3-no-resolver-validators.md`](../adr/0003-single-canonical-layer3-no-resolver-validators.md) — **load-bearing** for seam F phase 1. canonical layer 3 is the sole instance-layer validation phase, runs exactly once per `InstanceResult` that reaches layer 3, post-resolution. phase 1 excludes layer 3 on this basis; treat layer 3 as a later thread
- [`docs/adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md`](../adr/0006-sourcespan-textual-anchor-space-and-visual-provenance.md) — layer 1 span-validity discipline dispatches on `SourceSpan.text_anchor_space`; failure codes `candidate.text_anchor_space_mismatch` and `candidate.utf8_alignment` are ADR-0006 terms
- [`docs/tasks/core-contracts-and-objects.md`](core-contracts-and-objects.md) — prior thread; use the landed core `ProposedField`, `ValidatedField`, `NegativeOutcome`, `ValidationFailure`, `ValidationBinding`, `AnchorMap`, `SourceSpan`, and `anchors.py` helpers instead of reinventing them
- [`docs/tasks/seam-b-pydantic-spec-construction.md`](seam-b-pydantic-spec-construction.md) — seam B already rejects `Pydantic-as-Extractor` at spec load; seam F phase 1 relies on that and does not re-check the rule at layer 2
- [`docs/tasks/seam-e-cardinality-selection-adapter-phase-1.md`](seam-e-cardinality-selection-adapter-phase-1.md) — seam F phase 1 consumes the real `ProposedField` tuple emitted by `CardinalitySelectionAdapter`; reuse the canonical-name-vs-architecture-prose discipline established there

## Goal

implement seam F phase 1 so a deterministic `ProposalValidator` can consume a real `ProposedField` produced by seam E, run layer 1 (candidate-shape + span-validity checks) and layer 2 (the single normalization site) honestly, and emit one of `ValidatedField` | `NegativeOutcome` | `ValidationFailure` per the seam F invariants. layer 3 (cross-field instance-layer validation) is out of scope.

**"done" in one sentence:** a deterministic, per-`ProposedField` `ProposalValidator` runs layer 1 span-validity dispatched on `text_anchor_space` and layer 2 normalization (pydantic coercion + `field_validator`s for pydantic-backed specs; `ValidationBinding.normalizer` + `FieldValidator`s for manual specs), emitting `ValidatedField` | `NegativeOutcome` | `ValidationFailure`, with normalization happening at exactly one site and no layer-3 behavior smuggled in.

## Scope

numbered implementation areas. do each in order.

### 1. make the seam-F protocol explicit

implement the `ProposalValidator` callable surface in `src/extractx/core/contracts.py`.

requirements:

- define the phase-1 protocol method explicitly:
  - `validate(proposed: ProposedField, field_spec: FieldSpec, document_view: DocumentView, schema_cls: type[BaseModel] | None = None) -> ValidatedField | NegativeOutcome | ValidationFailure`
- `document_view` is required because layer 1 validates span offsets against the document's `normalized_text` / `anchor_map`. this mirrors seam E's shape: narrow, sync, deterministic, and explicit about what crosses the seam.
- `schema_cls` is caller-held runtime context for the pydantic-backed path. when non-`None`, layer 2 uses the pydantic-backed path; when `None`, layer 2 uses the manual `ValidationBinding` path. this keeps `ExtractionSpec` portable rather than smuggling a live schema class into spec state.
- keep it sync and pure for phase 1
- do not add a layer-3 callable surface on the protocol in this task
  - the architecture's §7 seam F describes a layer-3 consumer that takes an `InstanceResult`; do **not** land that method in phase 1. leave it for the later layer-3 thread
- keep the seam narrow:
  - consumes only `ProposedField`, `FieldSpec`, `DocumentView`, and optional caller-held `schema_cls`
  - emits exactly one of `ValidatedField` | `NegativeOutcome` | `ValidationFailure`
- do not add retry/reporter/runtime concerns here
- do not add `UsageEvent` emission in this task — validators are algorithmic

implementation-shape constraints:

- one method only — phase 1 proves layers 1 and 2 against a single per-`ProposedField` call
- no async protocol in this task
- name the protocol method `validate` (not `layer1` / `layer2`): the layers are internal dispatch inside the validator, not part of the public surface
- `ProposalValidator` is the canonical noun for the internal seam-F machinery (plugin-public `FieldValidator` and `InstanceValidator` are user-facing and live alongside — do not confuse them; see §10 plugin-public)

### 2. implement layer 1 — candidate-layer validation (span validity + structured_payload shape)

implement the narrow, deterministic layer-1 checks in `src/extractx/proposals/validation.py`.

requirements:

- layer 1 runs first inside `validate(...)` and dispatches on each span's `SourceSpan.text_anchor_space` per ADR-0006:
  - for `text_anchor_space="normalized_text"`:
    - `span.byte_end <= len(document_view.normalized_text.encode("utf-8"))`
    - both `byte_start` and `byte_end` are UTF-8-aligned against `document_view.normalized_text.encode("utf-8")`
    - use the landed `is_utf8_aligned(data, offset)` helper from `src/extractx/core/anchors.py`, or equivalently `check_normalized_text_span(span, normalized_text)` which raises on any of the above
  - for `text_anchor_space="source_bytes"`:
    - the span must be recoverable from `document_view.anchor_map` by `anchor_invert(anchor_map, span)` — if inversion raises `ValueError`, the span is invalid
- validate both `proposed.source_span` and every `proposed.evidence_spans[i]` under the same rule
- if any span's `text_anchor_space` is inconsistent with the `DocumentView`'s adapter subcontract (mixed-space spans within one `DocumentView`), emit `NegativeOutcome(category="validation", code="candidate.text_anchor_space_mismatch", field_id=proposed.field_id, instance_key=proposed.tentative_instance_key, reason="candidate.text_anchor_space_mismatch")`
- if a `normalized_text` span is UTF-8 misaligned or out of range, emit `NegativeOutcome(category="validation", code="candidate.utf8_alignment", ...)`
- other shape failures remain `NegativeOutcome(category="validation", code="candidate.<specific>", ...)` — pick a small, stable set of codes: at minimum `candidate.span_out_of_range` for `source_bytes` inversion failures, and `candidate.structured_payload_shape` for structured-payload shape defects (see below)
- layer 1 also validates `structured_payload` shape where the `Candidate` carried one. in phase 1 the only honest check is:
  - if `proposed.normalized_hint` is not `None`, it must be JSON-safe (primitive, `Mapping`, or `Sequence` of same). live pydantic models, custom classes, or other non-JSON-safe objects are shape defects → `NegativeOutcome(category="validation", code="candidate.structured_payload_shape", ...)`. justification: seam C's `Candidate.structured_payload` is typed `Mapping[str, Any] | None`, but `ProposedField.normalized_hint` in landed core is `Any | None`. phase 1 closes that gap narrowly at layer 1 rather than tightening the upstream type.
- layer 1 failures are **non-retryable** per §7 seam F. they stop at this seam as `NegativeOutcome`. do not emit `ValidationFailure` for layer 1
- layer 1 emits no `ValidationFailure`; layer 1 emits only `NegativeOutcome` or passes through

implementation-shape constraints:

- dispatch on `SourceSpan.text_anchor_space` explicitly. never collapse the two spaces into one offset check
- do not re-implement `is_utf8_aligned` or `anchor_invert`. import from `src/extractx/core/anchors.py`
- do not check `structured_payload` semantics beyond shape — no schema validation, no type coercion
- do not inspect `Candidate` directly — seam F only sees `ProposedField`. `ProposedField.normalized_hint` is the only carrier of candidate-derived structured data at this seam
- do not introduce a new exception type for layer 1; layer 1 failures are typed `NegativeOutcome`s, not raised exceptions

### 3. implement layer 2 — field-layer normalization (the single normalization site)

implement the layer-2 normalization dispatch in `src/extractx/proposals/validation.py`, after layer 1 passes.

requirements:

- **this is the single normalization site.** no other seam in the repo may normalize. anti-pattern: `Dual Normalization` (§15)
- dispatch by `schema_cls` presence:
  - **pydantic-backed path** (`schema_cls is not None`): run pydantic's type coercion on `proposed.raw_value` via the caller-provided schema class for the field, then run pydantic `field_validator`s for that field. the architecture says pydantic validators run **here and nowhere else** (§7 seam F invariants)
  - **manual path** (`schema_cls is None`): call `field_spec.validation_binding.normalizer(proposed.raw_value)`, then call each `FieldValidator` in `field_spec.validation_binding.field_validators` in declared order on the normalized value
- success → emit `ValidatedField(proposed=proposed, normalized_value=<result>, field_validation_version=<version>)`
- failure → emit `ValidationFailure(layer="field", field_id=proposed.field_id, instance_key=proposed.tentative_instance_key, reason=<str>, producer_version=<field_validation_version or None>)`
- `field_validation_version` composition:
  - use `algorithmic_producer_version(code_hash=...)` from `src/extractx/core/versions.py`
  - compose `code_hash` as a `stable_hash(...)` over a deterministic tuple describing the validator pipeline for this field. a sensible phase-1 shape:
    - `(spec_version, field_id, pydantic_backed_bool, normalizer_qualname_or_none, tuple(field_validator_qualnames))`
  - do **not** invent a second producer-version scheme. the seam F producer-version discipline is the same as seams C/D
  - if the landed `ValidatedField.field_validation_version` shape is a `str`, use exactly the `"code:{code_hash}"` form that `algorithmic_producer_version` returns
- routing of `ValidationFailure` through `ExecutorPolicy.on_validation_failure` is declared by §7 seam F but **not** implemented by this task. phase 1 emits `ValidationFailure` as the typed output and documents that the execution substrate (seam I/J) owns the retry/escalation loop. do not invent a retry policy here
- in landed core, `ExecutorPolicy` does not yet exist; `ValidationPolicy.on_validation_failure` is a `str | None` placeholder. this task does not advance that surface

implementation-shape constraints:

- normalization happens exactly once per `ProposedField`. there is no second normalization pass elsewhere in the validator or the surrounding seam
- pydantic validators never see raw text at seam F phase 1 — they see the post-coercion value. seam B already rejects `mode="before"` `field_validator`s that accept `str` via `detect_pydantic_as_extractor` in `src/extractx/schema/validators.py`; seam F assumes spec-load succeeded and does not re-check the rule
- if `schema_cls is None` and `FieldSpec.validation_binding is None`, that is a seam-B defect (seam B emits `SpecError` on manual `FieldSpec` with `validation_binding=None` and no pydantic class fallback per §7 seam B). seam F phase 1 does not re-check this invariant proactively — if the validator is handed that malformed combination at runtime anyway, fail loudly with a local `ValueError`-subtype, not a typed negative
- no exception bubbling to the caller. pydantic `ValidationError` from coercion or `field_validator` is caught and translated into `ValidationFailure(layer="field", reason=str(exc), ...)`
- do not compute `field_validation_version` inside the pydantic-ai / llm path — this seam is deterministic and has no llm access
- do not embed the normalizer's output in `reason`; `reason` is an operator-facing string drawn from the exception message or a stable code

### 4. wire failure-routing shape (stub phase)

layer 2 emits `ValidationFailure`. phase 1 does **not** implement `ExecutorPolicy.on_validation_failure` retry behavior.

requirements:

- document in `src/extractx/proposals/validation.py` module docstring that:
  - `ValidationFailure(layer="field", ...)` is the typed output from layer 2 failure
  - routing / retry / escalation is owned by the execution substrate (seam I/J) and lands in a later thread
  - `NegativeOutcome(category="validation", code="validation.field.*", ...)` escalation from exhausted retries happens at that later seam, not here
- do not add a retry loop in this seam
- do not invent a `Reporter` call from the validator
- the validator does not distinguish between retryable and non-retryable field-layer failures — §7 says layer 2 failures are "potentially recoverable via `ExecutorPolicy.on_validation_failure`"; phase 1 surfaces the typed failure and lets the later substrate decide

implementation-shape constraints:

- do not import `ExecutorPolicy` from `execution/policy.py` into the validator
- do not add a `ValidationPolicy` consumption path here. spec-wide policy is owned by a later thread
- do not re-raise exceptions; every path returns a typed outcome

### 5. package wiring

implement the minimal validator surface so seam F phase 1 is importable and testable.

requirements:

- land the concrete validator in `src/extractx/proposals/validation.py` with class name fixed for this task:
  - `LayeredProposalValidator`
- wire exports in `src/extractx/proposals/__init__.py`:
  - `LayeredProposalValidator`
  - any local error type if you introduced one (see "guardrails" — a local `ProposalValidatorContractError(ValueError)` is acceptable for runtime-reachable `FieldSpec` shape defects, mirroring `SelectionAdapterContractError`)
- do not move canonical object definitions out of `core/outcomes.py` or `core/objects.py`
- do not widen top-level `extractx/__init__.py` in this task

write-scope note:

- the only supporting edits outside `src/extractx/proposals/**` should be the smallest ones required in:
  - `src/extractx/core/contracts.py` (`ProposalValidator` protocol method signature)
  - `src/extractx/core/__init__.py` (only if the module already re-exports sibling protocols and the new shape requires a companion export)
- do not edit `src/extractx/schema/validators.py` — the `Pydantic-as-Extractor` guard is seam B's responsibility

### 6. explicit non-goals for this task

leave these out:

- **layer 3** (cross-field instance-layer validation). ADR-0003 pins layer 3 to post-`G.resolver` execution; it runs exactly once per `InstanceResult` that reaches layer 3. layer 3 is a separate later thread
- `ExecutorPolicy.on_validation_failure` retry orchestration (seam I/J concern)
- `Reporter` trace emission from the validator
- `UsageEvent` emission — validators are algorithmic in phase 1
- llm / pydantic-ai / interview capture
- seam G.planner / G.resolver behavior
- runtime/executor construction, `Runtime.from_env()`, capability injection
- replay artifact writing, manifest hashing
- `C.alt` grounded proposal generator behavior
- materialization (`to_pydantic`)
- any re-check of seam B's `Pydantic-as-Extractor` rejection at layer 2

typed stubs may remain where needed, but do not invent behavior owned by later or separate threads.

## Guardrails

- **write scope:** `src/extractx/proposals/validation.py`, `src/extractx/proposals/__init__.py` (re-export edit only), focused tests under `tests/contracts/` and `tests/proposals/`, and only the smallest supporting edits in:
  - `src/extractx/core/contracts.py`
- **no docs edits** unless you hit a real contradiction that makes the task impossible to complete honestly. if so, stop and report with the standard pushback shape
- **no dependency changes** in this task
- **no layer-3 behavior.** do not implement cross-field validation, pydantic `model_validator` invocation, or `InstanceResult` consumption
- **no dual normalization.** if a second normalization site appears anywhere else in the repo, that is a bug in the other seam, not something this seam should paper over
- **no silent coercion.** layer 1 and layer 2 failures become typed `NegativeOutcome` / `ValidationFailure`. never coerce invalid input into an absent-but-valid `ValidatedField`
- **no `Pydantic-as-Extractor` re-check at layer 2.** spec-load already blocks that; re-checking here would be duplicate overlapping policy (§15 `Duplicate Overlapping Path`)
- **no widening the end-user public surface** in this task. `ProposalValidator` is internal machinery per §10
- **no commits or pushes** unless separately asked. leave the branch ready for review

## Focused proof

add focused tests primarily under `tests/contracts/` and `tests/proposals/`.

minimum proof targets to cover:

- **surface:** `ProposalValidator.validate(proposed, field_spec, document_view, schema_cls=None) -> ValidatedField | NegativeOutcome | ValidationFailure` exists on the protocol surface
- **purity:** same `(proposed, field_spec, document_view, schema_cls)` yields byte-identical output across repeated calls
- **layer 1 span validity — `normalized_text` path:**
  - valid UTF-8-aligned in-range span on a `normalized_text` adapter → passes layer 1
  - UTF-8-misaligned `byte_start` or `byte_end` → `NegativeOutcome(category="validation", code="candidate.utf8_alignment", ...)`
  - `byte_end` exceeding `len(normalized_text.encode("utf-8"))` → `NegativeOutcome(category="validation", code="candidate.utf8_alignment", ...)` (or a sibling `candidate.*` code if the implementation distinguishes "out of range" from "misaligned"; pick one and stick to it)
- **layer 1 span validity — `source_bytes` path:**
  - span recoverable via `anchor_invert(anchor_map, span)` → passes layer 1
  - span straddling a segment boundary or outside the anchor map → `NegativeOutcome(category="validation", code="candidate.span_out_of_range", ...)` (or equivalent `candidate.*` code)
- **layer 1 `text_anchor_space` mismatch:** a span whose `text_anchor_space` does not match the `DocumentView`'s adapter subcontract (as evidenced by the anchor-map entries) → `NegativeOutcome(category="validation", code="candidate.text_anchor_space_mismatch", ...)`
- **layer 1 `structured_payload` shape:** a `ProposedField.normalized_hint` that is not JSON-safe → `NegativeOutcome(category="validation", code="candidate.structured_payload_shape", ...)`
- **layer 1 is non-retryable:** layer 1 never emits `ValidationFailure`; only `NegativeOutcome` or pass-through
- **layer 2 pydantic path:**
  - caller provides `schema_cls`; pydantic coercion on `raw_value` runs once; the resulting coerced value is visible to a pydantic `field_validator` registered on that schema class
  - a pydantic `field_validator` that succeeds returns a `ValidatedField(normalized_value=<coerced>, field_validation_version="code:...")` with deterministic version
  - a pydantic `field_validator` that raises → `ValidationFailure(layer="field", reason=<str>, ...)`, not an exception
- **layer 2 manual path:**
  - `ValidationBinding.normalizer(raw_value)` runs, followed by each `FieldValidator` in declared order
  - a `FieldValidator` rejecting the normalized value → `ValidationFailure(layer="field", reason=<str>, ...)`
  - validators run in declared order (prove by instrumenting a counter on test-only validators and asserting the sequence)
- **single normalization site:** assert that normalization runs exactly once per `validate` call. one way: instrument a counting normalizer, invoke `validate`, assert `normalizer.calls == 1` regardless of how many `field_validator`s fire
- **`Dual Normalization` prevention:** a test that imports `proposals/validation.py` and `proposals/adapter.py` (seam E) and statically confirms that `proposals/adapter.py` does not call `ValidationBinding.normalizer` or any pydantic coercion entry point. this is the invariant-level check for `Dual Normalization`
- **`Pydantic-as-Extractor` rejection at spec load, not at layer 2:**
  - a pydantic schema with a `mode="before"` `str`-typed `field_validator` raises `SpecError` at `ExtractionSpec.from_pydantic(...)` (covered by seam B tests — reuse or reference them)
  - layer 2 does **not** re-run this detection — a test that constructs a `FieldSpec` directly (bypassing `from_pydantic`) still succeeds at layer 2 if the raw-value coercion works, to prove the check is not duplicated here
- **`ValidatedField.field_validation_version` determinism:** same spec + same field + same validator bindings → same `field_validation_version`. change the normalizer's qualname or a `field_validator`'s qualname → different `field_validation_version`
- **lifecycle invariant:** the emitted `ValidatedField.proposed` is the same object as the input `ProposedField` (identity preserved; no clone), and `ValidatedField` is frozen (mutation raises)
- **layer 3 absence:** `ProposalValidator.validate(...)` does **not** invoke any pydantic `model_validator` or `InstanceValidator`. a test that attaches a raising `model_validator` to the schema class confirms it is never called

no smoke test is required in phase 1 — seam F phase 1 proves at contract level only.

## Deliverable

code and focused tests in the repo, centered in:

- `src/extractx/proposals/validation.py`
- `src/extractx/proposals/__init__.py`
- `src/extractx/core/contracts.py` (add the `ProposalValidator.validate` method signature)

with focused tests under:

- `tests/contracts/` (seam-F protocol surface + invariants)
- `tests/proposals/` (layer 1 + layer 2 behavior)

include in your final report:

- exact files changed
- the concrete validator class name (`LayeredProposalValidator`)
- the chosen `candidate.*` code set for layer 1 failures (the stable, small set the implementation landed on)
- how `field_validation_version` was composed (exact tuple you hashed)
- confirm that pydantic-backed specs use the caller-provided `schema_cls` runtime parameter, not `ExtractionSpec.source_schema_ref`, and note any follow-on drift or ergonomics concern rather than inventing a second resolution path
- any remaining ambiguity that should become a coordinator-owned follow-on thread rather than more code

## Success criteria

- `ProposalValidator` has an explicit callable surface (`validate(...)`)
- seam F is real for one deterministic `LayeredProposalValidator`
- layer 1 dispatches on `SourceSpan.text_anchor_space` using the landed `anchors.py` helpers (`is_utf8_aligned`, `check_normalized_text_span`, `anchor_invert`)
- layer 1 failures are typed `NegativeOutcome`s with the ADR-0006 codes (`candidate.text_anchor_space_mismatch`, `candidate.utf8_alignment`) plus a narrow set of other `candidate.*` codes for structural defects
- layer 2 is the single normalization site; pydantic coercion + `field_validator`s for pydantic-backed specs, `ValidationBinding.normalizer` + `FieldValidator`s for manual specs
- layer 2 success emits `ValidatedField` with a deterministic `field_validation_version`
- layer 2 failure emits `ValidationFailure(layer="field", ...)` as the typed output, routed-through-`ExecutorPolicy` behavior is explicitly deferred
- no layer-3 behavior is smuggled in; no pydantic `model_validator` runs at phase 1
- no re-check of the `Pydantic-as-Extractor` rule at layer 2
- focused proof passes:
  - `uv sync`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pyright`
- top-level repo state remains coherent with the architecture/doc pact

## Architecture drift acknowledged

these drift points between architecture prose and landed core must be resolved by pinning the worker to **code reality**. do not invent new fields; surface to the coordinator if a new contradiction arises.

1. **`ProposedField.normalized_value` vs `ProposedField.normalized_hint`.** the architecture §7 seam E prose says "`ProposedField.normalized_value` is always `None` at this seam — normalization is seam F's exclusive responsibility." the canonical landed type in `src/extractx/core/outcomes.py` has `normalized_hint: Any | None`, not `normalized_value`. seam E's phase-1 brief already pinned the worker to code reality on this point. **seam F phase 1 does the same:** treat `ProposedField.normalized_hint` as the carrier of candidate-derived structured data (shape-checked in layer 1), and produce `ValidatedField.normalized_value: Any` at layer 2. no new `normalized_value` field is added to `ProposedField`.
2. **`ProposedField.instance_key` vs `ProposedField.tentative_instance_key`.** the architecture §7 seam E / §9 prose uses `instance_key`; the landed type has `tentative_instance_key: InstanceKey | None`. pin the worker to the landed name. `ValidationFailure.instance_key` maps from `proposed.tentative_instance_key`.
3. **`ProposalValidator` vs user-facing protocols.** `FieldValidator` and `InstanceValidator` already exist as plugin-public protocols in `core/contracts.py` (empty bodies awaiting seam-F work). `ProposalValidator` is the internal seam-F machinery. do not add `ProposalValidator` to the plugin-public tier. do not rename `FieldValidator` or `InstanceValidator`.
4. **Pydantic-backed schema resolution path.** architecture §7 seam F assumes pydantic coercion happens here, but landed `ExtractionSpec.source_schema_ref: SchemaRef | None` is a portability placeholder, not a runtime-resolvable schema-class carrier. phase 1 resolves this by making `schema_cls: type[BaseModel] | None` an explicit caller-provided runtime parameter on `ProposalValidator.validate(...)`. this keeps `ExtractionSpec` portable and avoids inventing a second schema-resolution mechanism inside seam F.
5. **`ExecutorPolicy.on_validation_failure`.** architecture §7 seam F describes retry routing through `ExecutorPolicy.on_validation_failure`. landed core has only `ValidationPolicy.on_validation_failure: str | None` (placeholder in `core/objects.py`) and `execution/policy.py` is an empty stub. phase 1 does not depend on this surface; the validator emits `ValidationFailure` and the execution substrate (later thread) implements routing. the brief must not invent a retry loop inside the validator.
6. **Layer-3 consumer signature.** §7 seam F states layer 3 consumes `InstanceResult`. phase 1 does not add that method to the protocol. the layer-3 thread owns both the protocol extension and the post-`G.resolver` invocation site.
7. **`GroupingResolver` → `InstanceResolver`.** CODEX.md notes that the canonical noun is `InstanceResolver`. the brief uses `InstanceResolver` / `G.resolver` consistently.
8. **Layer 1 / 2 input widening for `DocumentView`.** architecture §7 seam F prose says layers 1 and 2 consume `ProposedField`. in landed reality, layer 1 needs `DocumentView` to validate spans against `normalized_text` and `anchor_map`. phase 1 pins the worker to the explicit `(proposed, field_spec, document_view, schema_cls)` signature; the architecture line is drifted and should align in a later doc pass.
9. **`CandidateSet.instance_hint` → `ProposedField.tentative_instance_key` under iterative fill.** the seam-E brief pins the worker to treat `tentative_instance_key = candidate_set.instance_hint` during phase-1 adaptation. seam F phase 1 sees `ProposedField.tentative_instance_key` and uses it verbatim for `ValidationFailure.instance_key` — no re-derivation.

if any additional contradiction surfaces mid-implementation that cannot be resolved by pinning to code reality, stop and report using the pushback shape (current contract / observed gap / consequence / proposed cleaner pattern / seam ownership impact / clarification vs architecture change / proof target).

## Downstream consequences

- gives seam G (resolver) a real `ValidatedField` surface to promote into `ResolvedFieldProposal`s
- establishes the single normalization site, removing ambiguity about where normalization lives in the lifecycle `ProposedField` → `ValidatedField` → `ResolvedFieldProposal`
- leaves layer 3 (cross-field instance-layer validation) for a later thread that runs after `G.resolver` assigns final `InstanceKey`s, consistent with ADR-0003
- leaves `ExecutorPolicy.on_validation_failure` retry routing for the execution-substrate thread (seam I/J)
- the first honest `DocumentView → CandidateSet → Selection → ProposedField → ValidatedField` path becomes provable at contract level once this task lands
- if this task exposes a real contradiction in the current seam-F contract (e.g. a phase-1 invariant that cannot hold without layer-3 behavior, or a pydantic-backed schema-resolution path with no landed home), that becomes a new coordinator-owned thread before more implementation proceeds
