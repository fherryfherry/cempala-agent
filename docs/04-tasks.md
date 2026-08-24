# Task Breakdown — MAP-001 … MAP-034

Version 0.2 · MVP
Estimate: **S** ≤ ½ day · **M** ~1 day · **L** ~2 days
Dependencies always point to a smaller number (no cycles).
Milestones: see [05-roadmap.md](05-roadmap.md).

> **v0.2 change.** 8 tickets were removed because we are not building our own coding agent:
> Ollama client, tool-calling loop, filesystem tool, ticketing tool, path jail, command allowlist.
> 2 new tickets: the ```map block parser and the opencode adapter, both promoted to M2.
> Total went from 41 → 33 tickets, ~35 days → ~26 days.

## Summary

| Milestone | Tickets | Total estimate |
|---|---|---|
| M0 Skeleton | MAP-001 … MAP-005 | ~3 days |
| M1 Ticketing | MAP-006 … MAP-016 | ~9 days |
| M2 Agent Runtime | MAP-017 … MAP-026 | ~8 days |
| M3 Autonomy | MAP-027 … MAP-033 | ~6 days |

---

## M0 — Skeleton

### MAP-001 · Repo init & folder structure · S · Lead
Git repo, `.gitignore`, `README.md`, `backend/`, `frontend/`, `storage/`, `docs/` structure
per [02-tsd.md](02-tsd.md) §1. README contains the `--auto` security warning
([02-tsd.md](02-tsd.md) §7) and the `opencode auth login` steps.
**Dep:** —
**AC:** `git status` clean after the initial commit; `storage/` ignored except for `.gitkeep`;
the security warning is in the README, not just in the docs.

### MAP-002 · FastAPI backend bootstrap · S · Engineer
FastAPI + CORS + `config.py` (pydantic-settings: `DATABASE_URL`, `STORAGE_DIR`, `CORS_ORIGINS`,
`OPENCODE_BIN`). Bind to `127.0.0.1`. `GET /api/health` endpoint that also reports whether the
opencode binary was found.
**Dep:** MAP-001
**AC:** `uvicorn app.main:app` runs and only accepts connections from localhost;
`/api/health` → `{"status":"ok","opencode":"1.x.x"}` or `"opencode": null` if not found.
No LLM credential variables in config.

### MAP-003 · DB schema & Alembic · M · Engineer
All models from [02-tsd.md](02-tsd.md) §2 + indexes. Alembic + initial migration.
**Dep:** MAP-002
**AC:** `alembic upgrade head` creates all tables, `downgrade base` is clean;
workspace cascade delete verified via test.

### MAP-004 · Next.js frontend bootstrap · M · Engineer
Next.js App Router + TS + Tailwind + shadcn/ui, `lib/api.ts`, React Query provider, layout+header.
**Dep:** MAP-001
**AC:** `next dev` runs on :3000; the root page calls `/api/health` and shows the backend status
**and** the opencode status.

### MAP-005 · One-command dev runner · S · Engineer
`Makefile` runs backend + frontend, plus `make migrate` and `make test`.
**Dep:** MAP-002, MAP-004
**AC:** `make dev` starts both; README: setup from scratch in ≤5 steps, including opencode
installation and authentication.

---

## M1 — Ticketing

### MAP-006 · Workspace CRUD API · M · Engineer
CRUD per [02-tsd.md](02-tsd.md) §3, including `repo_path` validation (absolute, exists, directory).
**Dep:** MAP-003
**AC:** invalid repo_path → 422 with the reason; duplicate key → 409;
deleting a workspace deletes its descendants but does not touch folders on disk (verified by a test).

### MAP-007 · `GET /api/models` from `opencode models` · S · Engineer
Run `opencode models`, parse one `provider/model` per line, cache 5 minutes in memory.
**Dep:** MAP-002
**AC:** command fails / empty list → 503 with a message suggesting `opencode auth login`;
30 second timeout; the backend does not store any LLM credentials.

### MAP-008 · Agent CRUD API · M · Engineer
Per-workspace agent CRUD: name (unique slug per workspace), role, model, tool_kind, optional
system_prompt, enabled, status.
**Dep:** MAP-006
**AC:** duplicate name within a workspace → 409; role/tool_kind outside the enum → 422;
`DELETE` an agent with an active run → 409.

### MAP-009 · Ticket CRUD API & key numbering · M · Engineer
CRUD + `<KEY>-<n>` key via `ticket_counter` in a single transaction. `GET /tickets/{key}`
returns the ticket + comments + attachments + runs + children. Query filters per the TSD.
**Dep:** MAP-006, MAP-008
**AC:** the number only ever increases and is never reused even if the ticket is deleted;
`parent_id` pointing to a ticket that already has a parent → 422 (max 1 level).

### MAP-010 · Comment API & mention parsing · M · Engineer
`GET/POST /api/tickets/{key}/comments`, parse `@agent-name` → `comment_mention`.
Run triggering is not here yet (MAP-029).
**Dep:** MAP-009
**AC:** `@does-not-exist` creates no mention and does not error; self-mentions are dropped;
the same name twice → a single mention row.

### MAP-011 · Attachment API · S · Engineer
Multipart upload (max 25 MB), download, delete to
`storage/attachments/<ticket_id>/<uuid>-<sanitized_name>`.
**Dep:** MAP-009
**AC:** a name like `../../etc/passwd` is sanitized into a flat name; >25 MB → 413;
files are stored outside `repo_path` (verified by a test).

### MAP-012 · Ticket state machine · M · Engineer
`core/state_machine.py`: status enum + legal transition matrix + per-role permissions
([03-agent-design.md](03-agent-design.md) §5). Enforced in `PATCH /tickets/{key}`.
Every transition writes a system comment.
**Dep:** MAP-009, MAP-010
**AC:** illegal transition → 422 naming the requested transition; the owner (without an agent) may
make any transition including `blocked → todo`; the API is reused by the map block parser (MAP-018).

### MAP-013 · Workspace UI + Agent Setup · L · Engineer
`/` page (workspace list + form), header switcher, and `/w/[key]/agents`
(agent list + form). Model dropdown from `/api/models`; tool_kind shows `opencode` active
and `claude`/`agy`/`codex` disabled labeled "coming soon".
**Dep:** MAP-004, MAP-006, MAP-007, MAP-008
**AC:** `repo_path` validation errors appear on the correct field; the active workspace is in the URL;
`/api/models` fails → the dropdown becomes a free-text input + an error message mentioning
`opencode auth login`.

### MAP-014 · Kanban board UI · L · Engineer
`/w/[key]/board`: one column per status, ticket cards (key, title, assignee, priority), drag & drop
calls `PATCH /tickets/{key}`.
**Dep:** MAP-013, MAP-012
**AC:** dropping onto a column whose transition is illegal is rejected, the card returns to its
original position with a toast containing the backend message.

### MAP-015 · Ticket detail UI · L · Engineer
`/w/[key]/ticket/[ticketKey]`: markdown description, sub-tickets, attachments, comments, composer
with `@agent` autocomplete, Run button (placeholder until MAP-023).
**Dep:** MAP-014, MAP-010, MAP-011
**AC:** autocomplete only shows that workspace's agents; system comments look different from
agent comments; attachments download with their original name.

### MAP-016 · Test: numbering & state machine · S · QA
pytest: 100 parallel ticket creations → 100 unique sequential keys; legal/illegal transition matrix per role.
**Dep:** MAP-009, MAP-012
**AC:** `make test` green; both tests fail if the related logic is deliberately broken.

---

## M2 — Agent Runtime

### MAP-017 · EventBus & event persistence · M · Engineer
`core/events.py`: `publish()` → insert into the `event` table (with `seq` per run) **then** push to
subscribers. Subscribe/unsubscribe per workspace.
**Dep:** MAP-003
**AC:** a slow subscriber does not block the publisher (bounded queue, drop oldest + flag
`overflow`); events remain complete in the DB even if the subscriber overflows.

### MAP-018 · ```map block parser · M · Lead
`core/report.py`: take the last ```map block, `yaml.safe_load`, validate `status` against the state
machine + role permissions, match `mention` to agent names, require `summary`, accept `tickets[]`
only from PM/QA/Pentester ([02-tsd.md](02-tsd.md) §4.3).
**Dep:** MAP-012
**AC:** missing block or broken YAML → an `invalid` result with a reason (not an exception);
multiple blocks → the last one is used; `tickets[]` from an Engineer is ignored + logged;
a `status` illegal for that role is rejected with a reason naming the role and its transition.
All cases in [02-tsd.md](02-tsd.md) §10.1 have a test.

### MAP-019 · Prompt builder & per-role default prompts · M · Lead
`agents/prompts.py`: BASE + role block ([03-agent-design.md](03-agent-design.md) §4) + ticket
context + anti-loop context + the ```map contract naming the legal statuses and the agents that
role can mention.
**Dep:** MAP-018
**AC:** a filled `agent.system_prompt` replaces the role block (BASE + the ```map contract remain);
the Engineer prompt never contains `tickets[]` instructions (verified by a test);
the final prompt is stored in the `run_started` event.

### MAP-020 · OpenCode adapter · L · Engineer
`agents/opencode_tool.py`: subprocess
`opencode run --format json --dir <repo> -m <model> --auto [-s <session>] [-f <att>] "<prompt>"`,
parse one stdout JSON per line → `Event`, save `session_id`, accumulate tokens & cost,
terminate→kill on cancel.
**Dep:** MAP-017, MAP-019
**AC:** missing binary → run `failed` with a clear message, backend does not crash;
non-JSON stdout lines are skipped without killing the run; exit code ≠ 0 → `failed` with stderr
saved in `run.error`; cancel actually kills the child process (checked with `ps`).
Tested against a fake binary — a script that prints sample JSON — with no real LLM calls.

### MAP-021 · Stub adapter claude/agy/codex · S · Engineer
`agents/stub_tool.py`: immediately emit an `error` event "adapter not yet available" and mark the run `failed`.
**Dep:** MAP-020
**AC:** saving an agent with that tool_kind is still allowed; running it produces a `failed`
run with a human-readable message, not a 500.

### MAP-022 · SSE endpoint · M · Engineer
`GET /api/workspaces/{id}/events/stream?since_event_id=`, `id:` per event, replay from DB on
reconnect, 15-second heartbeat, cleanup subscriber on disconnect.
**Dep:** MAP-017
**AC:** disconnect then reconnect with `Last-Event-ID` neither loses nor duplicates events;
closing the tab releases the subscriber.

### MAP-023 · Run API & basic orchestrator · L · Lead
`POST /tickets/{key}/run`, `POST /runs/{id}/stop`, `GET /runs/{id}`, `GET /workspaces/{id}/runs`.
`core/orchestrator.py`: `schedule()`, `execute()`, registry of active runs, FIFO queue per agent,
agent status, applying the map block parser result to the ticket (status + comment + `tickets[]`).
Automatic handoff is **not** in this ticket (MAP-029).
**Dep:** MAP-020, MAP-018, MAP-022
**AC:** one agent never has two `running` runs; a run that throws an exception → ticket
`blocked` + system comment containing the error; missing/broken map block → ticket `blocked` +
system comment containing the last 2,000 characters of the agent output; the agent returns to `idle` in every case.

### MAP-024 · Frontend SSE context · M · Engineer
React context with one `EventSource` per workspace, auto-reconnect, incoming events update the
feed + invalidate related React Query queries.
**Dep:** MAP-022, MAP-013
**AC:** only one SSE connection even if many components use it; switching workspaces closes
the old connection.

### MAP-025 · Activity feed UI & run panel · L · Engineer
`/w/[key]/activity`: live feed (agent, ticket, event type, time) with filters; clicking a run → panel
with the opencode output, tool-call list, parsed ```map block, cost, Stop button.
Plus agent status indicators (`idle`/`working`/`error`/`disabled`) in the header, board, agent page.
**Dep:** MAP-024, MAP-023
**AC:** output appears <1 second after the backend receives it; refresh does not wipe the history;
a feed of 1,000+ events stays responsive; Stop sets the run status to `cancelled`;
an unparseable map block is displayed prominently along with the reason.

### MAP-026 · Run recovery on restart · S · Engineer
At startup, `running`/`queued` runs are marked `interrupted`, agents reset to `idle`, and a
system comment is written on the related ticket.
**Dep:** MAP-023
**AC:** killing the backend mid-run and starting it again leaves zero `running` runs
and zero `working` agents.

---

## M3 — Autonomy

### MAP-027 · Guardrail module · M · Lead
`core/guardrails.py`: JSON defaults on the workspace, `check_guardrails()` before scheduling,
runtime monitoring (timeout, `max_cost_per_run`), accumulate `ticket.cost_used`,
`max_cost_per_ticket`, `max_handoff_depth`, `max_concurrent_runs`.
Every trip → ticket `blocked` + system comment naming which guardrail.
**Dep:** MAP-023
**AC:** lowering `run_timeout_sec` to 5 stops the run with a system comment naming it;
no guardrail path fails without a comment.

### MAP-028 · Loop detector · M · Engineer
Detect two-agent ping-pong on one ticket exceeding `loop_threshold` → `blocked` + system
comment writing out the cycle.
**Dep:** MAP-027
**AC:** a sequence of runs A→B→A→B→A triggers blocked at threshold 2; A→B→C→A does not trigger.

### MAP-029 · Handoff engine · M · Lead
`mention` from the map block (and manual owner comments) → schedule a run for the target agent
(`trigger=handoff`/`mention`, `parent_run_id` set). Resolution when the model writes a role,
not a name. Mention of a disabled or unknown agent name → `blocked`/system comment per
[03-agent-design.md](03-agent-design.md) §6.
**Dep:** MAP-023, MAP-027
**AC:** mention chains increment `handoff_depth` and stop at `max_handoff_depth`;
self-mentions do not trigger anything; a non-final status with no valid mention → ticket `blocked`,
not hanging.

## MAP-030 · Full autonomous flow · M · Lead
PM/QA/Pentester `tickets[]` get scheduled immediately for their assignee. The PM closes the epic when
all children are `done`. A follow-up run for the same agent+ticket reuses `-s <session_id>`.
**Dep:** MAP-029, MAP-012
**AC:** one epic run once goes to `done` or `blocked` without intervention;
no ticket in a non-final status without an active run and without a queue;
an Engineer who returns to the same ticket continues the previous opencode session (verified
from `run.session_id`).

### MAP-031 · Kill switch · S · Engineer
`POST /workspaces/{id}/pause` and `/resume`. Pause: cancel all runs, terminate subprocesses,
mark `cancelled`, agents to `idle`, `paused=true`, reject new schedules.
**Dep:** MAP-027
**AC:** pausing while 3 runs are active leaves zero opencode processes within ≤5 seconds (checked `ps`);
scheduling while paused is rejected with a clear message, not silently queued.

## MAP-032 · Workspace settings UI · M · Engineer
`/w/[key]/settings`: edit `repo_path`, guardrail values, Pause/Resume button (red, with
confirmation), global banner while paused, and the `--auto` security warning
([02-tsd.md](02-tsd.md) §7) that is always visible.
**Dep:** MAP-031, MAP-013
**AC:** the paused banner appears on every page of that workspace; changing guardrails applies to
the next run without a restart; the security warning cannot be dismissed.

### MAP-033 · End-to-end dogfood · M · QA
Create a workspace pointing at a sample repo, 6 agents (PM, Lead, 2 Engineers, QA, Pentester), one epic,
click Run once, let it run to completion.
**Dep:** MAP-030, MAP-032, MAP-021
**AC:** the epic reaches `done` or `blocked` with a clear reason; the feed has the full trace;
Pause stops everything mid-flight; a backend restart leaves no hanging runs;
the ```map block compliance rate (how many runs failed for format reasons) is logged and
written as a report at `docs/07-dogfood-report.md`.

## MAP-034 · Dashboard workspace · S · Engineer
`/w/[key]/dashboard`: stat cards (total, done, active, blocked), each agent status,
8 most recent runs, and alerts (blocked, failed, epic not started). Default landing & first
navigation. Purely data composition from the existing API + SSE invalidation; no new endpoints.
**Dep:** MAP-032, MAP-021
**AC:** the dashboard refreshes itself while agents work (no reload); every alert
links to the related ticket; statistics are correct for an empty workspace (no runs/agents).

## MAP-035 · Agent memory · M · Engineer
Table `agent_memory` (per `agent_id`, not per ticket). Optional `memory:` field in the
```map block — open to all roles, same as `artifacts:` (docs/03-agent-design.md §3) —
parsed by `core/report.py` and persisted by the orchestrator as a new row (`origin=agent`,
`source_ticket_key` set). The agent prompt (`agents/prompts.py`) includes the last notes
of that agent itself (capped at ~20 entries, docs/05-roadmap.md item 7) before the ticket
context. UI: `/w/[key]/agents` gets a "Memory" button per agent that opens the note list,
with a manual-add form (`origin=owner`) and a delete button per entry for owner curation.
**Dep:** MAP-018, MAP-019, MAP-023
**AC:** an agent reporting `memory:` in its block has a new `agent_memory` row and the next
run for that agent (any ticket) includes that note in the prompt; manual owner entries are
also injected; deleting an entry via the UI keeps it out of the next prompt; deleting an
agent cascades the delete of its memory.

## MAP-036 · Retry failed/interrupted runs · S · Engineer
`POST /runs/{id}/retry` — only for runs with status `failed`/`interrupted`, 409
`not_retryable` for any other status. Reschedules the same agent+ticket
(`trigger=manual`, same as clicking Run) through the existing `orchestrator.schedule()`;
the `session_id` lookup in `execute()` is already status-agnostic so it automatically
continues the old opencode session if available — no new code for that. If the ticket is
`blocked` at retry time, the endpoint clears the block first (`blocked_reason=None`,
`loop_reset_at`, `handoff_depth=0`, same pattern as `PATCH /tickets/{key}` in
`app/api/tickets.py`) so pre-failure history does not immediately re-trip the same guardrail.
UI: `/w/[key]/activity` — "Retry" button in the run list row and the run detail panel,
for runs with status `failed`/`interrupted`.
**Dep:** MAP-023, MAP-027
**AC:** retrying a `failed` run whose ticket is `blocked` because of an old `max_handoff_depth`/loop
gets successfully rescheduled (not an immediate 409 again); retrying a `failed` run with a
stored `session_id` passes `-s <session_id>` to opencode on the new run; retrying a
`done`/`running`/`queued`/`cancelled` run → 409 `not_retryable`.

## MAP-037 · Epic reuse & sprint/epic decoupling · M · Engineer
Epics remain `Ticket` (parent_id NULL), but are now **reusable** instead of single-use
containers (ADR-012). New `tickets[].epic` field (```map, `core/report.py`) and an equivalent
`create_ticket(epic=...)` parameter (MCP, `app/mcp_server.py`) — agents can attach new tickets
to an existing epic. The epic catalog (top-level tickets) and sprint catalog are injected into
the contract for pm/qa/pentester roles with a MANDATORY reuse rule (same pattern as the
Artifact Groups catalog). Bug fix: the 1-level nesting that was previously only enforced on
the manual API path is now also enforced on the agent `tickets[]` path — without an explicit
`epic:`, a new ticket created from a ticket that already has a parent attaches to that parent
(sibling), not as a grandchild. Sprints are now strictly timeboxes: the PM instruction that
said "focus per sprint" (the cause of sprint names leaking feature names) is replaced by
"Final plan must have 5 sections" (requirement, goal, target epic, sprint breakdown,
duration estimate). Frontend: the Epic dropdown in the Create Ticket dialog (Board) shows the
child ticket count.
**Dep:** MAP-018, MAP-019, MAP-023
**AC:** valid `tickets[].epic`/`create_ticket(epic=...)` → the new ticket becomes a child of that
epic; unknown/non-top-level key → skipped with a note, not aborting the report;
QA/Pentester reporting a bug from a parented ticket (without `epic:`) → the bug becomes a sibling
under the same epic, not a grandchild (regression test for the nesting bug found during
the audit); the PM `mention`-trigger prompt includes all five final plan sections.

