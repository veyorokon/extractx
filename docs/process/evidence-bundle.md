# Evidence Bundle

This is the standard worker completion report for lane work. It is an acceptance contract, not a narrative format.

Each lane may add lane-specific checklist items, but the base skeleton below is shared. Any non-zero failure terminates the bundle; report the failure and stop short of claiming completion.

## Template

```text
preflight
- pwd: <absolute path>
- branch: <git rev-parse --abbrev-ref HEAD>
- status: <git status summary>
- head: <git log -1 --oneline>

files changed
- <path>

implementation notes
- <load-bearing decision or "none">

test delta
- +N tests added, M passing total, K failing → 0 failing

proof
- uv sync: pass | fail
- uv run pytest: pass | fail
- uv run ruff check: pass | fail
- uv run pyright: pass | fail

drifts
- <out-of-scope observation, or "none">

pushback-or-omit
- <substantive pushback, or "none">

commit hash
- <final lane commit hash>
```

## Required Fields

- **preflight** — include `pwd`, `git rev-parse --abbrev-ref HEAD`, `git status`, and `git log -1 --oneline`.
- **files changed** — list every path touched by the thread.
- **implementation notes** — keep this short; include only load-bearing decisions.
- **test delta** — use the exact shape `+N tests added, M passing total, K failing → 0 failing`. A non-zero final failure count terminates the bundle.
- **proof** — report all four gates explicitly: `uv sync`, `uv run pytest`, `uv run ruff check`, `uv run pyright`. All four must pass for the thread to close.
- **drifts** — include out-of-scope observations the worker noticed but did not edit.
- **pushback-or-omit** — include substantive pushback or explicit `none`.
- **commit hash** — include the final commit on the lane branch.

## Pushback Rule

Substantive pushback means the worker names a contract gap, an ownership collision, or a write-scope contradiction. Substantive pushback re-opens the brief.

Cosmetic feedback means naming, ordering, wording, or test-shape preference. Cosmetic feedback may be logged in the evidence bundle, but it does not re-open the brief.
