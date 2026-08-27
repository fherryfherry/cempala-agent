```
██████╗ ███████╗ ███╗   ███╗ ██████╗  ██████╗  ██╗      ██████╗
██╔════╝ ██╔════╝ ████╗ ████║ ██╔══██╗ ██╔══██╗ ██║      ██╔══██╗
███████╗ █████╗   ██╔████╔██║ ██████╔╝ ███████║ ██║      ███████║
██╔═══╝  ██╔══╝   ██║╚██╔╝██║ ██╔═══╝  ██╔══██║ ██║      ██╔══██║
╚██████╗ ╚██████╗ ██║ ╚═╝ ██║ ██║      ██║  ██║ ███████╗ ██║  ██║
╚═════╝  ╚═════╝  ╚═╝     ╚═╝ ╚═╝      ╚═╝  ╚═╝ ╚══════╝ ╚═╝  ╚═╝
```

# CEMPALA

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Coverage Status](https://coveralls.io/repos/github/fherryfherry/cempala-agent/badge.svg)](https://coveralls.io/github/fherryfherry/cempala-agent)

A Jira-like portal for running a "software team" made entirely of AI agents (PM, Lead Engineer,
Engineer, Designer, QA, Pentester). You create tickets, the agent team works them autonomously
inside a local repo folder, and you watch everything happen in real time through an activity feed
and streaming output.

The portal does not build its own coding agent — it assembles a prompt, hands it to an external
coding CLI (`opencode`, `claude`, `codex`, or `agy` — configurable per agent), and receives the
result via a ` ```map ` block at the end of the agent's reply.

The name comes from the *cempala*, the small wooden mallet a Javanese wayang *dalang* uses to tap
the puppet sticks and puppet box — setting the rhythm, cueing the music, and commanding the stage
without ever touching the puppets directly. You are the dalang: you set the story (tickets), the
AI agents do the acting, and CEMPALA is the mallet that makes the whole performance run.

See [`docs/00-overview.md`](docs/00-overview.md) for the full pitch and
[`docs/02-tsd.md`](docs/02-tsd.md) for the technical architecture.

## ⚠️ Security warning — read before running

- Every supported CLI (`opencode --auto`, `claude --permission-mode ...`,
  `codex --dangerously-bypass-approvals-and-sandbox`, `agy --dangerously-skip-permissions`) is run
  in its full-auto mode, meaning the agent **approves all permissions itself** — no human confirms
  any permission dialog, regardless of which tool an agent is configured with.
- The working-directory flag each CLI is given only sets **where** it runs, it is **NOT a
  sandbox**. Nothing stops the agent from touching files outside that folder.
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

### Want to access CEMPALA remotely? Use Tailscale, don't open a port

The backend must stay bound to `127.0.0.1` — never expose it directly to the public internet or a
LAN via `0.0.0.0` / port forwarding, since there is no auth (ADR-005) and `--auto` means anyone who
can reach the API can run arbitrary commands as your user.

If you want to check on CEMPALA from another device (phone, laptop, another room) without loosening
that bind, install [Tailscale](https://tailscale.com) on the machine running `make dev` and use
`tailscale serve` (not `tailscale funnel`, which exposes a service publicly):

```
tailscale serve --bg 3000    # frontend
tailscale serve --bg 8000    # backend (only if you need direct API access)
```

This proxies your existing `127.0.0.1`-bound ports to your private **tailnet** at
`https://your-machine.tailnet-name.ts.net`, reachable only from devices you've authenticated into
that tailnet — the app itself is never rebound to `0.0.0.0` and never touches the public internet.

## Prerequisites

CEMPALA itself (backend + `make dev`) is written and tested for **macOS/Linux** — the Makefile
uses `.venv/bin/...` shell paths. **On Windows, use WSL** for the backend/dev-runner steps; native
Windows PowerShell/CMD is only relevant below for installing the agent CLIs, which mostly ship
native Windows installers.

Install these before touching the repo:

- **Git** — to clone the repo.
  - Official: [git-scm.com/downloads](https://git-scm.com/downloads) (has native installers for
    macOS, Linux, and Windows).
  - macOS: `brew install git`. Linux: `apt install git` / `dnf install git`. Windows: use the
    official installer above, or inside WSL: `sudo apt install git`.
- **Python 3.11+** (3.12 recommended — that's what the setup command below pins). Check with
  `python3 --version`.
  - Official: [python.org/downloads](https://www.python.org/downloads/).
  - macOS: `brew install python@3.12`. Linux: `apt install python3.12` (or your distro's package
    manager). Windows: use python.org's installer, or (recommended) install inside WSL the same
    way as Linux.
- **[`uv`](https://docs.astral.sh/uv/getting-started/installation/)** (recommended) — a fast
  Python package/venv manager. Not strictly required: [Setup from scratch](#setup-from-scratch)
  below gives a plain `venv`/`pip` fallback if you'd rather not install it.
  - Official install script — macOS/Linux/WSL: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
    Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.
  - macOS: `brew install uv`.
- **Node.js** (v20 or newer — required by Next.js 16) and **npm**. Check with `node --version`.
  - Official: [nodejs.org](https://nodejs.org) (LTS installer, has macOS/Linux/Windows builds).
  - macOS: `brew install node`. Linux: use [nvm](https://github.com/nvm-sh/nvm) →
    `nvm install --lts` (distro repos often ship an outdated Node). Windows: the official installer
    above, or `winget install OpenJS.NodeJS.LTS`, or inside WSL via `nvm` as on Linux.
- **`make`** — used to run the dev/migrate/test commands.
  - macOS: preinstalled (or `xcode-select --install` if missing); brew alternative:
    `brew install make`.
  - Linux: preinstalled on most distros; otherwise `apt install make` / `dnf install make`.
  - Windows: not natively available — **use WSL** (`sudo apt install make` inside it), which is
    also required for the Makefile's shell paths to work at all.

Then, at least one agent CLI. Each agent picks one CLI tool (`tool_kind`) to run its work through;
the backend only shells out to whichever of these binaries an agent is actually configured with —
you only need to install and authenticate the ones you plan to use. LLM credentials are never
stored by this portal; each CLI manages its own auth.

- [`opencode`](https://opencode.ai) — also used for the model list (`GET /api/models`) regardless
  of which tool an agent uses.
  - Official install script — macOS/Linux: `curl -fsSL https://opencode.ai/install | bash`.
    Windows: see [opencode.ai/docs](https://opencode.ai/docs) for the native Windows method, or
    run the Linux script inside WSL.
  - macOS/brew: `brew install sst/tap/opencode`.
  - Authenticate: `opencode auth login`.
- [`claude`](https://claude.com/product/claude-code) (Claude Code):
  - Official install script — macOS/Linux/WSL: `curl -fsSL https://claude.ai/install.sh | bash`.
    Windows PowerShell: `irm https://claude.ai/install.ps1 | iex`. Windows CMD:
    `curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd`.
  - macOS/brew: `brew install --cask claude-code`. Windows: `winget install Anthropic.ClaudeCode`.
  - Authenticate: run `claude` and follow the browser login prompt.
- [`codex`](https://github.com/openai/codex) (OpenAI Codex CLI):
  - Official install script — macOS/Linux: `curl -fsSL https://chatgpt.com/codex/install.sh | sh`.
    Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"`.
  - Cross-platform via npm: `npm install -g @openai/codex`. macOS/brew: `brew install --cask codex`.
  - Authenticate: run `codex` and choose "Sign in with ChatGPT" (or configure an API key).
- `agy` (Google Antigravity CLI) — see [antigravity.google](https://antigravity.google) / Google's
  own setup docs for install and auth on each OS; no official brew formula at time of writing.

## Setup from scratch

1. Make sure Git, Python 3.11+, Node.js 20+, and `make` are installed (see
   [Prerequisites](#prerequisites)). **On Windows, do the rest of these steps inside WSL.**
2. Clone the repo and `cd` into it: `git clone <this-repo-url> && cd cempala`.
3. Install & authenticate at least one agent CLI (see [Prerequisites](#prerequisites)) — `opencode`
   is the simplest to start with: `opencode auth login`.
4. Run `./run.sh` — sets up the backend venv and frontend `node_modules` on first run (skipped on
   later runs), applies DB migrations, then starts backend (`:8000`) and frontend (`:3000`)
   together. `Ctrl+C` stops both. (Equivalent manual steps, if you'd rather not use the script:
   backend `uv venv --python 3.12 .venv && uv pip install -e ".[dev]"` — or without `uv`,
   `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"` — then frontend `npm install`,
   then `make migrate && make dev`.)
5. Open `http://localhost:3000` — the root page shows backend status. Create a workspace pointing
   at a real repo folder, add an agent (pick whichever CLI you authenticated), and create your
   first ticket.

## Running

```
./run.sh       # first-time setup (if needed) + migrate + backend & frontend together
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

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first — it
covers the dev setup, testing conventions, and the project's security constraints.
By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

CEMPALA is open-sourced software licensed under the [MIT license](LICENSE).
