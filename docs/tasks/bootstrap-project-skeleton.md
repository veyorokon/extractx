# Task: bootstrap the extractx project skeleton

*First task for this repo. Establishes the scaffolding that every subsequent seam implementation builds on. No extraction logic lands in this task — only the structure, tool configs, and empty typed module tree.*

## Read first

the exec agent starts cold. read these before doing anything:

- [`AGENTS.md`](../../AGENTS.md) — generic working doctrine (seams, contracts, threads, proof doctrine)
- [`CODEX.md`](../../CODEX.md) — repo-local guide: canonical nouns, seam summary, workflow rules, forbidden shortcuts, debugging workflow
- [`CLAUDE.md`](../../CLAUDE.md) — tool policies (voice, git rules, hook rules)
- [`docs/architecture.md`](../architecture.md) — the full system design. **read §16 project layout in full before starting**, and skim §2 first principles + §4 canonical vocabulary + §5 seam map for context
- [`docs/thread-orchestration.md`](../thread-orchestration.md) — you are a bounded worker on this task; the main agent owns the critical path

## Goal

stand up the extractx repo as a modern `uv`-managed python library with the full empty module skeleton from `docs/architecture.md` §16. the repo must be in a state where `uv sync`, `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, and `uv run pyright` all pass cleanly on an empty collection.

**"done" in one sentence:** a freshly cloned repo runs `uv sync && uv run pytest` with zero errors, collects at least one passing smoke test, and every directory from `docs/architecture.md` §16 exists with a typed `__init__.py` (even if empty).

## Scope

numbered investigation and implementation areas. do each in order.

### 1. top-level uv project configuration

create `pyproject.toml` with:

- **`[project]`**
  - `name = "extractx"`
  - `version = "0.1.0"`
  - `description = "a schema-first grounded proposal engine"`
  - `readme = "README.md"`
  - `authors = [{ name = "Vahid Eyorokon", email = "veyorokon@gmail.com" }]`
  - `requires-python = ">=3.12"`
  - `license = { text = "MIT" }` (provisional — revisit if needed)
  - `dependencies = ["pydantic>=2.0,<3", "opentelemetry-api>=1.20", "opentelemetry-sdk>=1.20", "msgspec>=0.18"]`
- **`[project.optional-dependencies]`**
  - `pydantic_ai = ["pydantic-ai>=0.0.13"]` — default llm-backed Selector per ADR-0002; also provides the message-history serialization that powers `.interview()`
  - `unstructured = ["unstructured>=0.12"]` — **provisional.** the default document adapter is under research (`docs/tasks/select-default-document-adapter.md`). this entry may be replaced or removed based on the finding. include it in the skeleton so the `extras/unstructured/` module tree exists; do not implement the adapter.
  - `modal = ["modal>=0.60"]`
  - `ray = ["ray>=2.9"]`
  - `all = ["extractx[pydantic_ai,unstructured]"]` (omit remote executors from `all`)
  - `dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.6", "pyright>=1.1.380", "pre-commit>=3.5"]`
- **`[dependency-groups]`** (PEP 735 — required so that bare `uv sync` installs dev tools; area 6 step 1 is literal `uv sync` and subsequent steps run `ruff`/`pyright`/`pytest` via `uv run`, which need them present)
  - `dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.6", "pyright>=1.1.380", "pre-commit>=3.5"]` (mirror of `[project.optional-dependencies].dev`; retain the optional-dependencies entry so `pip install extractx[dev]` still works)
- **`[project.scripts]`**
  - `extractx = "extractx.cli:main"`
- **`[build-system]`**
  - `requires = ["hatchling"]`
  - `build-backend = "hatchling.build"`
- **`[tool.hatch.build.targets.wheel]`**
  - `packages = ["src/extractx"]`
- **`[tool.pytest.ini_options]`**
  - `testpaths = ["tests"]`
  - `asyncio_mode = "auto"`
  - `addopts = "-ra --strict-markers --strict-config"`
- **`[tool.ruff]`**
  - `line-length = 100`
  - `target-version = "py312"`
- **`[tool.ruff.lint]`**
  - `select = ["E", "F", "I", "UP", "B", "SIM", "N"]`
- **`[tool.pyright]`**
  - `include = ["src"]`
  - `pythonVersion = "3.12"`
  - `typeCheckingMode = "strict"`
  - `reportMissingTypeStubs = false`

create `.python-version` containing `3.12`.

### 2. source layout

create the full directory tree per `docs/architecture.md` §16, but **under `src/extractx/`** (not bare `extractx/` — we use the src layout):

```
src/extractx/
  __init__.py                # re-exports tier-1 end-user types (see area 3)
  api.py                     # stub: async def run_extraction(...) -> ExtractionResult — raises NotImplementedError
  types.py                   # stub: will eventually re-export Money, Percent, Date, Org, etc.
  core/
    __init__.py
    contracts.py
    objects.py
    outcomes.py
    anchors.py
    cardinality.py
    value_kinds.py
    versions.py
    dependencies.py
    exceptions.py            # SpecError, CapabilityError, InfrastructureError, InterviewError class stubs (empty bodies, type-correct)
  schema/
    __init__.py
    types.py
    extract_field.py
    from_pydantic.py
    to_pydantic.py
    metadata.py
    validators.py
    inference.py
  source/
    __init__.py
    document_view.py
    adapters/
      __init__.py
      html.py
      pdf.py
      text.py
  candidates/
    __init__.py
    candidate_set.py
    generators/
      __init__.py
      regex.py
      ner.py
      clause.py
      table.py
      hybrid.py
    sorters/
      __init__.py
      relevance.py
      layout.py
    grounded/
      __init__.py
      neural.py
  selection/
    __init__.py
    selector.py
    context_pack.py
    algorithmic/
      __init__.py
    llm/
      __init__.py
    prompts/
      __init__.py
      base.py
      selection.py
      grounded.py
  proposals/
    __init__.py
    adapter.py
    validation.py
    provenance.py
  instances/
    __init__.py
    planners/
      __init__.py
      structural.py
      graph.py
      neural.py
    resolvers/
      __init__.py
      deterministic.py
      graph.py
      neural.py
    state.py
    plan.py
    grouping.py
    precedence.py
    boundary.py
  replay/
    __init__.py
    artifact.py
    writer.py
    reader.py
    fixtures.py
    comparison.py
  execution/
    __init__.py
    executor/
      __init__.py
      protocol.py
      serial.py
      async_.py
    strategies/
      __init__.py
      independent.py
      iterative.py
    policy.py
    runtime.py
    budget.py
    reporter.py
    manifest.py
  extras/
    __init__.py
    pydantic_ai/
      __init__.py
      selector.py                # PydanticAISelector stub
      interview.py               # InterviewTranscript + .interview() impl stub
    unstructured/
      __init__.py
      adapter.py
    modal/
      __init__.py
      executor.py
    ray/
      __init__.py
      executor.py
  cli/
    __init__.py
    run.py
    replay.py
    inspect.py
```

every `.py` file should be syntactically valid and type-check under strict pyright. use `...` (ellipsis body) or `raise NotImplementedError` for function bodies. top-level stubs that pyright must accept:

- `exceptions.py` — declare `SpecError`, `CapabilityError`, `InfrastructureError`, `InterviewError` as empty `Exception` subclasses (all four are tier-1 end-user public per `docs/architecture.md` §10 and §13 exception taxonomy)
- `api.py` — declare `async def run_extraction(document, spec, runtime, policy) -> "ExtractionResult"` that raises `NotImplementedError`. use string forward refs for types not yet defined.
- `cli/run.py` — declare `def main() -> None` that raises `NotImplementedError` (wired to the `extractx` script entry point)

do **not** define canonical types (`DocumentView`, `ExtractionSpec`, `FieldSpec`, etc.) in this task. just create the module files with a module docstring referencing which section of `docs/architecture.md` they implement.

### 3. public `__init__.py` surface

`src/extractx/__init__.py` should be a minimal re-export surface. at this stage, only include what exists (which is mostly nothing). it should:

- define `__version__ = "0.1.0"`
- declare an empty `__all__: list[str] = []`
- add a module docstring stating: "public tier-1 end-user surface. tier-2 plugin types are imported from their canonical modules directly (e.g. `from extractx.core.contracts import Selector`)."

leave the actual re-exports for a later task once the canonical objects exist.

### 4. test skeleton

create `tests/` with subdirectories per `docs/architecture.md` §16:

```
tests/
  __init__.py
  conftest.py                # empty but present
  contracts/__init__.py
  integration/__init__.py
  smoke/__init__.py
  invariant/__init__.py
  replay/__init__.py
  determinism/__init__.py
  strategies/__init__.py
  schema/__init__.py
  cardinality/__init__.py
  precedence/__init__.py
  lifecycle/__init__.py
  prompts/__init__.py
  smoke/test_import.py       # single smoke test (see below)
```

`tests/smoke/test_import.py` contains one passing test:

```python
"""smoke test — verifies the package is importable and the public API surface loads."""

import extractx


def test_package_imports() -> None:
    """the package loads without errors and exposes a version."""
    assert hasattr(extractx, "__version__")
    assert extractx.__version__ == "0.1.0"


def test_run_extraction_is_exposed_but_unimplemented() -> None:
    """run_extraction exists at the public surface but raises NotImplementedError."""
    from extractx.api import run_extraction
    assert callable(run_extraction)
```

this is the only test that needs to pass in this task. it proves the skeleton is coherent.

### 5. tool configuration files

create at the repo root:

- **`.pre-commit-config.yaml`** — hooks for ruff (check + format) and pyright, both run on commit. pin each hook to an **exact rev** (commit sha or release tag) that is current-latest-stable at authoring time. do not use floating refs like `main`, `stable`, or `latest`. future version bumps are explicit maintenance commits, not silent drift on re-install. include `pre-commit-hooks`, `astral-sh/ruff-pre-commit`, and `RobertCraigie/pyright-python` (the canonical community pre-commit hook for pyright — `microsoft/pyright` does not publish a `.pre-commit-hooks.yaml` manifest).
- **`.gitignore`** — standard python + uv + macOS ignores. start with:
  ```
  __pycache__/
  *.py[cod]
  *$py.class
  *.egg-info/
  .venv/
  .uv-cache/
  .pytest_cache/
  .ruff_cache/
  .pyright/
  dist/
  build/
  .DS_Store
  ```
- **`README.md`** — short stub:
  ```markdown
  # extractx

  a schema-first grounded proposal engine.

  see [`docs/architecture.md`](docs/architecture.md) for the system design, [`CODEX.md`](CODEX.md) for the repo-local operating guide, and [`AGENTS.md`](AGENTS.md) for the generic working doctrine.

  ## install

  `uv sync`

  ## test

  `uv run pytest`
  ```

### 6. verify the skeleton

run, in order, and confirm each passes:

1. `uv sync`
2. `uv lock` (confirms the lockfile is stable)
3. `uv run ruff check .`
4. `uv run ruff format --check .`
5. `uv run pyright`
6. `uv run pytest`

all six must exit 0. if any fails, fix the underlying cause — do not suppress with `# type: ignore`, `# noqa`, or config exclusions unless there is a genuine reason and you document it inline.

### 7. first commit

stage and commit the scaffolding with the message:

```
bootstrap project skeleton per docs/architecture.md §16

scaffolds the full empty typed module tree, tool configs (ruff, pyright,
pre-commit), pyproject.toml with extras, and a single smoke test that
confirms the package imports. no seam implementations — that is
delegated to per-seam tasks downstream.
```

do **not** push. the coordinator pushes after review.

## Guardrails

- **no seam implementations** — do not implement any canonical object, protocol, or function body beyond `NotImplementedError` stubs and type-correct empty-class declarations. implementation work is delegated to per-seam tasks.
- **no dependency additions** beyond the list in area 1. if you think something is missing, flag it in a "questions" section at the end of your output; do not add it.
- **no architectural decisions** — the architecture is in `docs/architecture.md`. if something is unclear, flag it; do not invent.
- **no import from `../extractx-old/`** — the old repo made several anti-pattern choices we are rebuilding against. reference material only, and only if explicitly useful.
- **no shortcuts** — do not use `--no-verify` on the commit, do not disable strict pyright for convenience, do not suppress ruff errors with `# noqa` unless there is a specific documented reason.
- **no git push, no PR, no branch creation** — commit to the current branch (likely `main` on an empty repo). coordinator handles branching policy.
- **no AI attribution** in commit messages or file headers (see `CLAUDE.md` git rules).
- **do not modify `docs/`, `AGENTS.md`, `CLAUDE.md`, or `CODEX.md`** — those are coordinator-owned. if you think one needs a change, flag it in your output.

## Deliverable

the extractx repo in the state defined by the success criteria below. specifically:

- `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `.pre-commit-config.yaml`, `README.md` at repo root
- full `src/extractx/` tree per §16
- full `tests/` tree with subdirs and one passing smoke test
- one commit with the scaffolding, on the current branch, not pushed

include in your final output to the coordinator:

- the list of commands run and their exit codes
- any places where you deviated from the brief (with reason)
- any gaps you flagged (see guardrails)
- a one-paragraph summary of what the next task should probably be (your best read as the worker who just touched the scaffold)

## Success criteria

each is testable; the output either meets it or doesn't.

- `uv sync` completes without error
- `uv lock` is stable (running it twice produces no changes)
- `uv run ruff check .` exits 0 with zero findings
- `uv run ruff format --check .` exits 0 with zero files needing reformat
- `uv run pyright` exits 0 in strict mode with zero errors
- `uv run pytest` exits 0 and collects at least the `tests/smoke/test_import.py` tests (both passing)
- every directory listed in area 2 exists with an `__init__.py`
- every `.py` file parses and type-checks under strict pyright
- `extractx.__version__ == "0.1.0"` on import
- `from extractx.api import run_extraction` succeeds; calling it raises `NotImplementedError`
- `from extractx.core.exceptions import SpecError, CapabilityError, InfrastructureError, InterviewError` succeeds
- the commit exists on the current branch, with no AI attribution, containing only scaffolding
- no dependency was added beyond the list in area 1
- no code outside `src/extractx/`, `tests/`, and the top-level config files was created or modified

## Downstream consequences

- this task unblocks every per-seam implementation task that follows
- the next likely task is **seam A (DocumentAdapter)** — smallest proof surface, no soft compute, and the foundation for every candidate generator. coordinator will draft the brief based on `docs/architecture.md` §7 seam A
- or potentially **seam B (ExtractionSpec.from_pydantic)** — the second-smallest proof surface; unblocks all schema-layer work
- any deviations or flagged gaps from this task may produce ADRs before the next implementation task lands
