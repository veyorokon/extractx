# ADR-0027: Negotiate Provider Structured Output Modes

**Status:** Accepted
**Date:** 2026-05-06

## Context

extractx selectors and proposers call soft-compute providers with an expected
typed output model and require malformed output to fail at the producer seam.
ADR-0002 chose pydantic-ai as the default LLM-backed selector implementation,
and ADR-0015 made provider usage metadata a passthrough envelope.

Provider structured-output behavior is not uniform. Some providers and models
work best through tool/function output, some through native JSON Schema
response formats, and some only through prompted JSON text. Consumer benchmarks
now show a concrete mismatch: a provider/model can produce semantically valid
JSON for a selector output while failing the request because the transport
expected a tool-call envelope.

## Decision

extractx will make structured-output mode an explicit provider capability at
the provider adapter boundary.

Selectors, instance proposers, and future soft producers continue to depend on
one contract:

```python
ProviderFn(prompt: RenderedPrompt, output_model: type[T]) -> T | ProviderResult[T]
```

The provider adapter owns the transport-specific mechanism used to obtain that
typed `T`. It may use tool calls, native JSON Schema structured outputs, JSON
object mode plus validation, or prompted JSON text plus validation. Everything
above the provider adapter receives a typed output model or a typed provider
failure; it does not inspect provider-specific envelopes.

## Contract

The structured-output provider boundary is a soft-compute producer boundary.
It must validate provider output into `output_model` before returning to
selectors, proposers, or strategies.

Required invariants:

- provider protocol quirks do not shape semantic public contracts;
- selector and proposer implementations ask for typed output and receive typed
  output;
- provider adapters may branch on provider capability, model profile, endpoint,
  or explicit user configuration;
- provider adapters must fail loudly when the selected mode cannot produce an
  object valid for `output_model`;
- `UsageEvent.raw_usage` and `raw_response_metadata` remain provider-native
  passthrough metadata per ADR-0015;
- provider transport failures, schema-validation failures, refusals, and
  malformed output are diagnosis-preserving provider failures, not selector
  abstentions;
- retry semantics are mode-internal provider behavior. A retry may help the
  adapter obtain a valid `output_model`, but retries do not widen the
  `ProviderFn` contract and do not expose partial untyped outputs above the
  provider boundary;
- semantic contract checks remain above the provider adapter. For example,
  "selected candidate id belongs to this field's candidate set" is still a
  selector/strategy contract, not something pydantic-ai or the provider knows.

The provider may expose an explicit mode surface equivalent to:

```python
class StructuredOutputMode(StrEnum):
    AUTO = "auto"
    TOOL_CALL = "tool_call"
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPTED_JSON = "prompted_json"
```

Names are implementation-owned, but the semantics are not:

- `tool_call`: provider/model returns the output as tool/function arguments.
- `json_schema`: provider/model uses native structured outputs or JSON Schema
  response format.
- `json_object`: provider/model is constrained only to syntactically valid
  JSON; extractx still validates against `output_model`.
- `prompted_json`: prompt instructs the model to return JSON matching a schema;
  extractx parses and validates locally.
- `auto`: adapter chooses the adapter's backward-compatible default mode until
  explicit capability profiles are available. Phase 1 `auto` is intentionally a
  no-op for existing users; stronger automatic mode selection is a later
  profile-backed behavior.

Mode selection is provider adapter state, not schema state. A schema should not
need to change when a runtime swaps from OpenAI to Groq, Ollama, Anthropic, or
another provider, as long as the provider adapter returns the same typed model
contract.

## Strength Order

Structured-output modes are not equivalent in contract strength.

Preferred order:

1. native JSON Schema structured outputs, when supported for the provider and
   endpoint;
2. tool/function output, when the provider reliably requires and returns the
   tool-call envelope;
3. JSON object mode with local validation and bounded retry;
4. prompted JSON text with local validation and bounded retry.

Once provider capability profiles exist, `auto` should choose the strongest
available mode known to work for the provider/model. Provider profiles may
override the default order when a provider documents limitations, for example
"structured outputs do not support tool use" or "this model often emits plain
JSON instead of tool calls."

Capability profiles must have a falsification path. A profile is not just a
static registry entry; it should be backed by a tiny live contract probe or a
provider-specific contract fixture that proves the claimed mode can round-trip
one small `output_model`. Otherwise profiles rot and turn "auto" into another
hidden compatibility guess.

## Pydantic-AI Relationship

pydantic-ai already exposes conceptually similar modes: tool output, native
output, and prompted output. extractx should use those surfaces when they map
cleanly to the configured provider.

If pydantic-ai does not expose the required provider-specific mode cleanly,
extractx may add a direct provider adapter for that mode. That adapter still
implements the same `ProviderFn` contract and returns `ProviderResult[T]` when
usage metadata is available.

This does not reverse ADR-0002. pydantic-ai remains the default LLM-backed
selector implementation and transcript-capable backend. This ADR only states
that provider structured-output transport is a replaceable implementation
detail behind the provider adapter seam.

## Diagnostics

Provider adapters should emit enough structured diagnostic data to explain the
mode that was attempted and why it failed without exposing raw document content
by default.

Recommended diagnostic fields:

- `provider`
- `model_id`
- `endpoint`
- `structured_output_mode`
- `attempt_number`
- `output_model`
- `validation_error_count`
- `finish_reason`
- `response_id`
- `raw_error_type`
- `failure_stage`

If a provider returns parseable JSON in an error payload, the adapter may use
that only inside a mode-specific recovery path that still validates into
`output_model`. It must not leak "parse failed_generation from this provider"
branches into selector, strategy, or consumer code.

`failure_stage` should distinguish at least:

- `provider_protocol`: the provider rejected or could not complete the selected
  structured-output protocol;
- `parse`: the adapter could not parse the provider's content as JSON in a mode
  that requires local parsing;
- `output_validation`: the adapter parsed a JSON object but it did not validate
  as `output_model`;
- `semantic_contract`: typed output crossed the provider boundary but failed a
  selector/proposer contract above the provider adapter.

Native JSON Schema and tool-call modes may still expose raw provider metadata
for diagnostics. That metadata remains passthrough operational evidence, not a
normalized semantic contract.

## Implementation Phases

- **Phase 1 — Provider mode surface:** add an explicit structured-output mode
  config to provider adapters, with `auto` defaulting exactly to current
  behavior for existing users. Completion condition: existing OpenAI/pydantic-ai
  selector tests continue to pass, diagnostics record the selected mode, and no
  provider changes mode unless explicitly configured.
- **Phase 2 — Native JSON Schema adapter path:** support a native JSON Schema
  mode for OpenAI-compatible providers that expose `response_format:
  json_schema`. Completion condition: a provider that rejects tool-call output
  but supports JSON Schema can return the same typed selector DTO.
- **Phase 3 — Provider capability profiles:** add documented capability
  profiles or explicit config for providers whose strongest supported mode is
  known. Completion condition: `auto` mode is deterministic, test-covered for
  at least one tool-call provider and one JSON-Schema provider, and every
  profile has a live probe or contract fixture that can falsify the claimed
  mode.
- **Phase 4 — Prompted JSON fallback:** optionally add prompted JSON as an
  explicit fallback mode for models without tool calls or native schema support.
  Completion condition: failures validate locally, retry boundedly, and never
  cross the provider boundary as untyped dicts.

## Alternatives Considered

- **Parse provider-specific failed payloads in selectors.** Rejected. That
  would leak transport quirks into seam D and make selector code responsible
  for provider envelopes.
- **Tell prompts to avoid tools.** Rejected as the primary fix. Prompt wording
  cannot reliably repair a provider/API protocol mismatch and would couple
  schema behavior to one provider's transport.
- **Require all providers to use tool calls.** Rejected. Native JSON Schema is
  a first-class structured-output mechanism in multiple provider ecosystems and
  is often stronger than tool calls for pure extraction outputs.
- **Switch away from pydantic-ai globally.** Rejected. The mismatch is at the
  provider structured-output transport layer, not proof that pydantic-ai is the
  wrong selector backend.
- **Accept untyped dicts above the provider seam.** Rejected. That would weaken
  extractx's soft-compute boundary. Untyped JSON may exist inside an adapter
  while parsing, but only validated typed output may cross the seam.

## Consequences

- Provider/model swaps become cleaner because transport quirks are hidden behind
  one provider contract.
- Fast providers that support JSON Schema but not reliable tool envelopes can
  be adopted without changing selector prompts or schema definitions.
- The provider layer becomes slightly more complex: mode selection,
  capability profiles, and diagnostics need tests.
- Runtime configuration must make the selected mode inspectable enough for
  benchmark forensics and drift investigation.
- Existing selector, planner, replay, scoring, and observation contracts remain
  unchanged.

## Related

- [ADR-0002: Adopt pydantic-ai as default llm-backed Selector](0002-pydantic-ai-default-selector-and-interview.md)
- [ADR-0015: Minimal Soft-Compute Usage Events](0015-minimal-soft-compute-usage-events.md)
- [ADR-0023: Batch Selector Observations](0023-batch-selector-observations.md)
- [ADR-0024: Readable Bounded ID Selector Prompts](0024-readable-bounded-id-selector-prompts.md)
- [Pydantic AI output modes](https://pydantic.dev/docs/ai/core-concepts/output/)
- [Groq structured outputs](https://console.groq.com/docs/structured-outputs)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
