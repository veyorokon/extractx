# CLAUDE.md

This repo uses [AGENTS.md](AGENTS.md) as the canonical operating doctrine and [CODEX.md](CODEX.md) as the filled-out repo-local guide.

**Read order when entering cold:**

1. [`AGENTS.md`](AGENTS.md) — generic seam / contract / thread / proof doctrine
2. [`CODEX.md`](CODEX.md) — repo-local snapshot, vocabulary, seams, workflow, debugging, forbidden shortcuts
3. [`docs/architecture.md`](docs/architecture.md) — the full system design (the north star)
4. [`docs/thread-orchestration.md`](docs/thread-orchestration.md) — the delegation model (coordinator vs worker threads)
5. `docs/adr/NNNN-*.md` — decisions relevant to your work
6. `docs/tasks/<slug>.md` — your own brief, if you were handed one

The content that would otherwise live in this file lives in `CODEX.md` — keep them consistent. If they ever drift, `CODEX.md` is authoritative for repo-local content and this file is a pointer.

## Claude-specific rules

- **Voice:** match the project's tone. Vahid's style is lowercase, no apostrophes in conversational prose, direct. Technical docs may use normal capitalization where it aids legibility (tables, headers), but prose stays low-key.
- **Git rules:** never mention Claude, Anthropic, Codex, OpenAI, or any AI tool in commit messages, PR titles, PR descriptions, or `Co-Authored-By` lines. Git artifacts should read as if written by a human engineer.
- **Hooks:** never use `--no-verify`, `--no-gpg-sign`, or similar shortcuts unless the user explicitly requests it. if a hook fails, fix the underlying issue.
- **Tool use:** prefer Grep / Glob / Read / Edit / Write over shelling to `cat` / `grep` / `find` / `sed`. use Bash only for shell-only operations (mkdir, cp, git, uv, pytest).
- **Shared-state actions:** commits, pushes, merges, deploys, dependency changes, any write to a service beyond the local filesystem — confirm with the user before executing unless the current instruction clearly authorizes that specific action.
- **Do not delete or rewrite** content the user has not asked you to touch. when in doubt, ask.
- **Subagent delegation:** when handing a bounded implementation thread to a worker, produce the brief in `docs/tasks/<slug>.md` first, then relay the brief. do not hand the worker conversational context — it starts cold.

## What Claude owns in this repo

The delegation model (see `docs/thread-orchestration.md`) puts the main agent at the orchestration layer:

- thread selection and ordering
- seam decisions
- benchmark and transcript interpretation
- global contract integrity
- final acceptance of worker output
- integration of concurrent work

Bounded implementation and research work is handed to worker agents via `docs/tasks/`. Before delegating:

1. the thread must have a desired state, initial seam, final seam, and proof target
2. the write scope must be reasonably isolated
3. the task brief must be self-contained (assume the worker starts cold)
4. anti-patterns the worker must avoid must be named explicitly
