# API Specification — CEMPALA Multi-Agent Portal

Version 0.2 · MVP · 2026-08-22
Companion docs: [01-prd.md](01-prd.md) · [02-tsd.md](02-tsd.md) · [03-agent-design.md](03-agent-design.md)

> This document is extracted from [02-tsd.md](02-tsd.md) §2–§3 for convenience. The TSD remains the authoritative source.

---

## Base URL

```
http://localhost:8000/api
```

All requests and responses use JSON. Uniform error format:

```json
{"error": {"code": "...", "message": "..."}}
```

---

## Authentication

No authentication (MVP — single local user, ADR-005). Backend binds `127.0.0.1` only.

---

## Workspaces

### List workspaces
```
GET /workspaces
```
Response: `Workspace[]`

### Create workspace
```
POST /workspaces
Body: {name: string, key: string, repo_path: string}
```
- `key`: 2–5 uppercase letters, unique
- `repo_path`: must be an existing directory
Response: `Workspace` · 409 if key duplicate · 422 if path invalid

### Get workspace
```
GET /workspaces/{id}
```
Response: `Workspace`

### Update workspace
```
PATCH /workspaces/{id}
Body: {name?: string, repo_path?: string, guardrails?: Guardrails}
```
Response: `Workspace`

### Delete workspace
```
DELETE /workspaces/{id}
```
Cascade: agents, tickets, runs, events. Does NOT delete the repo folder.
Response: 204

### Terminate workspace
```
POST /workspaces/{id}/terminate
```
Pauses the workspace (kill switch), waits for all running/queued runs to stop
(timeout 60s), then deletes every ticket/sprint/artifact group and the workspace
itself (cascades to agents, runs, events, conversations, routines, memories).
Does NOT delete the repo folder. Returns 409 `runs_in_progress` if runs don't
stop within the timeout (workspace stays paused).
Response: 204

### Pause workspace (kill switch)
```
POST /workspaces/{id}/pause
```
Sets `paused=true`, cancels all runs, terminates all opencode subprocesses.
Response: 204

### Resume workspace
```
POST /workspaces/{id}/resume
```
Response: 204

---

## Agents

### List agents
```
GET /workspaces/{id}/agents
```
Response: `Agent[]`

### Create agent
```
POST /workspaces/{id}/agents
Body: {
  name: string,
  role: "pm" | "lead" | "engineer" | "designer" | "qa" | "pentester" | "business_analyst" | "system_architect",
  model: string,
  tool_kind: "opencode" | "claude" | "agy" | "codex",
  system_prompt?: string
}
```
- `name`: unique slug per workspace (used for @mention)
- `model`: format `provider/model` from `opencode models`
- `tool_kind`: only `opencode` is active; others show as disabled
Response: `Agent` · 409 if name duplicate

### Update agent
```
PATCH /agents/{id}
Body: {name?, role?, model?, tool_kind?, system_prompt?, enabled?}
```
Response: `Agent`

### Delete agent
```
DELETE /agents/{id}
```
Response: 204 · 409 if agent has an active run

### Agent memory

```
GET /agents/{id}/memory         → newest first
POST /agents/{id}/memory        {note}  — origin=owner, manual note
DELETE /agent-memory/{memory_id}
```
Notes with `origin=agent` are created by the orchestrator from the ```map `memory:` block only.

---

## Tickets

### List tickets
```
GET /workspaces/{id}/tickets
Query: ?status=&assignee_id=&parent_id=
```
Response: `Ticket[]`

### Create ticket
```
POST /workspaces/{id}/tickets
Body: {
  title: string,
  description?: string,
  priority?: "low" | "medium" | "high" | "urgent",
  assignee_id?: string,
  parent_id?: string,
  is_new_epic?: boolean
}
```
- Requires either `parent_id` or `is_new_epic=true`
- Key format: `<WORKSPACE_KEY>-<n>` (auto-increment, never reused)
Response: `Ticket` · 422 if epic_required or invalid_epic_flag

### Get ticket
```
GET /tickets/{key}
```
Returns: ticket + comments + attachments + runs + children + parent
Response: `TicketDetail`

### Update ticket
```
PATCH /tickets/{key}
Body: {
  status?: string,
  priority?: string,
  assignee_id?: string,
  sprint_id?: string
}
```
- Status must be a valid transition per state machine
Response: `Ticket` · 422 if illegal transition

### Delete ticket
```
DELETE /tickets/{key}
```
PM only (via MCP tool). Cascade: comments, attachments, runs.
Response: 204 · 403 if not PM

---

## Attachments

### Upload attachment
```
POST /tickets/{key}/attachments
Content-Type: multipart/form-data
Body: file (max 25 MB)
```
Stored at `storage/attachments/<ticket_id>/<uuid>-<sanitized_name>`
Response: `Attachment` · 413 if > 25 MB

### Get attachment
```
GET /attachments/{id}
```
Response: file download

### Delete attachment
```
DELETE /attachments/{id}
```
Response: 204

---

## Artifact Groups

```
GET /workspaces/{id}/artifacts
```
Returns: attachments with `origin=agent`, grouped per `ArtifactGroup`.
Read-only — groups and attachments are created via the ```map `artifacts:` block only.

---

## Routines (scheduled agent tasks)

### List routines
```
GET /workspaces/{id}/routines
```

### Create routine
```
POST /workspaces/{id}/routines
Body: {
  name: string,
  prompt: string,
  interval_minutes: number,
  mode: "idle_only" | "consistent",
  agent_id?: string
}
```

### Update routine
```
PATCH /routines/{id}
Body: {name?, prompt?, interval_minutes?, mode?, agent_id?, status?}
```

### Delete routine
```
DELETE /routines/{id}
```

### Run routine now
```
POST /routines/{id}/run
```
Manual trigger (bypasses interval).

---

## Comments

### List comments
```
GET /tickets/{key}/comments
```
Response: `Comment[]` (newest first)

### Post comment
```
POST /tickets/{key}/comments
Body: {body: string, author_agent_id?: string}
```
- Parses `@agent-name` → creates `comment_mention` rows
- Mentioned agents are triggered for a run (except the author)
Response: `Comment`

---

## Conversations (chat with PM)

### List conversations
```
GET /workspaces/{id}/conversations
```

### Create conversation
```
POST /workspaces/{id}/conversations
Body: {title: string, linked_ticket_key?: string}
```

### Get conversation
```
GET /conversations/{id}
```

### List messages
```
GET /conversations/{id}/messages
```

### Post message
```
POST /conversations/{id}/messages
Body: {body: string}
```
Owner message → triggers a PM chat run (`trigger="chat"`)

### Conversation attachments
```
GET /conversations/{id}/attachments
POST /conversations/{id}/attachments   (multipart)
GET /conversations/attachments/{id}/download
DELETE /conversations/attachments/{id}
```

---

## Runs

### Run agent on ticket
```
POST /tickets/{key}/run
Body: {agent_id?: string}
```
Manual trigger.

### Stop run
```
POST /runs/{id}/stop
```
Response: 204

### Retry run
```
POST /runs/{id}/retry
```
Only for `failed` / `interrupted` / `cancelled` (with `error=None`).
Reschedules same agent+ticket (`trigger="manual"`).
Response: `Run` · 409 `not_retryable` for other statuses

### Get run
```
GET /runs/{id}
```
Returns: metadata + events (paginated)

### List workspace runs
```
GET /workspaces/{id}/runs?status=
```

---

## Events (SSE)

### Stream events
```
GET /workspaces/{id}/events/stream?since_event_id=
```
`text/event-stream` · `id: <event.id>` per message
Replay from DB on reconnect via `Last-Event-ID`
Heartbeat `: ping` every 15 seconds

Event types:
- `run_started` · `assistant_text` · `reasoning` · `tool_call` · `tool_result`
- `status_change` · `comment` · `conversation_message` · `handoff` · `error` · `run_ended`

---

## Models

```
GET /models
```
Runs `opencode models`, returns `["provider/model", ...]`
Cached 5 minutes.
503 if command fails (suggest `opencode auth login`)

---

## Health

```
GET /api/health
```
Returns: `{"status": "ok", "opencode": "1.x.x"}` or `"opencode": null`

---

## Data Models

### Workspace
```typescript
{
  id: string
  name: string
  key: string              // 2-5 uppercase, unique
  repo_path: string         // absolute path
  paused: boolean
  guardrails: Guardrails
  ticket_counter: number
  created_at: string        // UTC ISO
}
```

### Guardrails
```typescript
{
  run_timeout_sec: number
  max_cost_per_run: number
  max_cost_per_ticket: number
  max_handoff_depth: number
  loop_threshold: number
  max_concurrent_runs: number
  max_auto_retries: number
}
```

### Agent
```typescript
{
  id: string
  workspace_id: string
  name: string              // unique per workspace, slug for @mention
  role: Role
  model: string             // "provider/model"
  tool_kind: "opencode" | "claude" | "agy" | "codex"
  system_prompt: string | null
  enabled: boolean
  status: "idle" | "working" | "error" | "disabled"
  created_at: string
}
```

### Ticket
```typescript
{
  id: string
  workspace_id: string
  key: string               // "MAP-001"
  title: string
  description: string        // markdown
  status: TicketStatus
  priority: "low" | "medium" | "high" | "urgent"
  assignee_id: string | null
  parent_id: string | null   // null = epic (top-level)
  cost_used: number
  handoff_depth: number
  sprint_id: string | null
  created_at: string
  updated_at: string
}
```

### TicketStatus
```
"backlog" | "todo" | "in_progress" | "review" | "qa" | "security" | "done" | "blocked"
```

### Run
```typescript
{
  id: string
  ticket_id: string | null
  conversation_id: string | null
  agent_id: string
  status: "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted"
  trigger: "manual" | "mention" | "handoff" | "auto" | "routine" | "chat"
  parent_run_id: string | null
  tool_kind: string
  model: string
  session_id: string | null
  tokens_in: number
  tokens_out: number
  cost: number
  report: object | null     // parsed ```map block result
  error: string | null
  started_at: string
  ended_at: string | null
}
```

### Event
```typescript
{
  id: string
  run_id: string
  workspace_id: string
  seq: number               // per run
  type: EventType
  payload: object           // JSON
  created_at: string
}
```

### Comment
```typescript
{
  id: string
  ticket_id: string
  author_agent_id: string | null  // null = owner
  is_system: boolean
  body: string              // markdown
  created_at: string
}
```

### Attachment
```typescript
{
  id: string
  ticket_id: string
  filename: string
  content_type: string
  size_bytes: number
  path: string               // relative to storage/
  origin: "upload" | "agent"
  group_id: string | null
  description: string | null
  created_at: string
}
```

---

## MCP Ticket Server (ADR-011)

Each opencode run gets a local MCP server via `opencode.json` env var. Tools:

```
list_tickets      → workspace tickets; top-level tagged [EPIC]
get_ticket(key)   → detail with description, comments, status, sub-tickets
post_comment      → comment (author = running agent)
create_ticket(epic?: string) → new backlog ticket
update_ticket     → change status/priority (actor = running agent)
list_artifacts    → artifact groups + files
read_artifact     → artifact content (truncated to 8000 chars)
get_memory        → this agent's memory notes
create_memory     → save a note (max 500 chars)
update_memory     → update existing note
```

All validation stays in the backend — MCP server is a thin proxy only.
