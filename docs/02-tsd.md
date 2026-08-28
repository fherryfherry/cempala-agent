# TSD — CEMPALA Multi-Agent Portal

Version 0.2 · MVP · 2026-08-22
Companion docs: [01-prd.md](01-prd.md) · [03-agent-design.md](03-agent-design.md) · [06-adr.md](06-adr.md)

> **v0.2 changes.** The coding agent is no longer built in-house. There is no `self` tool, no
> tool-calling loop, no filesystem tool. This portal **only** assembles a prompt, hands it to an
> external coding tool (opencode), and receives the result. What we build: tickets,
> orchestration, guardrails, and visibility. ([ADR-006](06-adr.md))

## 1. Architecture

```
┌──────────────────────────────┐
│ Next.js (App Router)         │  :3000
│ React Query · Tailwind       │
│ shadcn/ui · EventSource      │
└──────────┬───────────────────┘
           │ REST + SSE (http://localhost:8000)
┌──────────▼───────────────────┐
│ FastAPI                      │  :8000
│  api/      routers           │
│  core/     orchestrator      │──► asyncio.Task per run
│  agents/   prompt + adapter  │
│  db/       SQLAlchemy        │
└──────────┬───────────────────┘
           │ subprocess
    ┌──────▼──────────────────────────┐
    │ opencode run --format json      │──► LLM (provider configured in opencode)
    │   --dir <repo_path> --auto      │──► reads/writes files in the repo
    └─────────────────────────────────┘
```

The portal **never** touches files inside `repo_path`. All code access is opencode's business.
The only things we persist are `map.db` and `storage/attachments/`.

### Repo layout

```
backend/
  app/
    main.py              # FastAPI app, CORS, lifespan
    config.py            # pydantic-settings
    db/models.py  db/session.py
    api/
      workspaces.py  agents.py  tickets.py  comments.py
      attachments.py  runs.py  events.py  models.py
    core/
      orchestrator.py    # run scheduler, per-agent queue
      events.py          # EventBus + persist
      state_machine.py   # status transitions + per-role permissions
      guardrails.py      # budget, depth, loop detector, kill switch
      report.py          # ```map block parser  ← agent return contract
    agents/
      base.py            # AgentTool protocol
      opencode_tool.py   # the only active adapter
      stub_tool.py       # claude/agy/codex → structured error
      prompts.py         # per-role default system prompts + context assembler
  alembic/
  pyproject.toml
frontend/  (same as before)
storage/attachments/
docs/
```

There is no `llm/`. The portal does not talk to any LLM directly.

## 2. Data model

SQLite + SQLAlchemy 2.0 + Alembic. All ids are `TEXT` (uuid4 hex), timestamps UTC ISO.

```
workspace
  id, name, key (unique, 2-5 uppercase letters), repo_path,
  paused (bool, default false),
  guardrails (JSON, §6),
  ticket_counter (int),
  created_at

agent
  id, workspace_id → workspace.id (cascade),
  name (unique per workspace, slug for @mention),
  role (enum: pm|lead|engineer|designer|qa|pentester|business_analyst|system_architect),
  model (str, format "provider/model" — from `opencode models`),
  tool_kind (enum: opencode|claude|agy|codex),
  system_prompt (nullable → default per role),
  enabled (bool), status (enum: idle|working|error|disabled),
  created_at

agent_memory
  id, agent_id → agent.id (cascade),
  note (text), origin (enum: agent|owner),
  source_ticket_key (nullable, set only for origin=agent),
  created_at
  -- cross-ticket notes per agent (not per ticket), MAP-035. origin=agent comes from the
  -- ```map `memory:` block (§4.3); origin=owner comes from manual POST /agents/{id}/memory.

ticket
  id, workspace_id (cascade), key ("MAP-001"),
  title, description (markdown),
  status (enum, §5), priority (enum: low|medium|high|urgent),
  assignee_id → agent.id (nullable, SET NULL),
  parent_id → ticket.id (nullable in the DB for 1-level nesting, but the `POST` API requires
    either `parent_id` or `is_new_epic: true` — no orphan ticket without an epic).
    "Epic" = a ticket with parent_id NULL — not a separate entity (ADR-012). Deliberately
    **reusable**: an epic can be used repeatedly as the parent of new tickets (feature/story/
    bug/enhancement) going forward, not a disposable one-off container per request — see §4.3
    field `tickets[].epic` and §3 of [03-agent-design.md](03-agent-design.md) for the reuse
    mechanism,
  cost_used (float, default 0),           -- accumulated cost from opencode
  handoff_depth (int, default 0),
  created_at, updated_at

artifact_group
  id, workspace_id (cascade), name, created_at
  -- get-or-create by (workspace_id, name) case-insensitive, created by agents via the ```map
  -- `artifacts:` block (§4.3). No manual create/rename endpoint.

attachment
  id, ticket_id (cascade), filename, content_type, size_bytes,
  path (relative to storage/),
  origin (enum: upload|agent, default upload),  -- upload = manual owner attachment,
                                                  -- agent = published from `artifacts:`
  group_id → artifact_group.id (nullable, SET NULL),
  description (nullable, from `artifacts:` — always NULL for origin=upload),
  created_at

comment
  id, ticket_id (cascade), author_agent_id (nullable = owner),
  is_system (bool), body (markdown), created_at

comment_mention
  id, comment_id (cascade), agent_id (cascade)

conversation            -- chat with the PM, separate from ticket comments (ADR-014)
  id, workspace_id (cascade), title,
  linked_ticket_key (nullable, display-only context link),
  created_at, updated_at, last_message_at (nullable)

conversation_message
  id, conversation_id (cascade), run_id (nullable, SET NULL),
  author_agent_id (nullable = owner), is_system (bool), body, created_at

conversation_attachment
  id, conversation_id (cascade), message_id (nullable, SET NULL),
  filename, content_type, size_bytes, path (relative to storage/), created_at

run
  id, ticket_id (cascade, nullable — NULL for routine/chat runs),
  conversation_id (nullable, SET NULL — set only for chat runs),
  agent_id (cascade),
  status (enum: queued|running|done|failed|cancelled|interrupted),
  trigger (enum: manual|mention|handoff|auto|routine|chat),
  parent_run_id → run.id (nullable),
  tool_kind, model,
  session_id (nullable),                  -- opencode session, for continuation
  tokens_in, tokens_out, cost (float),
  report (JSON, nullable),                -- parse result of the ```map block
  error (text, nullable),
  started_at, ended_at

event
  id, run_id (cascade), workspace_id (denormalized, SSE filter),
  seq (int, per run),
  type (enum: run_started|assistant_text|reasoning|tool_call|tool_result|
        status_change|comment|conversation_message|handoff|error|run_ended),
  payload (JSON), created_at
```

Indexes: `event(workspace_id, id)`, `event(run_id, seq)`, `ticket(workspace_id, status)`, `run(status)`.

The `event` table is the single source of activity: the live feed and the post-refresh replay
read the same table ([ADR-008](06-adr.md)).

## 3. API contract

Base `http://localhost:8000/api`. Uniform errors `{"error": {"code": "...", "message": "..."}}`.

### Workspace
```
GET    /workspaces
POST   /workspaces               {name, key, repo_path}
GET    /workspaces/{id}
PATCH  /workspaces/{id}          {name?, repo_path?, guardrails?}
DELETE /workspaces/{id}
POST   /workspaces/{id}/pause
POST   /workspaces/{id}/resume
```
`repo_path` is validated: absolute, exists, is a directory. (This validation is for convenience —
not a sandbox; see §7.)

### Agent
```
GET    /workspaces/{id}/agents
POST   /workspaces/{id}/agents   {name, role, model, tool_kind, system_prompt?}
PATCH  /agents/{id}
DELETE /agents/{id}              → 409 if it has an active run
```

### Agent memory (MAP-035)
```
GET    /agents/{id}/memory       → newest first
POST   /agents/{id}/memory       {note}   -- origin=owner, manual note
DELETE /agent-memory/{memory_id}
```
Notes with `origin=agent` are created by the orchestrator only, from the ```map `memory:` block
(§4.3) — there is no POST endpoint for them besides the agent's own report.

### Ticket
```
GET    /workspaces/{id}/tickets   ?status=&assignee_id=&parent_id=
POST   /workspaces/{id}/tickets   {title, description, priority?, assignee_id?, parent_id?,
                                    is_new_epic?}
                                   -- requires either parent_id or is_new_epic=true
                                   (422 epic_required if both are empty, 422
                                   invalid_epic_flag if both are set)
GET    /tickets/{key}             → ticket + comments + attachments + runs + children + parent
PATCH  /tickets/{key}
DELETE /tickets/{key}
```
Key: `UPDATE workspace SET ticket_counter = ticket_counter + 1 RETURNING ticket_counter`
in the same transaction as the insert. Numbers are never reused.

### Attachment
```
POST   /tickets/{key}/attachments   multipart, max 25 MB
GET    /attachments/{id}
DELETE /attachments/{id}
```
Stored at `storage/attachments/<ticket_id>/<uuid>-<sanitized_name>`, outside `repo_path`.
Attachments are passed to opencode via the `-f` flag (§4.2).

### Artifact groups
```
GET   /workspaces/{id}/artifacts   → attachments with origin=agent, grouped per ArtifactGroup
```
Read-only — groups and their attachments are created via the ```map `artifacts:` block (§4.3);
there is no manual create/update/delete endpoint. The Artifacts menu in the frontend (§8) uses
this endpoint.

### Routines (scheduled agent tasks)
```
GET    /workspaces/{id}/routines
POST   /workspaces/{id}/routines   {name, prompt, interval_minutes, mode, agent_id?}
PATCH  /routines/{id}              {name?, prompt?, interval_minutes?, mode?, agent_id?, status?}
DELETE /routines/{id}
POST   /routines/{id}/run          → manual trigger ("Run now" button)
```
Routines = scheduled tasks that run an agent **without a ticket** (`Run.ticket_id = NULL`,
`trigger = "routine"`, `routine_id` links to `routine`). Routine statuses:
`idle` (waiting for the interval) → `waiting` (run scheduled/queued) → `running` (run in
progress) → `idle`; `disabled` = turned off by the owner. In-process scheduler
(`core/routine_scheduler.py`) ticks every 60 seconds; `idle_only` mode skips the tick if the
agent is busy (and advances `last_run_at`), `consistent` mode queues behind the agent's
currently running run. Workspace `paused` → all routines are skipped. The `max_concurrent_runs`
guardrail still applies (routines are counted toward it); `max_cost_per_ticket`/`max_handoff_depth`
do not apply (no ticket).

### Comment
```
GET    /tickets/{key}/comments
POST   /tickets/{key}/comments   {body, author_agent_id?}
```
The server parses `@agent-name`, fills `comment_mention`, and triggers a run for each mentioned
agent (except the author), `trigger=mention`. This is a nudge, not a reassignment: unlike a
```map `mention:` handoff (§4.3), a comment `@mention` never changes `ticket.assignee_id` —
it may be a plain discussion in the comment thread.

### Conversations (chat with the PM, ADR-014)
```
GET    /workspaces/{id}/conversations
POST   /workspaces/{id}/conversations   {title, linked_ticket_key?}
GET    /conversations/{id}
GET    /conversations/{id}/messages
POST   /conversations/{id}/messages     {body}   → owner message, triggers a PM chat run
GET    /conversations/{id}/attachments
POST   /conversations/{id}/attachments   (multipart file)
GET    /conversations/attachments/{id}/download
DELETE /conversations/attachments/{id}
```
Chat is a separate channel from ticket comments: messages live in
`conversation_message`, and each owner message schedules a PM run with
`Run.ticket_id = NULL`, `trigger = "chat"`, `Run.conversation_id` set. The PM's reply
is the ```map `summary` written back into the conversation; `comments[]` in the chat
```map contract is the two-way follow-up onto real tickets (same field as routine
runs). Only `max_concurrent_runs` applies (counted across ticket + no-ticket runs);
failures land as System messages in the conversation. One chat run per conversation
at a time — a second message while a run is queued/running is persisted and answered
by the in-flight run (its prompt includes the full transcript).

### Run
```
POST   /tickets/{key}/run        {agent_id?}
POST   /runs/{id}/stop
POST   /runs/{id}/retry           -- only for runs with status failed/interrupted
GET    /runs/{id}                → metadata + events (paginated)
GET    /workspaces/{id}/runs     ?status=
```
`retry` (MAP-036) re-schedules the same agent+ticket (`trigger=manual`) — mechanically identical
to clicking Run again. It covers status `failed`/`interrupted` (Retry) and `cancelled` with
`error=None` (Resume — a run the owner stopped); `cancelled` runs with `error` set were killed
by a runtime guardrail and are rejected. The `session_id` lookup in `execute()` is already
status-agnostic (§4.5), so it automatically resumes the old opencode session if the retried
run managed to get a `session_id` before stopping/failing. If the ticket is `blocked` at retry
time, this endpoint clears the block first (`blocked_reason=None`, `loop_reset_at`,
`handoff_depth=0`) — the same pattern as `PATCH /tickets/{key}` when moving a ticket out of
`blocked` — so pre-failure history does not immediately re-trip the same guardrail. 409
`not_retryable` for any other status.

### Events (SSE)
```
GET /workspaces/{id}/events/stream?since_event_id=
```
`text/event-stream`, `id: <event.id>` per message, replay from the DB on reconnect via
`Last-Event-ID`, heartbeat `: ping` every 15 seconds.

### Models
```
GET /models   → ["opencode/big-pickle", "ollama/qwen3-coder:480b-cloud", ...]
```
Runs `opencode models` (one `provider/model` per line), cached 5 minutes in memory.
Single source of truth: whatever appears in the dropdown is guaranteed to be recognized by
opencode. If the list is empty or the command fails → 503 with a message suggesting
`opencode auth login`. Ollama Cloud models appear here once the `ollama` provider is configured
in opencode by the owner — the portal never stores any LLM API keys.

### MCP ticket server (ADR-011)

Each opencode run gets a local MCP server (`app/mcp_server.py`, stdio subprocess) via a
per-run `opencode.json` config (`OPENCODE_CONFIG` env var, deleted after the run finishes). The
server proxies to the backend HTTP API (`MAP_API_BASE`, default `127.0.0.1:8000/api`) scoped by
per-run workspace/agent env vars. Tools:

```
list_tickets      → list of workspace tickets; top-level tickets (epics) tagged [EPIC]
get_ticket(key)   → detail: description, comments, status, assignee, sub-tickets
post_comment      → comment on a ticket (author = running agent, does not trigger a run)
create_ticket(epic?) → new backlog ticket, not auto-scheduled. Without `epic`: becomes a new
                       top-level epic. With `epic` (an existing top-level ticket key, ADR-012):
                       attaches as a child of that epic — rejected if the key is not a
                       top-level epic.
update_ticket     → change status/priority (actor = running agent, backend state machine)
list_artifacts    → artifact groups + files (Artifacts menu)
read_artifact     → artifact content (markdown/text; truncated to 8,000 chars)
get_memory        → this agent's memory notes
create_memory     → save a memory note (max 500 chars)
update_memory     → update an existing memory note
```

All validation (state machine, role gate, PM approval) stays in the backend — this server is a
thin proxy, not duplicated logic. No TCP: the server is only a stdio subprocess spawned by the
backend. Disable with `MAP_MCP_ENABLED=false` (backend config) to run without ticket tools.

## 4. Agent runtime

One run = one `opencode` process. No loop, no tool calls from our side.

```
prompt (assembled) ──► opencode subprocess ──► JSON stream ──► Event ──► SSE + DB
                                          └► final text ──► parse ```map block ──► ticket actions
```

### 4.1 Adapter

```python
# agents/base.py
class AgentTool(Protocol):
    async def run(self, ctx: RunContext) -> AsyncIterator[Event]: ...
```

`RunContext`: `run_id`, `workspace`, `agent`, `ticket`, `repo_path`, `prompt`, `attachments`,
`prev_session_id`, `guardrails`, `cancel_event`.

`TOOLS = {"opencode": OpenCodeTool}`. `claude`/`agy`/`codex` map to `StubTool` which immediately
returns an `error` event "adapter not available yet" and marks the run `failed`.

### 4.2 OpenCodeTool

```
opencode run --format json --dir <repo_path> -m <provider/model> --auto \
  [-s <prev_session_id>] [-f <attachment_path> ...] "<prompt>"
```

- `--format json` → JSON event stream, one event per line on stdout. The adapter maps them to
  `Event`: assistant text → `assistant_text`, opencode tool usage → `tool_call`/`tool_result`,
  reasoning → `reasoning`, error → `error`. Lines that aren't valid JSON are **skipped**, they
  don't kill the run. stderr is captured into `run.error` if the exit code ≠ 0.
- `--auto` is required: no human approves the permission dialog. Consequences in §7.
- `session_id` from the first event is stored in `run.session_id`. When the same agent returns
  to the same ticket, the next run uses `-s <session_id>` so its working context carries over.
- Ticket attachments are passed via `-f`.
- Tokens and cost are read from the opencode events and accumulated into `run` and
  `ticket.cost_used`.
- Cancel: `process.terminate()`, wait 5 seconds, `kill()`.
- Binary missing from PATH → run `failed` with a clear message; the backend doesn't crash.

### 4.3 Return contract: ```map block

opencode is a black box — it can't call our ticket API. That's why every prompt ends with an
instruction to close the answer with a fenced block:

````
```map
status: review              # target status for this ticket
mention: [lead-1]           # agent that should take over (name, not role, no @)
summary: |                  # becomes a comment on the ticket
  Added email validation to the login form.
  Files: src/auth/login.tsx, src/auth/validate.ts
  Evidence: npm test → 12 passed
  @lead-1 — please review the auth flows.
tickets:                    # optional; PM for breakdown, QA/Pentester for bugs
  - title: Add validation to POST /auth/login
    description: |
      ...
    assignee: eng-1
    priority: high
    epic: AUTH-001            # optional; target epic key — REQUIRED if a relevant one exists
artifacts:                  # optional; files produced by the agent, shown in the Artifacts menu
  - path: docs/PRD.md       # relative to repo_path
    group: Technical Documents   # REQUIRED, must be one of the groups listed in the prompt (see rules below)
    description: initial PRD
memory:                     # optional; cross-ticket notes, see rules below
  - Run the migration before reporting done.
```
````

Parser rules (`core/report.py`):

- Take the **last** ```map block from the last assistant text. Parse it as YAML (`yaml.safe_load`).
- `status` is validated by the state machine (§5). Illegal → ticket `blocked` + a system comment
  naming the requested transition.
- `mention` is matched against agent names in the workspace. Unknown name → noted in a system
  comment, no run is triggered. A successful handoff (`mention` resolving to ≥1 valid agent on
  a non-final ticket) also moves `ticket.assignee_id` to the first valid target — the board,
  ticket detail, and timeline all render assignee from that column. Fan-out schedules every
  target but keeps exactly one assignee (the first). Mentions on a final status or in comment
  text never change the assignee ([03-agent-design.md](03-agent-design.md) §6).
- `summary` is **required** → becomes a ticket comment with that agent's `author_agent_id`.
  An `@name` in the comment text (summary or `comments[]` body) is recorded as a
  `comment_mention` row for the UI badge/link — informational only, never schedules a run.
  The actionable handoff is exclusively the `mention:` field (a second schedule from the
  same report would bypass handoff guardrails).
- `tickets[]` optional. Assigned, status `todo`. Only PM, QA, and Pentester may fill this in
  (enforced per role, not trusted to the model). The parent (`parent_id`) is resolved by the
  orchestrator, **not** always "child of the current ticket" (see `epic:` below, ADR-012).
- **`epic:` per `tickets[]` item** (ADR-012) — target epic key (top-level ticket), optional.
  The contract includes a catalog of existing epics (top-level tickets, ~100 most recently
  updated, same pattern as the `artifacts:` catalog below) with a REQUIRED-reuse-if-relevant
  rule. Orchestrator `parent_id` resolution: (1) valid `epic:` → that epic's id; (2) unknown
  `epic:`/not a top-level epic → skipped with a note in the system comment, continue to (3);
  (3) no `epic:` and the current ticket **already has a parent** → use that parent (sibling
  under the same epic — keeping the flat 1-level hierarchy, fixing an old bug where the agent
  path didn't enforce this like the manual API path did); (4) no `epic:` and the current
  ticket has no parent → the current ticket itself becomes the parent (original behavior, this
  ticket becomes a new epic).
- `sprints[]` optional, companion to `tickets[]` (executed only when the report also carries
  `tickets[]`). The roles allowed to declare it are configured per workspace via the
  `sprint_creator_roles` setting (Settings page, pill picker; default `["pm"]`) — enforced in
  the parser, not trusted to the prompt. The owner-approval gate (PM not yet approved) still
  applies for the pm role. **A sprint is purely a timebox** — the contract includes a catalog of
  existing sprint names with a REQUIRED reuse rule (exact name) if the timebox is still
  relevant, plus an explicit instruction: don't put feature/scope names in sprint names (that's
  the job of `epic:` above).
- `artifacts[]` optional, available to all roles (unlike `tickets[]`). Each entry (`path`,
  `group`, `description?`) is processed by the orchestrator (not this parser — the parser stays
  filesystem-free): `path` is resolved relative to `repo_path` and **must stay inside**
  `repo_path` (entries escaping via `..`/absolute path are ignored + noted in a system comment,
  same as failed `updates:`), then its content is copied to `storage/attachments/` as an
  `Attachment` (`origin=agent`, `group_id` from `ArtifactGroup` get-or-create by name per
  workspace, case-insensitive — same pattern as sprints). This is the only place the
  orchestrator reads files inside `repo_path`, and only explicitly declared paths the agent
  itself lists — no folder scanning, no new filesystem tool for the agent (see the note in
  [ADR-006](06-adr.md)).
- **Group names are no longer free-form.** The ```map block in the prompt includes the list of
  existing groups in the workspace (the `ArtifactGroup` list at prompt-assembly time); the agent
  must pick the relevant one (matching by purpose, not exact spelling) and may only create a new
  name if nothing matches. This prevents duplicates/ambiguity like "Technical Docs" vs
  "Technical Document". Case-insensitive dedup get-or-create remains as the last safety net.
- **Artifact catalog in the prompt.** Every prompt includes the list of artifacts already
  published in the workspace (most recent ~100, format `[group] filename (KEY) — description`)
  so every agent can read/search what already exists before creating new files — preventing
  duplicated work and file hoarding.
- `artifact_updates[]` optional, **PM only** (enforced in the parser, same as `tickets[]`).
  Tidies the Artifacts menu: `rename` (group → target; if the target exists, it becomes a
  merge), `merge` (from→into, source deleted), `move` (a single file between groups),
  `delete` (only empty groups; ones still containing files are rejected). Executed by the
  orchestrator **after** `_publish_artifacts` on the same report, so new artifacts in the same
  block get organized too. Group/file not found or unknown op → noted in a system comment, does
  not fail the report (same tolerance as `updates:`/`tickets:`).
- `memory[]` optional (MAP-035), available to all roles like `artifacts[]`. Each string entry is
  persisted as a new `agent_memory` row (`origin=agent`, `source_ticket_key` from this ticket)
  — unlike `artifacts[]`, no filesystem is touched. Empty/non-string entries are discarded
  without failing, and each note is truncated to 500 characters (see §4.4 for how these notes
  are reused in later prompts).
- **Routine runs** (`trigger="routine"`, no ticket) use a different ```map contract: `status`/
  `mention` are **rejected** (parse error → run `failed`, not blocked). Allowed: `summary`
  (required), `comments[]` (comments on other tickets — only valid in routine runs),
  `tickets[]` (become `todo` backlog tickets, **not** auto-scheduled), `updates[]`, `memory[]`,
  `artifact_updates[]` (PM). `artifacts[]` is rejected (needs a ticket for the FK/storage
  folder). Actions are executed by the orchestrator; no ticket status transitions happen.
- **Missing or malformed block** → run `failed`, ticket `blocked`, system comment containing the
  last 2,000 characters of agent output so you can see what it actually wrote. No guessing, no
  silent failure. ([ADR-009](06-adr.md))

The full assistant output is always persisted as events — the ```map block only determines
actions, it doesn't replace the record.

### 4.4 Prompt assembler

`agents/prompts.py` assembles, in order:

1. **BASE** — identity, `repo_path`, work rules, list of teammates
   ([03-agent-design.md](03-agent-design.md) §2).
2. **Role block** — per-role default, or `agent.system_prompt` if set.
3. **Agent memory** (MAP-035) — `agent_memory` notes owned by this agent, cross-ticket (most
   recent ~20 entries, in chronological order), if any. Shown before ticket context because
   they're cross-ticket in nature, not specific to the current ticket.
4. **Ticket context** — key, title, status, priority, description, attachment list, last 5
   comments, `report.summary` of previous runs on this ticket.
5. **Artifact catalog** — the workspace's artifact list (most recent ~100) so the agent can
   read/search for what has been published before creating new files.
6. **Anti-loop context** — if this is the n-th review, a summary of previous reviews
   ([03-agent-design.md](03-agent-design.md) §7).

7. **```map contract block** — legal statuses for this role, mentionable agent names, whether
   `tickets[]` is allowed, plus (for roles allowed `tickets[]`) the existing epic catalog
   (`existing_epics`, ADR-012) and sprint catalog (`existing_sprints`) — both queried live by
   the orchestrator each run, not stored as static text, because they must always reflect the
   latest tickets/sprints in the workspace.

The final prompt is stored on the `run_started` event so it can be inspected when something
goes wrong.

### 4.5 Orchestrator

```python
async def schedule(ticket, agent, trigger, parent_run=None):
    if workspace.paused: return reject("workspace paused")
    guard = check_guardrails(ticket, agent, parent_run)
    if not guard.ok: return block_ticket(ticket, guard.reason)
    run = create_run(...)
    RUNNING[run.id] = asyncio.create_task(execute(run))
```

- One agent = one active run; the rest queue FIFO per agent.
- Each event from the adapter → `EventBus.publish()` → insert into `event` **then** push to SSE
  subscribers.
- Run finished → parse the ```map block → apply ticket actions → determine next agent from
  `mention` → `schedule()`.
- Startup: runs in the DB with status `running`/`queued` are marked `interrupted`, agents are
  reset to `idle`, a system comment is written. ([ADR-004](06-adr.md))

## 5. Ticket state machine

```
backlog → todo → in_progress → review → qa → security → done
                     ↑            │      │      │
                     └────────────┴──────┴──────┘   (reject/bug → in_progress)
   any → blocked   (guardrail, error, malformed map block, or agent request)
   blocked → todo  (owner only)
```

The per-role permission matrix is in [03-agent-design.md](03-agent-design.md) §4, enforced in
`core/state_machine.py`. It applies equally to `PATCH /tickets/{key}` and to `status` from the
```map block. Every transition writes a `status_change` event + a system comment.

## 6. Guardrails

Per workspace, in `workspace.guardrails` (JSON), editable on the settings page.

```json
{
  "run_timeout_sec": 1800,
  "max_cost_per_run": 2.0,
  "max_cost_per_ticket": 20.0,
  "max_handoff_depth": 12,
  "loop_threshold": 3,
  "max_concurrent_runs": 3,
  "max_auto_retries": 3
}
```

- **run_timeout_sec** — `asyncio.wait_for` around the subprocess; exceeded → terminate + run
  `failed`.
- **max_cost_per_run** — monitored from opencode cost events while running; exceeded → terminate.
- **max_cost_per_ticket** — accumulated in `ticket.cost_used`; exceeded → ticket `blocked`.
- **max_handoff_depth** — length of the `parent_run_id` chain; exceeded → `blocked`. Not applied
  to runs triggered by owner chat (`trigger="mention"`) — see [03-agent-design.md](03-agent-design.md)
  §6.
- **loop_threshold** — a ping-pong pair (A→B→A→B) exceeding the threshold → `blocked` + system
  comment recording the cycle.
- **max_concurrent_runs** — per-workspace semaphore. Default is low (3) because each run is a
  full opencode process, not just an HTTP call.
- **max_auto_retries** — how many times a *retryable* failed run is retried automatically per
  (ticket, agent) before the ticket gets `blocked`. A run is retryable when the failure is one
  the agent can adapt to: a missing/malformed ```map block or an opencode subprocess failure
  (nonzero exit / stderr / missing binary / no `run_ended` event). Each retry is a new `Run`
  row chained via `parent_run_id` (`trigger="auto"`); the ticket is NOT blocked between retries.
  The retry prompt carries a "PERINGATAN: RUN SEBELUMNYA GAGAL" notice (parent's error + tail
  of the agent's last output) and starts a **fresh** opencode session (`-s` is not passed).
  Non-retryable failures — state-machine rejections, runtime guardrail trips (`cancelled`),
  user stops — and routine runs never auto-retry. `max_auto_retries=0` disables the feature
  (pre-MAP behavior: fail → block). The chain (and budget) resets at any owner intervention:
  a manual `POST /runs/{id}/retry` or `trigger="mention"` schedules with no `parent_run_id`,
  breaking the chain — mirroring `loop_reset_at`/`handoff_depth` reset on unblock. Retry
  children are still subject to every other guardrail, and `max_cost_per_ticket` accumulates
  the failed attempts' cost, so runaway retries are bounded by the cost brake too.
- **Kill switch** — `POST /workspaces/{id}/pause`: set `paused=true`, set all `cancel_event`s,
  terminate all subprocesses, mark runs `cancelled`, agents `idle`, reject new schedules.
- **ticket_not_in_active_sprint** — not part of the dict above (always on, no toggle on the
  Settings page). A ticket that isn't in any sprint (backlog) or whose sprint isn't the `active`
  one can't be run by an agent → `blocked` + system comment. Exceptions: any role that is in
  `workspace.sprint_creator_roles` (default PM only) — such roles always need to be reachable
  on any ticket (including backlog) to do triage/move tickets to a sprint. The owner moves a
  ticket into the active sprint or swaps which sprint is active via the existing mechanisms
  (`PATCH /tickets/{key}` `sprint_id`, `PATCH /sprints/{id}` `status`).

Every blocking guardrail **always** writes a system comment naming the guardrail that fired.
There is no silent failure.

Note: there are no per-iteration step/token guardrails anymore — we don't control the loop
inside opencode. The remaining brakes are time, cost, and handoff topology.

## 7. Security — what is guaranteed and what is not

**Not guaranteed.** `opencode --auto` approves all permissions. An agent can run arbitrary
commands with the privileges of the user running the backend. `--dir <repo_path>` sets a working
directory, **not a sandbox** — nothing stops it from touching files outside that folder. This is
a consciously accepted consequence ([ADR-010](06-adr.md)).

**Therefore:**
- The backend binds `127.0.0.1`. This portal must not be reachable from a network.
- The README and the settings page carry an explicit warning with the exact wording below, not
  hidden away.
- Run it only on repositories you trust, on a machine you control.
- Do not put production secrets inside `repo_path`.

**What the portal still guarantees:**
- LLM API keys are never stored or touched by the portal (that's `opencode auth`'s business).
- Attachments are stored outside `repo_path` and their names are sanitized.
- The kill switch really kills child processes, not just marking status in the DB.

## 8. Frontend

| Route | Content |
|---|---|
| `/` | Workspace list + create form |
| `/w/[key]/board` | Kanban by status, drag & drop, badge showing which agent is working |
| `/w/[key]/ticket/[ticketKey]` | Detail: description, attachments, comments + mention composer, run list, Run/Stop; link to parent epic (if any) and list of sub-tickets (if epic) |
| `/w/[key]/agents` | Agent setup: role, model (dropdown from `/models`), tool_kind, system prompt, "Memory" button per agent opening a cross-ticket notes dialog (MAP-035) |
| `/w/[key]/activity` | Live feed; click a run → opencode output panel + tool calls + parsed map block; "Retry" button on `failed`/`interrupted` runs (both in the run list and the detail panel, MAP-036) |
| `/w/[key]/artifacts` | Read-only: attachments with `origin=agent`, grouped per ArtifactGroup, link back to the origin ticket |
| `/w/[key]/settings` | `repo_path`, guardrails, Pause/Resume, §7 security warning |

Realtime: one `EventSource` per workspace in the React context; an incoming event → update feed +
invalidate related queries.

## 9. Configuration

`.env` in `backend/`:
```
DATABASE_URL=sqlite+aiosqlite:///./map.db
STORAGE_DIR=../storage
CORS_ORIGINS=http://localhost:3000
OPENCODE_BIN=opencode
```
No LLM credentials. Those belong to opencode.

## 10. Testing

Automated tests required (pytest):

1. **```map block parser** — valid block, missing block, malformed YAML, multiple blocks (take
   the last), illegal `status`, `tickets[]` from unauthorized roles.
2. **State machine** — legal/illegal transition matrix per role.
3. **Loop detector** — A→B→A→B→A triggers `blocked`; A→B→C→A does not.
4. **Ticket numbering** — 100 parallel inserts → 100 unique sequential keys.
5. **Cost guardrail** — exceeding `max_cost_per_ticket` → `blocked` + system comment.
6. **Opencode adapter** — with a fake binary (a script printing sample JSON): event parsing,
   malformed lines skipped, non-zero exit → `failed`, cancel kills the child process.

The rest is tested manually against the definition of done in [05-roadmap.md](05-roadmap.md).
