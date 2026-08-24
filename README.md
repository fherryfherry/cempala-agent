```
██████╗ ███████╗ ███╗   ███╗ ██████╗  ██████╗  ██╗      ██████╗
██╔════╝ ██╔════╝ ████╗ ████║ ██╔══██╗ ██╔══██╗ ██║      ██╔══██╗
███████╗ █████╗   ██╔████╔██║ ██████╔╝ ███████║ ██║      ███████║
██╔═══╝  ██╔══╝   ██║╚██╔╝██║ ██╔═══╝  ██╔══██║ ██║      ██╔══██║
╚██████╗ ╚██████╗ ██║ ╚═╝ ██║ ██║      ██║  ██║ ███████╗ ██║  ██║
╚═════╝  ╚═════╝  ╚═╝     ╚═╝ ╚═╝      ╚═╝  ╚═╝ ╚══════╝ ╚═╝  ╚═╝
```

# CEMPALA

A Jira-like portal for running a "software team" made entirely of AI agents (PM, Lead Engineer,
Engineer, Designer, QA, Pentester). You create tickets, the agent team works them autonomously
inside a local repo folder, and you watch everything happen in real time through an activity feed
and streaming output.

The portal does not build its own coding agent — it assembles a prompt, hands it to an external
coding tool (`opencode`), and receives the result via a ` ```map ` block at the end of the agent's
reply.

The name comes from the *cempala*, the small wooden mallet a Javanese wayang *dalang* uses to tap
the puppet sticks and puppet box — setting the rhythm, cueing the music, and commanding the stage
without ever touching the puppets directly. You are the dalang: you set the story (tickets), the
AI agents do the acting, and CEMPALA is the mallet that makes the whole performance run.

See [`docs/00-overview.md`](docs/00-overview.md) for the full pitch and
[`docs/02-tsd.md`](docs/02-tsd.md) for the technical architecture.

## ⚠️ Security warning — read before running

- `opencode` is run with the **`--auto`** flag, meaning the agent **approves all permissions
  itself** — no human confirms any permission dialog.
- `--dir <repo_path>` only sets the **working directory**, it is **NOT a sandbox**. Nothing stops
  the agent from touching files outside that folder.
- Consequence: the agent can run **any command** with the privileges of the user running the
  backend.
- Therefore:
  - The backend **must** bind to `127.0.0.1` only. **Never** expose this portal to a network —
    that is the same as opening remote code execution.
  - Run it only on repos you trust, on a machine you control.
  - **Do not** put production secrets inside `repo_path`.
  - The `repo_path` validation in the API is a convenience, not a security boundary.

This is not an implementation detail you can ignore — it is a consciously accepted architectural
consequence (see [ADR-010](docs/06-adr.md)).

## Prerequisites

- The [`opencode`](https://opencode.ai) binary installed and authenticated:

  ```
  opencode auth login
  ```

  The backend shells out to this binary for every agent run and for the model list. LLM
  credentials are never stored by this portal.

## Setup from scratch

1. Install & authenticate `opencode`: `opencode auth login` (see [Prerequisites](#prerequisites)).
2. Set up the backend: `cd backend && uv venv --python 3.12 .venv && uv pip install -e ".[dev]"`
   (without `uv`: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`).
3. Set up the frontend: `cd frontend && npm install`.
4. `make migrate` — apply database migrations.
5. `make dev` — run backend (`:8000`) and frontend (`:3000`) together; `Ctrl+C` stops both.

## Running

```
make dev       # backend (uvicorn :8000) + frontend (next dev :3000)
make migrate   # alembic upgrade head
make test      # pytest
```

## Layout

```
backend/    FastAPI + SQLite (via SQLAlchemy/Alembic)
frontend/   Next.js App Router
storage/    Attachments (outside the agent repo_path, not source code)
docs/       Specification — read this first
```
