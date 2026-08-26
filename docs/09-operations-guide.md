# Operations Guide — CEMPALA Multi-Agent Portal

Version 0.2 · MVP · 2026-08-22

> For developers and operators. For end-user documentation (how to use the portal), see the in-app UI and [01-prd.md](01-prd.md).

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | ≥3.11 | Backend |
| Node.js | ≥18 | Frontend |
| opencode | any | Must be installed and authenticated |
| SQLite | (built-in) | No separate install needed |

Install opencode and authenticate:
```bash
# Install opencode (see https://opencode.ai)
# Then authenticate — this is REQUIRED before the portal works:
opencode auth login
```

---

## Setup (Local Development)

### 1. Clone / navigate to the repo

```bash
cd /path/to/multi-agent
```

### 2. Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env           # edit if needed
```

### 3. Frontend setup

```bash
cd frontend
npm install
```

### 4. Initialize database

```bash
cd backend
alembic upgrade head
```

### 5. Run

```bash
make dev
```

This starts:
- Backend: `uvicorn` on `http://127.0.0.1:8000`
- Frontend: `next dev` on `http://localhost:3000`

Or manually:
```bash
# Terminal 1
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2
cd frontend && npm run dev
```

### 6. Verify

```
curl http://127.0.0.1:8000/api/health
# → {"status":"ok","opencode":"1.x.x"}  or "opencode": null if not authenticated
```

Open `http://localhost:3000` in your browser.

---

## Configuration

### Environment Variables (backend/.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./map.db` | SQLite database |
| `STORAGE_DIR` | `../storage` | Where attachments are stored |
| `CORS_ORIGINS` | `http://localhost:3000` | Allowed frontend origin |
| `OPENCODE_BIN` | `opencode` | Path to opencode binary |
| `OPENCODE_STREAM_LIMIT_BYTES` | `10485760` (10MB) | Max stdout capture |
| `MAP_MCP_ENABLED` | `true` | Enable MCP ticket server per run |
| `MAP_API_BASE` | `http://127.0.0.1:8000/api` | Backend URL for MCP proxy |

### Guardrails (per workspace, editable in Settings)

| Setting | Default | Description |
|---------|---------|-------------|
| `run_timeout_sec` | 1800 | Max seconds per run |
| `max_cost_per_run` | 2.0 | Max cost per run |
| `max_cost_per_ticket` | 20.0 | Max accumulated cost per ticket |
| `max_handoff_depth` | 12 | Max agent-to-agent chain length |
| `loop_threshold` | 3 | Ping-pong pairs before blocked |
| `max_concurrent_runs` | 3 | Max simultaneous runs per workspace |
| `max_auto_retries` | 3 | Auto-retry failed runs before blocking |

---

## Project Structure

```
multi-agent/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app entry point
│   │   ├── config.py            # pydantic-settings config
│   │   ├── mcp_server.py        # MCP ticket server (per-run stdio subprocess)
│   │   ├── api/                 # REST API routers
│   │   │   ├── workspaces.py
│   │   │   ├── agents.py
│   │   │   ├── tickets.py
│   │   │   ├── comments.py
│   │   │   ├── attachments.py
│   │   │   ├── runs.py
│   │   │   ├── events.py        # SSE endpoint
│   │   │   ├── conversations.py
│   │   │   ├── sprints.py
│   │   │   ├── routines.py
│   │   │   ├── artifacts.py
│   │   │   ├── models.py        # /models endpoint
│   │   │   ├── git.py
│   │   │   └── errors.py
│   │   ├── core/
│   │   │   ├── orchestrator.py  # Run scheduler & executor (3744 lines)
│   │   │   ├── events.py        # EventBus + SSE broadcast
│   │   │   ├── report.py        # ```map block parser
│   │   │   ├── state_machine.py # Ticket status transitions
│   │   │   ├── guardrails.py    # Budget, depth, loop, kill switch
│   │   │   ├── loop_detector.py
│   │   │   ├── auto_check.py    # Stale ticket auto-nudge
│   │   │   ├── routine_scheduler.py
│   │   │   └── git.py
│   │   ├── agents/
│   │   │   ├── base.py          # AgentTool protocol
│   │   │   ├── opencode_tool.py # opencode adapter
│   │   │   ├── claude_tool.py   # claude adapter (stub)
│   │   │   ├── stub_tool.py     # placeholder for agy/codex
│   │   │   └── prompts.py       # Per-role prompt templates
│   │   ├── db/
│   │   │   ├── models.py        # SQLAlchemy 2.0 models
│   │   │   └── session.py
│   │   └── schemas/             # Pydantic request/response schemas
│   ├── alembic/                 # DB migrations
│   │   └── versions/
│   ├── tests/                  # 35+ test files
│   └── pyproject.toml
├── frontend/                   # Next.js App Router
├── storage/                    # Attachments (outside repo_path)
│   └── attachments/
├── docs/                       # This project
│   ├── 00-overview.md
│   ├── 01-prd.md
│   ├── 02-tsd.md               # Technical architecture + API
│   ├── 03-agent-design.md      # Role prompts, handoff rules
│   ├── 04-tasks.md             # Task breakdown (MAP-001...)
│   ├── 05-roadmap.md
│   ├── 06-adr.md               # Architecture decision records
│   ├── 07-dogfood-report.md
│   ├── 08-api-spec.md          # This file
│   └── 09-operations-guide.md
└── Makefile
```

---

## Common Operations

### Reset database (development only)

> ⚠️ **WARNING**: Never run this on a production/shared dev DB. See CLAUDE.md §"Local dev safety".

```bash
cd backend
rm -f map.db map.db-wal map.db-shm
alembic upgrade head
```

### Run migrations

```bash
make migrate
```

### Run tests

```bash
cd backend
make test
# or: .venv/bin/pytest
```

Single test:
```bash
cd backend && .venv/bin/pytest tests/test_report.py::test_missing_block -q
```

### Database migrations

```bash
cd backend
alembic upgrade head      # apply all migrations
alembic downgrade base     # rollback all
alembic current            # show current revision
alembic history            # show migration history
```

### Check opencode version

```bash
opencode --version
```

### Force-refresh models list

Restart the backend — the models list is cached for 5 minutes.

---

## Troubleshooting

### `/api/health` returns `"opencode": null`

opencode binary not found in PATH or not authenticated.

```bash
opencode auth login
opencode models   # verify it works
```

### "repo_path validation failed" when creating workspace

- Must be an absolute path
- Must exist and be a directory
- Must be readable/writable by the user running the backend

### Agent runs but produces no output

Check the run's events in the Activity feed. If you see `error` events with "no ```map block found", see [07-dogfood-report.md](07-dogfood-report.md) Finding #1 — adapter schema mismatch.

### Ticket stuck in `blocked`

Check the system comment on the ticket — it names the guardrail that fired. Common causes:
- `run_timeout_sec` exceeded
- `max_cost_per_ticket` exceeded
- `max_handoff_depth` exceeded
- `loop_threshold` exceeded (ping-pong between two agents)
- Missing or malformed ```map block

### Backend won't start — port 8000 already in use

```bash
# Find what's using port 8000
lsof -i :8000
# Kill it if it's a stale uvicorn process
kill <PID>
```

### Frontend shows "Backend offline"

1. Check backend is running: `curl http://127.0.0.1:8000/api/health`
2. Check CORS: `CORS_ORIGINS` in `.env` must include `http://localhost:3000`

### opencode runs hang indefinitely

- Check `run_timeout_sec` in guardrails
- Press Pause (kill switch) to stop all runs
- Check `ps aux | grep opencode` for orphaned processes

### Migration errors

```bash
cd backend
alembic upgrade head --sql | head -50   # preview SQL
alembic stamps                               # show current stamp
# If stuck: alembic stamp head              # force stamp to head
```

---

## Deployment (Git-Based to VM)

### Overview

Deployment is git-based: push to the `main` branch on the VM host triggers a Docker rebuild. Traffic flows through Cloudflare → Nginx Proxy Manager → WireGuard tunnel → containers.

```
Browser → Cloudflare → NPM (WireGuard VM) → WireGuard tunnel → VM (Docker/Nginx)
```

### Prerequisites

| Component | Purpose |
|-----------|---------|
| VM with Ubuntu/Debian | Host for Docker containers |
| WireGuard | VPN tunnel from Cloudflare VM to backend VM |
| Nginx Proxy Manager | Reverse proxy + Let's Encrypt TLS |
| Cloudflare | DNS + CDN + DDoS protection |

### Step 1 — VM Setup

```bash
# On the VM
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose nginx wireguard

# Enable and start Docker
sudo systemctl enable docker
sudo systemctl start docker
```

### Step 2 — WireGuard Configuration

```bash
# On the Cloudflare side (NPM VM), generate key pair:
wg genkey | tee privatekey | wg pubkey > publickey

# On the backend VM, generate key pair:
wg genkey | tee privatekey | wg pubkey > publickey

# Exchange public keys and configure /etc/wireguard/wg0.conf on BOTH VMs:
# Backend VM (10.66.0.2):
[Interface]
Address = 10.66.0.2/24
PrivateKey = <backend_private_key>
ListenPort = 51820

[Peer]
PublicKey = <npm_public_key>
Endpoint = <npm_vm_ip>:51820
AllowedIPs = 10.66.0.0/24
PersistentKeepalive = 25

# NPM VM (10.66.0.1):
[Interface]
Address = 10.66.0.1/24
PrivateKey = <npm_private_key>
ListenPort = 51820

[Peer]
PublicKey = <backend_public_key>
Endpoint = <backend_vm_ip>:51820
AllowedIPs = 10.66.0.0/24
PersistentKeepalive = 25
```

Enable and start WireGuard:
```bash
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0
```

### Step 3 — Nginx Proxy Manager

Install Nginx Proxy Manager (e.g., via Docker Compose):
```yaml
# docker-compose.yml on NPM VM
version: '3'
services:
  app:
    image: jlesage/nginx-proxy-manager
    container_name: npm
    ports:
      - "80:8080"
      - "443:8443"
      - "81:8181"  # admin UI
    volumes:
      - ./data:/data
      - ./letsencrypt:/etc/letsencrypt
    restart: unless-stopped
```

```bash
docker-compose up -d
# Admin UI: http://<npm_ip>:81
# Default: admin@example.com / admin
```

Add a proxy host:
- Domain: `your-domain.com`
- Forward hostname: `10.66.0.2` (backend VM WireGuard IP)
- Forward port: `8000` (backend)
- Enable SSL with Let's Encrypt

### Step 4 — Deploy Script

On the backend VM, create a deploy hook:

```bash
# /opt/cempala/deploy.sh
#!/bin/bash
set -e
cd /opt/cempala
git pull origin main
docker-compose build --no-cache
docker-compose up -d
docker image prune -f
```

Set up git remote on the VM:
```bash
git remote add vm ssh://user@<vm_ip>:/opt/cempala
```

Or use a GitHub webhook to trigger `/opt/cempala/deploy.sh` on push to `main`.

### Step 5 — Cloudflare DNS

Point your domain's A record to the NPM VM's public IP:
```
A  your-domain.com  203.0.113.10   # NPM VM public IP
```

Enable Cloudflare proxy (orange cloud) for HTTPS.

### Step 6 — First Deploy

```bash
# On the VM
cd /opt/cempala
git clone <repo_url> .
docker-compose up -d --build
```

Verify:
```bash
curl https://your-domain.com/api/health
docker ps
```

---

## Security Notes

### ⚠️ The `--auto` flag

opencode runs with `--auto` which approves all permissions. An agent can run **any command** with the privileges of the user running the backend.

- **Never expose the backend to a network** — it binds `127.0.0.1` only
- **Only run on repos you trust**
- **Do not put production secrets inside `repo_path`**
- The security warning is shown in the README and Settings page — it cannot be dismissed

### Data separation

- `map.db` and `storage/attachments/` are gitignored — they are not in the repo
- The agent's `repo_path` is the only place agents touch — no other folder is accessible
- Attachments are stored outside `repo_path`

### Kill switch verification

When you press Pause, the backend:
1. Sets `paused=true` on the workspace
2. Sends cancel signal to all subprocesses
3. Marks all runs `cancelled`
4. Resets all agents to `idle`

You can verify processes are killed:
```bash
ps aux | grep opencode    # should be empty after pause
```

---

## Architecture Summary

```
Browser → Next.js (:3000) → FastAPI (:8000) → opencode subprocess → LLM
                ↑                    ↓
                SSE ← ← ← ← EventBus ← ← ← ← ← ← events (DB)
```

Key points:
- Backend never touches files inside `repo_path` — only opencode does
- All activity is persisted to the `event` table before broadcast
- The ```map block at the end of agent output is the return contract
- Guardrails (timeout, cost, depth, loop) are the only brakes on autonomous flow

For full architecture details, see [02-tsd.md](02-tsd.md).
