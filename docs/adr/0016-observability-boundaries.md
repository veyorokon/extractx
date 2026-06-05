# ADR-0016: Observability Boundaries

## Status

Accepted.

## Context

extractx now has several surfaces that can describe a run:

- typed run records: `Extraction.trace`, `Extraction.usage_events`,
  `ReplayArtifact`, and `RunManifest`
- inspectable plan / dry-run projections from ADR-0011
- future or current CLI presentation, including human output and `--json`
- ordinary Python logging

Without a clear boundary, these surfaces can drift into overlapping sources of
truth. Logs can become pseudo-replay records, CLI JSON can become an implicit
contract, or consumers can be forced to parse presentation text for operational
diagnosis.

## Decision

Use four distinct observability layers.

1. **Typed run records are canonical facts.**

   `Extraction.trace`, `Extraction.usage_events`, replay artifacts, and run
   manifests are the durable record of what happened. They are the audit,
   replay, and downstream-consumer surface.

2. **Extraction plans are typed projections.**

   Plan and dry-run output answer "what will this run attempt?" They are derived
   inspectable models, not logs. CLI `--json` may render these models as JSON,
   but the typed model is the contract.

3. **Stdlib logging is operational visibility.**

   extractx may log seam-level lifecycle and diagnostic events through the
   standard logger tree rooted at `extractx`.

   Library code must not configure handlers, formatters, or levels. Consumers
   decide whether logs become text, JSONL, OpenTelemetry, structlog, or some
   other sink.

   Log records should use stable structured extras, especially:

   - `extractx_event`
   - `document_id`
   - `spec_version`
   - `field_id`
   - `instance_id`
   - `candidate_count`
   - `outcome`
   - `operation`
   - `model_id`
   - `replay_artifact_ref`

   The human log message may change; `extractx_event` is the stable event
   discriminator for consumers.

4. **CLI output is presentation.**

   CLI commands may print human-readable summaries by default and typed JSON
   with `--json`. CLI JSON is a rendering of typed objects; it is not the
   library logging substrate.

Hooks or callbacks are deferred. They should land only for a concrete
synchronous in-process need, such as live UI progress, cancellation, or custom
streaming orchestration that cannot be served by typed records plus logging.

## Logging Guardrails

Default logs must not include raw document text, candidate text, prompt bodies,
model outputs, secrets, or provider credentials. Those belong in explicit
debug/replay surfaces with their own retention and access controls.

Log at producer/consumer seams rather than every helper. Good first events:

- `extractx.extraction.started`
- `extractx.extraction.completed`
- `extractx.extraction.failed`
- `extractx.candidates.generated`
- `extractx.selector.started`
- `extractx.selector.completed`
- `extractx.selector.malformed_output`
- `extractx.validation.rejected`
- `extractx.instance_proposer.started`
- `extractx.instance_proposer.completed`

## Consequences

Consumers get normal Python logging integration without extractx owning their
log format or observability stack.

Replay and audit stay grounded in typed artifacts rather than ephemeral logs.

The CLI can copy familiar `--json` ergonomics without forcing library consumers
to parse CLI output.

Future hooks remain possible, but they must not duplicate the canonical run
record or logging substrate.

## Alternatives Rejected

- **Custom event bus in core.** Rejected because it would create a second
  observability substrate that consumers must adapt and tests must fake.
- **Library-owned JSON logging.** Rejected because formatting and routing are
  application concerns.
- **Using logs as replay/audit authority.** Rejected because logs are
  operational and may be filtered, sampled, reformatted, or absent.
- **CLI `--json` as the observability contract.** Rejected because CLI output
  is presentation; the typed models it renders are the contract.
