# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

**Pre-implementation.** This repo contains only `docs/` — no code, no git repo, no dependencies.
The planning docs are complete and approved (v0.2). Implementation starts at MAP-001.

Read `docs/00-overview.md` first, then `docs/04-tasks.md` for what to build next.
`docs/06-adr.md` explains why the architecture is the way it is — read it before proposing
structural changes, since several obvious-looking simplifications were already considered and
rejected there for stated reasons.

## Local dev safety — don't run destroyer commands

`backend/map.db`, `backend/map.db-wal`/`-shm`, and `storage/attachments/` are **not disposable
test fixtures** even though they're gitignored — they can hold real dogfooding data from a
`make dev` session that's already running. Before touching any of them:

- Check first: `lsof -i :8000` / `ps` for an already-running backend, `lsof <file>` for open
  handles. If something's running, its DB is live even if you don't see it in `git status`.
- Never run `rm`, `mv`, `alembic downgrade`, or any other destructive/mutating command against
  `backend/map.db` or `storage/` to "reset" or "smoke test" something — ask first.
- For manual verification, use a throwaway DB/repo path (exactly what the test suite's fixtures
  already do — a fresh `sqlite+aiosqlite:///:memory:` or a temp file), never the shared dev file.

## Commands

None exist yet. MAP-005 creates the `Makefile` that provides:

```
make dev       # backend (uvicorn :8000) + frontend (next dev :3000)
make migrate   # alembic upgrade head
make test      # pytest
```

Single test once pytest exists: `cd backend && pytest tests/test_report.py::test_missing_block -q`

**External prerequisite:** the `opencode` binary must be installed and authenticated
(`opencode auth login`). The backend shells out to it for every agent run and for the model list.
`GET /api/health` reports whether it was found.

## Architecture

A Jira-like portal where AI agents (PM, Lead, Engineer, Designer, QA, Pentester) work tickets
autonomously inside a real local repo. Next.js frontend → FastAPI backend → SQLite, with agent
runs as `asyncio.Task`s in the backend process.

Four things drive most of the design and are not apparent from any single file:

### 1. The portal does not implement a coding agent

Every run is one `opencode` subprocess (ADR-006). There is no tool-calling loop, no filesystem
tools, no LLM client, and no `llm/` package. The portal assembles a prompt, spawns
`opencode run --format json --dir <repo_path> --auto`, maps its stdout JSON to events, and reads
the result back.

Consequences worth internalizing: the portal never touches files inside `repo_path`; it stores no
LLM credentials (that's `opencode auth`); the model dropdown comes from `opencode models`, not
from any provider API. Do not add filesystem or shell tools to the backend — that direction was
explicitly reversed in v0.2.

### 2. Agents report back through a fenced ` ```map ` block

Because opencode is a black box, it cannot call the ticket API. Each prompt ends with a contract
requiring the agent to close its answer with one YAML block: `status`, `mention`, `summary`, and
optionally `tickets[]`. `core/report.py` parses the **last** such block and executes it.

This is the linchpin and the biggest technical risk in the MVP (ADR-009). Rules that matter:

- Role permissions (`which statuses`, `may create tickets`) are enforced **in the parser**, not
  trusted to the prompt. See `docs/03-agent-design.md` §3 for the matrix.
- A missing or malformed block must block the ticket and post a system comment containing the
  agent's raw tail output. Never guess intent, never fail silently.
- Full agent output is still persisted as events; the block only determines *actions*.

If format compliance turns out poor in dogfooding (MAP-033 measures it), the planned replacement
is an MCP server exposing ticket tools — not a heuristic parser.

### 3. The `event` table is the single source of activity

Everything persists to `event` **before** being broadcast to SSE subscribers (ADR-008). The live
feed and post-refresh replay read the same table, so what you watch and what you can re-read are
always identical. With agents as black boxes, this trace is the only way to understand a failed
run — including the assembled prompt, stored on the `run_started` event.

### 4. Guardrails are the only brakes left

Losing the in-process loop meant losing step caps and per-tool control. What remains: run timeout,
cost per run, cost per ticket, handoff depth, loop detector, `max_concurrent_runs` (default 3,
because each run is a full subprocess), and `ticket_not_in_active_sprint` (a ticket with no sprint,
or one that isn't the workspace's active sprint, can't be scheduled — except for whichever roles
`workspace.sprint_creator_roles` trusts to plan sprints, default PM-only, since those roles need to
be reachable on any ticket to do that triage). Every guardrail trip **must** leave a system comment
naming which guardrail fired — there is no silent failure path.

The kill switch is a security control, not a convenience: it must actually kill child processes,
verified with `ps`, not just mark rows in the DB.

## Security posture

`opencode --auto` approves all permissions, and `--dir` sets a working directory, **not** a
sandbox. An agent can run arbitrary commands with the backend user's privileges. This was accepted
deliberately (ADR-010), which makes three things non-negotiable:

- Backend binds `127.0.0.1`. There is no auth (ADR-005), so exposing it to a network means handing
  out remote code execution.
- The warning must be visible in README and on the settings page, and must not be dismissible.
- `repo_path` validation in the API is a convenience check, not a security boundary. Don't
  describe it as one.

## Build order

The sequencing in `docs/05-roadmap.md` encodes decisions, not just convenience:

- **M2:** parser (MAP-018) → prompt builder (MAP-019) → opencode adapter (MAP-020). The return
  contract is the hard part; building the adapter first means discovering the wrong contract shape
  after everything is wired to it.
- **M3:** guardrails, loop detector, and kill switch (MAP-027/028/031) must be finished and tested
  *before* the handoff engine and autonomous flow (MAP-029/030) are switched on.

Test the opencode adapter against a fake binary — a script that prints sample JSON — rather than
real LLM calls.

## Conventions

- Docs are in Indonesian; code, identifiers, and commit messages in English.
- Ticket IDs `MAP-NNN` in `docs/04-tasks.md` are the unit of work. Reference them in commits.
- When a doc decision changes, update the doc in the same change — the docs are the spec, and
  v0.2 already rewrote v0.1 in place rather than accumulating stale text.
