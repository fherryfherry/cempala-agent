# Contributing to CEMPALA

Thanks for considering contributing! CEMPALA is a Jira-like portal where AI agents work
tickets autonomously inside a real local repo. This document explains how to set up a dev
environment, how to make changes, and what the project's conventions are.

## Code of Conduct

By participating in this project you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Prerequisites (full details in the [README](README.md#prerequisites)):

- Git
- Python 3.11+ (3.12 recommended)
- [`uv`](https://docs.astral.sh/uv/) (recommended; plain `venv`/`pip` also works)
- Node.js 20+ and npm
- `make`
- At least one agent CLI: `opencode`, `claude`, `codex`, or `agy` (authenticated)

One-shot setup and run:

```sh
./run.sh        # first-time setup + migrate + backend (:8000) + frontend (:3000)
```

Manual equivalents (or to run them separately):

```sh
make dev        # backend (uvicorn :8000) + frontend (next dev :3000)
make migrate    # alembic upgrade head
make test       # pytest
```

Open `http://localhost:3000`. The root page shows backend status. Create a workspace
pointing at a real repo folder, add an agent (pick whichever CLI you authenticated), and
create your first ticket.

## Before you start

- Read [`docs/00-overview.md`](docs/00-overview.md) first, then
  [`docs/04-tasks.md`](docs/04-tasks.md) for the build order.
- Read [`docs/06-adr.md`](docs/06-adr.md) before proposing structural changes. Several
  obvious-looking simplifications were already considered and rejected there for stated
  reasons — in particular, the portal deliberately does **not** implement its own coding
  agent, filesystem tools, or an `llm/` package (ADR-006), and role permissions are
  enforced in the ` ```map ` block parser, not trusted to the prompt (ADR-009).

## Security — please read

The portal runs agent CLIs in full-auto mode (`opencode --auto`, `claude --permission-mode ...`,
`codex --dangerously-bypass-approvals-and-sandbox`, `agy --dangerously-skip-permissions`).
The working-directory flag is **not** a sandbox: an agent can run arbitrary commands with
the privileges of the user running the backend.

Therefore, when developing or testing:

- Never run the backend bound to anything but `127.0.0.1`.
- Never put production secrets inside a `repo_path`.
- **Never run destructive commands against `backend/map.db`, `backend/map.db-wal`/`-shm`,
  or `storage/attachments/`** — these can hold real data from a live `make dev` session.
  Check `lsof -i :8000` / `ps` for a running backend first; for manual verification use a
  throwaway DB (a fresh `sqlite+aiosqlite:///:memory:` or a temp file), exactly like the
  test suite's fixtures.
- Do not add filesystem or shell tools to the backend — that direction was explicitly
  reversed in v0.2 (see ADR-006).

## Making changes

1. Fork the repo and clone your fork, or create a feature branch:

   ```sh
   git checkout -b feat/your-feature     # or fix/your-fix
   ```

2. Make your change. Follow the conventions below.
3. Run the tests: `make test`. To run a single test:

   ```sh
   cd backend && pytest tests/test_report.py::test_missing_block -q
   ```

4. For frontend changes, run `npm run lint` in `frontend/`.
5. Commit with a concise message referencing the relevant ticket, e.g.
   `feat: add X (MAP-012)`. See [Committing](#committing).
6. Push your branch and open a pull request. Fill out the PR template — the checklist is
   the definition of done.

### Testing agents without real LLM calls

Test the opencode adapter against a fake binary — a script that prints sample JSON — rather
than real LLM calls. Guardrails, the loop detector, and the kill switch must be finished
and tested **before** the handoff engine and autonomous flow are switched on (that order is
deliberate; see `docs/05-roadmap.md`).

## Conventions

- Docs are in English; code, identifiers, and commit messages in English.
- Ticket IDs `MAP-NNN` in `docs/04-tasks.md` are the unit of work. Reference them in commits.
- **When a doc decision changes, update the doc in the same change.** The docs are the
  spec — `docs/06-adr.md` already rewrote v0.1 in place rather than accumulating stale text.

## Committing

- Keep commits small and focused; one logical change per commit.
- Use the conventional prefix: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`.
- Reference the ticket: `feat: add X (MAP-012)`.
- Never commit secrets, `.env` files, `*.db` files, or `node_modules`.

## Reporting bugs

Open an issue using the bug report template. Include: OS, agent CLI and version, backend
version (from `docs/07-dogfood-report.md` or the changelog), the `repo_path` setup, and —
most importantly — the relevant events from the activity feed (agent runs are black boxes;
the persisted event trace is the only way to understand a failed run).

For security vulnerabilities, do **not** open a public issue — see
[SECURITY.md](SECURITY.md).

## Questions

Ask in the repo's Discussions, or open an issue. If you are unsure whether a change fits
the project's direction, open a discussion first — the docs describe a deliberately
opinionated architecture.
