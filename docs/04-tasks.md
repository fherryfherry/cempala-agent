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
`POST /runs/{id}/retry` — for runs with status `failed`/`interrupted` (Retry) and
`cancelled` with `error=None` (Resume, i.e. a run the owner stopped), 409
`not_retryable` for any other status — including `cancelled` runs whose `error` is set
(those were killed by a runtime guardrail: timeout/cost — a deliberate brake, never
resumable). Reschedules the same agent+ticket
(`trigger=manual`, same as clicking Run) through the existing `orchestrator.schedule()`;
the `session_id` lookup in `execute()` is already status-agnostic so it automatically
continues the old opencode session if available — no new code for that. If the ticket is
`blocked` at retry time, the endpoint clears the block first (`blocked_reason=None`,
`loop_reset_at`, `handoff_depth=0`, same pattern as `PATCH /tickets/{key}` in
`app/api/tickets.py`) so pre-failure history does not immediately re-trip the same guardrail.
UI: `/w/[key]/activity` — "Retry" button for `failed`/`interrupted` and "Resume" button for
`cancelled` in the run list row and the run detail panel; "Stop" button for
`running`/`queued` runs. `cancelled` runs display as `stopped`.
**Dep:** MAP-023, MAP-027
**AC:** retrying a `failed` run whose ticket is `blocked` because of an old `max_handoff_depth`/loop
gets successfully rescheduled (not an immediate 409 again); retrying a `failed` run with a
stored `session_id` passes `-s <session_id>` to opencode on the new run; retrying a
`done`/`running`/`queued` run → 409 `not_retryable`; resuming a `cancelled` run (owner stop,
`error=None`) schedules a new run that completes; retrying a `cancelled` run with `error`
set (guardrail kill) → 409 `not_retryable`.

## MAP-044 · Auto-retry failed runs with adaptive prompt · M · Engineer
Before a ticket is blocked, a *retryable* failure is retried automatically up to
`max_auto_retries` (per workspace, `workspace.guardrails`, default 3) per (ticket, agent).
Retryable = missing/malformed ```map block or an opencode subprocess failure (nonzero exit /
stderr / binary not found / no `run_ended` event). Each retry is a new `Run` row
(`trigger="auto"`, `parent_run_id` chained to the failed run, ticket NOT blocked between
attempts). The retry prompt (`_build_prompt_for`) carries a "PERINGATAN: RUN SEBELUMNYA
GAGAL" notice: the parent's `error` plus the tail of the agent's accumulated `assistant_text`
(replayed from the `event` table), with an instruction to re-read the ```map contract and try
a different approach. Retry runs deliberately do NOT pass `-s <session_id>` (fresh opencode
conversation). Only when the budget is exhausted does the ticket get blocked, with the reason
naming `max_auto_retries`. Non-retryable failures (state-machine rejection, runtime guardrail
trips, user stops) and routine runs never retry. The attempt chain (and budget) resets at any
owner intervention: manual `POST /runs/{id}/retry` or `trigger="mention"` schedules without
`parent_run_id`, breaking the chain (same "fresh window" semantics as `loop_reset_at` /
`handoff_depth`). `max_auto_retries=0` restores the pre-MAP fail → block behavior. Retry
children pass through all other guardrails, including `max_cost_per_ticket` (cost of failed
attempts accumulates), which bounds runaway retries.
**Dep:** MAP-036, MAP-018, MAP-019, MAP-023
**AC:** missing-```map failure → child run scheduled (ticket stays `in_progress`), child
prompt contains the failure notice + output tail, child runs without `-s`; after
`max_auto_retries` consecutive failures the ticket is `blocked` with the reason naming
`max_auto_retries`; a later manual retry succeeds with a fresh budget; agent B's failures do
not consume agent A's budget on the same ticket; a routine run failure does not retry; a
guardrail-cancelled run does not retry.

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


## MAP-045 · Chat terpisah dari tiket (conversations) · L · Engineer
Chat dengan PM dipisah dari tiket: tabel baru `conversation` + `conversation_message` +
`conversation_attachment` (migration `a1b2c3d4e5f6`), run PM dari chat berjalan tanpa tiket
(`Run.ticket_id=NULL`, `trigger="chat"`, `Run.conversation_id` baru). Owner mengirim pesan →
`POST /conversations/{id}/messages` → `orchestrator.schedule_chat()` (guardrail hanya
`max_concurrent_runs`, dihitung lintas tiket + no-ticket runs). PM membalas lewat `summary`
di ```map kontrak chat (kontrak baru `_chat_contract_block` di `agents/prompts.py`); arah
kedua: `comments[]` (sudah ada untuk routine) kini juga valid di run chat — PM bisa menulis
komentar follow-up ke tiket mana pun dalam satu run yang sama. `tickets[]`/`updates:`/
`memory:`/`artifact_updates:` ikut tersedia (backlog, tidak auto-schedule). Kegagalan run chat
(auto-retry habis / guardrail / error internal) → System message di conversation, bukan
block tiket. `recover_interrupted_runs` menulis System message ke conversation yang
terinterupsi. Data chat lama (komentar di tiket epic) TIDAK dimigrasi — tetap sebagai
history di komentar tiket. Frontend: halaman chat di-rewrite jadi daftar conversation +
thread; komposer memakai `MentionAutocomplete` (agent/artifact/tiket, klik/Tab/panah);
render mention jadi link via `lib/mention-link.ts`; attachment chat disimpan per
conversation dan dikirim ke PM sebagai context file (PM bisa menyalinnya ke tiket lewat
komentar). SSE: event type baru `conversation_message` → invalidate conversations.
**Dep:** MAP-018, MAP-019, MAP-023, MAP-044
**AC:** pesan owner → run `trigger="chat"` tanpa tiket; PM reply muncul di conversation;
`comments[]` di run chat menulis komentar ke tiket target (author = PM); `status`/`mention`
di kontrak chat ditolak (run failed); pesan kedua saat run aktif tidak membuat run kedua;
guardrail `max_concurrent_runs` memblokir run chat dengan System message; attachment chat
upload/download/delete; `GET /workspaces/{id}/runs` menampilkan run chat; autocomplete
`@` di chat (3 tipe) dan komentar tiket (agent) dengan klik + Tab + panah; mention
ter-render jadi link.

## MAP-046 · Activate sprint kicks off its tickets · S · Engineer
When a sprint transitions to `active` (`PATCH /sprints/{id}`), the backend schedules a
run (`trigger="manual"`) for every ticket in that sprint that still needs work
(status in backlog/todo/in_progress/review/qa/security/blocked) and has an assignee
(enabled agent). Tickets already `done` are skipped — if every ticket is
done, nothing is triggered. Tickets without an assignee are skipped (no agent to run
them). Guardrail trips (e.g. `max_concurrent_runs`) are swallowed per ticket —
`schedule()` already wrote its own system comment naming the guardrail, and the
sprint activation itself must still succeed. Re-saving an already-active sprint with
other fields does NOT re-trigger (kick-off only on the transition into active).
**Dep:** MAP-023
**AC:** activating a planned sprint schedules runs for all unfinished assigned tickets;
done tickets and unassigned tickets are skipped; all-done sprint triggers nothing;
re-saving an active sprint triggers nothing; guardrail-blocked tickets don't fail the
activation.

## MAP-047 · Sprint gate refuses runs without touching ticket status · S · Engineer
Dogfooding fix: the `ticket_not_in_active_sprint` guardrail used to transition the
ticket to `blocked` on every trip (via `schedule()`'s shared `_block_ticket` path),
so an agent merely *attempting* to work a ticket in a planned sprint / backlog moved
its status to `blocked` — the agent effectively changed status on a ticket it wasn't
allowed to work. Now `schedule()` special-cases this guardrail: the run is refused
(409 `guardrail_blocked`) with a system comment naming the guardrail, but the
ticket's status is never touched (no `blocked` transition, no `status_change`
event). All other guardrails still block the ticket as before. ADR-013 updated.
**Dep:** MAP-027, ADR-013
**AC:** scheduling a backlog/planned-sprint ticket for a non-exempt role → 409 +
system comment, ticket status unchanged; other guardrail trips still block the
ticket; PM (sprint_creator_roles) exemption unchanged.

## MAP-048 · MCP identity: agent comments must not land as owner-authored · S · Engineer
Dogfooding incident: MCP `post_comment`/`update_ticket`/memory tools write on the
running agent's behalf, but opencode's MCP launcher did not forward the `env` block
of the per-run opencode.json config to the MCP subprocess — `MAP_AGENT_ID` was empty,
so `author_agent_id` was omitted and every MCP comment landed as owner-authored
(`author_agent_id=NULL`). Worse, `comments.py` treats NULL-author comments as
human-written, so each `@mention` inside those comments created `comment_mention`
rows AND triggered runs for the mentioned agents — every report got duplicated as an
owner comment + an agent comment, and mention runs kept firing. Fix: `mcp_config.py`
now passes `--workspace-id`/`--agent-id` as CLI flags (reliable channel) in addition
to env vars; `mcp_server.py` reads ids from CLI args as a fallback over env; and
`post_comment` fails loud (refuses to send) when no agent id resolved, instead of
silently degrading to owner authorship.
**Dep:** ADR-011
**AC:** MCP `post_comment` always stores `author_agent_id` (from CLI flag when env
missing); with no id at all the tool returns an error and sends nothing; env still
takes precedence over CLI flags; existing MCP e2e tests pass.

## MAP-048 · MCP identity: agent comments must not land as owner-authored · S · Engineer
Dogfooding fix. `app/mcp_server.py` reads `MAP_AGENT_ID`/`MAP_WORKSPACE_ID` from env,
but opencode's MCP launcher was observed dropping the `env` block of the per-run
config — so `AGENT_ID` was empty and every MCP `post_comment`/`update_ticket`/memory
write landed as **owner-authored** (`author_agent_id=NULL`). Because `comments.py`
treats NULL-author comments as human-written, each such comment created
`comment_mention` rows AND triggered mention runs — producing the observed
"owner comment + agent comment with the same text" duplication on tickets, plus
spurious runs. Fix: `mcp_config.py` now passes `--workspace-id`/`--agent-id` as CLI
flags (the reliable channel) in addition to env vars; `mcp_server.py` parses them
via `argparse` and prefers CLI args over env; `post_comment` fails loud (returns an
error, sends nothing) when no agent id resolved, instead of silently degrading to
owner authorship.
**Dep:** MAP-045, ADR-011
**AC:** MCP `post_comment` stores `author_agent_id` from CLI flags when env is
missing; env still takes precedence when both present; missing both → tool returns
an error and no comment is created; existing MCP tests (`test_mcp_server.py`)
pass.

## MAP-049 · PM deletes tickets + sprint date range from ```map · S · Engineer
Two PM powers. (1) New MCP tool `delete_ticket(key)` — permanent delete
(comments/attachments/runs cascade), gated in the backend API (`DELETE
/tickets/{key}?actor_agent_id=...`): only a PM agent may delete; owner path stays
ungated. Taught in `_mcp_tools_block` with strong usage guidance (only
duplicate/mistaken tickets, never in-flight work). (2) `sprints:` in the ```map
block now accepts `start_date`/`end_date` (YYYY-MM-DD): `SprintDraft` + parser +
`_get_or_create_sprint` (+ all call sites) persist the calendar range so the PM
sets the sprint's date range when creating it (previously UI-only via PATCH).
Malformed dates are ignored (sprint keeps NULL), never fail the run.
**AC:** PM can delete via MCP tool (agent-authored, gated); non-PM delete → 403;
`sprints:` with dates creates sprint with dates; without dates → NULL dates.

## MAP-050 · Built-in auto-check on stale tickets · L · Engineer
Proactive follow-up, built-in (not a Routine — works by default for every
workspace, no per-workspace setup). New background scheduler
`core/auto_check.py` (started in `main.py` lifespan, mocked in tests' conftest)
ticks every 30s and, per workspace, finds tickets in the ACTIVE sprint whose
status still needs work (in_progress/review/qa/security/blocked) and whose
`updated_at` is older than `auto_check_stale_minutes` (default 3), then schedules
a follow-up run (`trigger="auto"`) for the assigned agent IF idle; busy/disabled
agents and unassigned tickets fall through to the PM (idle only) — the PM owns
the whole sprint. Tunable via two new guardrails in Settings
(`auto_check_interval_minutes`, `auto_check_stale_minutes`; 0 disables).
Guardrail trips/paused workspaces are swallowed per ticket (schedule() already
comments). **Dep:** MAP-027, MAP-046
**AC:** stale in_progress ticket in active sprint → auto run for idle assignee;
fresh ticket → no run; busy assignee → PM run instead; interval 0 → disabled;
no active sprint → skipped.

## MAP-051 · Timeline sprint bar covers its tickets; dashboard alert list removed · · Owner
Frontend fixes. (1) Timeline `layoutScheduledRows`: the sprint bar width is now
`max(calendar_range_width, tickets_total_width)` — a sprint whose tickets'
combined estimated duration exceeds its calendar range (or has no dates) no longer
lets ticket blocks spill past the black bar. (2) Dashboard: the bottom
"alerts/informasi gagal" block (blocked tickets, failed runs, unstarted epics)
is removed. (3) Chat unread bullet: `lastAgentChatAt` is now stamped with the
event's own `created_at` (never `Date.now()`), and only when it's newer than the
stored value — the SSE replay on (re)connect no longer re-lights the header
bullet after the chat page was opened; the chat page also re-marks read when a
new agent message arrives while it's open.

## MAP-052 · Handoff role-mention to self loops forever · Bugfix · Engineer
Found by the MAP-050 suite: a report mentioning its OWN role (the only lead
mentioning "lead") resolved to itself and handed off forever (previously only
name-mentions self-dropped in `report.py`; with `max_handoff_depth` raised the
chain never stopped). `_handoff` now drops a role-mention that resolves to the
reporting agent itself, with a note — same rule as name self-mention.

## MAP-053 · Git menu — branch tree + commit history · M · Engineer
Read-only Git menu per workspace showing: branch list, lane-assigned commit graph
(gitk-style), paginated commit history (per branch, 100/page load-more), and
per-commit detail (metadata, changed files with +/− stats, unified diff).
Backend: `core/git.py` — `run_git()` with read-only allowlist, lane layout
algorithm, all commands read-only. `api/git.py` — 4 GET endpoints under
`/workspaces/{id}/git`. Frontend: `/w/[key]/git` page with SVG graph,
branch filter chips, commit list, detail panel with diff. Manual + 30s
refetch. **Dep:** MAP-006 (workspace CRUD)
**AC:** branch list shows all local branches with HEAD marker; graph renders
correct lane topology for branch/merge history; commit detail shows file stats
and diff; load more pagination works; non-git repo_path shows friendly empty
state; read-only enforcement (no write subcommands callable).
