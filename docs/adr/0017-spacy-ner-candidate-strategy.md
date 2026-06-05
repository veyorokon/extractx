# ADR-0017: spaCy NER Candidate Strategy

## Status

Accepted.

## Context

Regex candidate strategies are useful, but broad numeric/date/money regexes can
produce noisy candidate sets. A selector can sometimes disambiguate noisy
candidates from prose context, but broad candidate menus increase cost, reduce
prompt clarity, and can surface false positives when unrelated numbers share the
same lexical shape.

NER is a better bounded-candidate source for entity-shaped values because it
uses tokenization and entity spans rather than raw character patterns. For
example, an NER pipeline can treat a full date as one `DATE` span rather than
separate numeric fragments.

`NerCandidateStrategy` provides a generic mechanism that consumers can
configure with their own domain policy. It must not embed downstream domain
vocabulary into core.

## Decision

Implement `NerCandidateStrategy` as an explicit opt-in `CandidateStrategy`
backed by spaCy.

V1 scope:

- `NerCandidateStrategy` lives in `extractx.candidates.generators.ner`.
- spaCy is an optional dependency exposed through `extractx[spacy]`.
- If spaCy is unavailable, strategy construction or generation raises
  `InfrastructureError` with a message naming `extractx[spacy]`.
- The strategy runs on `DocumentView.normalized_text`.
- V1 emits candidates from `doc.ents` only.
- EntityRuler configuration is supported through typed, JSON-safe config
  objects.
- Post-entity filters are referenced by registered component names only.
- `entity_filter` may narrow emitted labels.
- All strategy params must be serializable and stable enough to participate in
  spec hashing and replay diagnostics.
- Candidate source spans must be translated through extractx's existing
  `DocumentView` / `SourceSpan` coordinate contract. spaCy character offsets are
  not emitted directly as extractx source offsets.
- Tests and docs use generic examples only.

The v1 strategy is not auto-attached for `ValueKind.MONEY`,
`ValueKind.PERCENT`, `ValueKind.DATE`, or `ValueKind.CARDINAL`. Schemas must
bind it explicitly through `extract_field(strategy_bindings=(...,))`.

## Config Contract

The implementation should use typed config models rather than raw dict soup.
The exact class names are implementation-owned, but the contract should include
the equivalent of:

```python
class NerEntityRulerConfig(BaseModel):
    name: str
    patterns: tuple[dict[str, object], ...]
    overwrite_ents: bool = False


class NerStrategyConfig(BaseModel):
    model_id: str = "en"
    entity_rulers: tuple[NerEntityRulerConfig, ...] = ()
    filter_components: tuple[str, ...] = ()
    entity_filter: tuple[str, ...] | None = None
```

`model_id="en"` should be supported for tests and lightweight usage by creating
a blank English pipeline. Other `model_id` values may be loaded with
`spacy.load(model_id)`.

Raw callables must not appear in `StrategyBinding.params`. If a consumer needs a
custom filter, it registers a spaCy component by stable name and references that
name in `filter_components`.

## Usage Example

Schemas bind the strategy explicitly. `ValueKind` describes the expected value;
it does not choose NER automatically.

```python
from typing import Annotated

from pydantic import BaseModel

from extractx import ValueKind, extract_field
from extractx.candidates import NerCandidateStrategy, NerEntityRulerConfig
from extractx.core import StrategyBinding


class InvoiceSummary(BaseModel):
    total_due: Annotated[str, ValueKind.MONEY] = extract_field(
        description="invoice total due",
        strategy_bindings=(
            StrategyBinding(
                cls=NerCandidateStrategy,
                kind="candidate",
                params={
                    "model_id": "en",
                    "entity_rulers": (
                        NerEntityRulerConfig(
                            name="invoice_money",
                            patterns=(
                                {"label": "MONEY", "pattern": "$42.50"},
                            ),
                        ).model_dump(mode="json"),
                    ),
                    "entity_filter": ("MONEY",),
                },
            ),
        ),
    )
```

Install spaCy support with `extractx[spacy]`. `model_id="en"` uses
`spacy.blank("en")`, which is suitable for configured `EntityRuler` patterns.
Pretrained spaCy models are installed separately and referenced by package name
through `model_id`.

## Consequences

Consumers get a stronger deterministic candidate-generation mechanism without
turning extractx into a domain vocabulary package.

Replay and spec hashing remain stable because strategy configuration is typed
and serializable.

CI can test the strategy with `spacy.blank("en")` plus `EntityRuler`; no model
download is required for the core test suite.

The strategy remains a candidate source, not an authority. Text candidates from
NER still flow through the existing selector, observation adapter, and validator
contracts.

## Alternatives Rejected

- **Default auto-attachment for numeric/date value kinds.** Rejected for v1.
  NER is structurally cleaner than broad regex for entity-shaped values, but
  defaulting every `MONEY`, `PERCENT`, `DATE`, or `CARDINAL` field to NER is a
  cross-consumer policy decision without enough empirical backing.
- **Raw callables in strategy params.** Rejected because callables are not
  JSON-safe, portable, or replay-stable.
- **Unvalidated `dict` configuration as the public contract.** Rejected because
  it weakens spec hashing, docs, and error messages.
- **Domain-specific patterns in extractx.** Rejected. Consumers own domain
  policy and can pass typed EntityRuler config.
- **Span-group support in v1.** Rejected as premature. `doc.ents` is sufficient
  for the initial strategy; span groups can be added later with a distinct
  contract.
- **Bundling a downloaded spaCy model.** Rejected. extractx may document model
  installation, but core tests and base installs must not require model assets.
