# extractx

a schema-first grounded extraction engine.

extractx processes **one already-scoped document at a time**. Given a
document and a pydantic schema, it produces grounded field observations,
typed extraction instances, source evidence, and replay artifacts.

Production extraction follows a grounded-classification pattern:
deterministic producers find candidate evidence with source spans, then
LLM-backed classifiers choose among those bounded candidate IDs. The LLM
does not author raw values, normalized values, evidence spans, or domain
identity. Formatting, normalization, validation, and sealing remain
deterministic after the observation decision.

Deterministic instance assignment is intentionally not the production
multi-instance path. `Cardinality.ONE` uses one synthetic extraction
instance for single-instance fixtures, CI baselines, and simple
one-instance documents. `Cardinality.MANY` requires an instance candidate
strategy binding plus an instance proposer binding. The candidate strategy
builds the bounded menu; the LLM-backed proposer selects from it.

It does **not** own downstream domain identity. Consumer systems decide
whether an extraction instance maps to a business entity such as a
tax-return `return_id`, customer account, case file, or invoice record.
That correlation layer belongs outside extractx, using
extraction instances, evidence, observations, and replay as inputs.

The intended shape is:

```text
extractx:
  document + schema -> Extraction + ReplayArtifact

consumer:
  Extraction + domain rules -> business entities / facts / state
```

The planned dry-run surface is an inspectable extraction plan: static dry-run
shows compiled bindings and required capabilities; grounded dry-run also shows
deterministic field and instance candidate menus without calling an LLM or
writing replay.

see [`docs/architecture.md`](docs/architecture.md) for the system design, [`CODEX.md`](CODEX.md) for the repo-local operating guide, and [`AGENTS.md`](AGENTS.md) for the generic working doctrine.

## install

`uv sync`

Optional LLM selector/proposer support lives behind the `pydantic_ai` extra:

```toml
[project]
dependencies = ["extractx[pydantic_ai]"]

[tool.uv.sources]
extractx = { path = "../extractx", editable = true }
```

Declare the extra on the dependency string; keep the editable path in
`tool.uv.sources`. If your workspace manager cannot compose extras with path
sources, add `pydantic-ai>=1.99.0` directly in the consuming project.

Optional spaCy NER candidate generation lives behind the `spacy` extra:

```toml
[project]
dependencies = ["extractx[spacy]"]
```

`model_id="en"` uses `spacy.blank("en")`, which is enough for configured
`EntityRuler` patterns and core tests. If you want a pretrained spaCy model,
install that model separately and pass its package name as `model_id`.

## candidate strategies

`ValueKind` says what kind of value a field expects. Candidate strategies say
where candidate evidence comes from. Strategies are explicit bindings; extractx
does not silently attach NER or regex based on `ValueKind`.

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

NER candidates are text candidates. They flow through the same candidate set,
filter, selector, validation, evidence, and replay contracts as regex
candidates.

## source spans

`Evidence.source_span.byte_start` and `byte_end` are byte offsets, not Python
string indexes. For `text_anchor_space="source_bytes"`, they index the UTF-8
source bytes stored under `source_ref`; for `text_anchor_space="normalized_text"`,
they index `DocumentView.normalized_text.encode("utf-8")`.

When highlighting inside a Python `str`, convert the byte span first:

```python
from extractx import slice_utf8_byte_span, utf8_byte_span_to_char_range

start, end = utf8_byte_span_to_char_range(document_text, evidence.source_span)
assert document_text[start:end] == evidence.evidence_text
assert slice_utf8_byte_span(document_text, evidence.source_span) == evidence.evidence_text
```

Use this projection only when the supplied `document_text.encode("utf-8")`
matches the bytes addressed by the span.

## value kinds and runtime types

`ValueKind` is not a parser. It is a semantic tag on a Python type. The Python
annotation controls the normalized runtime shape.

```python
from typing import Annotated

from extractx import ValueKind

count_text = Annotated[str, ValueKind.CARDINAL]
count_int = Annotated[int, ValueKind.CARDINAL]
```

If source evidence says `"20 items"`, `count_text` can normalize to the string
`"20 items"`. `count_int` asks pydantic to produce an `int`; if the raw
candidate text is still the full phrase, validation fails unless the annotation
contains a pre-coercion parser or the candidate strategy emits only the numeric
span.

For pydantic-backed schemas, put phrase-to-type parsers in the annotation with
`BeforeValidator` so extractx's isolated field validation sees them before
pydantic type coercion:

```python
from typing import Annotated

from pydantic import BeforeValidator

from extractx import ValueKind


def parse_count(value: object) -> object:
    if isinstance(value, str) and value.startswith("20 "):
        return 20
    return value


count_int = Annotated[int, BeforeValidator(parse_count), ValueKind.CARDINAL]
```

Class-level pydantic `field_validator`s run after extractx's pydantic coercion
step in the pydantic-backed path, so they should validate already-coerced
values rather than parse raw evidence phrases.

Use `ValueKind.CARDINAL` for count-like quantities. Use the Python type and
annotation-level validators to define the exact normalized value.

## object validators

Use object validators for cross-field checks within one extracted object. They
return structured issues instead of raising exceptions, so execution strategies
can later decide whether to retry, repair, accept with warning, or fail.
The current registration form is schema-method based; use a decorated
`@staticmethod` and call shared helper functions from that method when multiple
schemas need the same rule.

```python
from datetime import date
from typing import Annotated

from pydantic import BaseModel

from extractx import ObjectIssue, ValueKind, extract_field, extractx_object_validator


class ScheduledEvent(BaseModel):
    start_date: Annotated[date, ValueKind.DATE] = extract_field(
        description="event start date",
    )
    end_date: Annotated[date, ValueKind.DATE] = extract_field(
        description="event end date",
    )

    @staticmethod
    @extractx_object_validator(implicates=("start_date", "end_date"))
    def dates_ordered(values, evidence):
        del evidence
        if values["end_date"] < values["start_date"]:
            return ObjectIssue(
                code="date_order",
                reason="end_date must be on or after start_date",
            )
        return None
```

Object validators run after field validation and resolution. Warning issues are
diagnostic; error issues block the instance and surface as validation
negatives with structured `object_issues`. Error issues do not remove the
instance from `Extraction.instances`; they flip the instance to `partial` and
append a validation negative.

If an `ObjectIssue` omits `implicates`, extractx fills them from the
`@extractx_object_validator(implicates=...)` metadata. If the returned issue
sets `implicates`, that narrower issue-specific set is preserved.

Use `ExecutorPolicy(strategy="iterative")` to enable the bounded repair path for
single-instance specs. The executor first runs the normal bounded extraction. If
field validation fails, extractx retries that field once with the pydantic or
manual validation reason in `ContextPack.retry_feedback`. It then resolves and
runs object validators; if object validators emit error issues, extractx retries
only the implicated fields once with the issue reasons in
`ContextPack.retry_feedback`, then validates the object again. Candidate sets are
not mechanically filtered during either retry; the same validators still own
truth after repair.

## test

`uv run pytest`
