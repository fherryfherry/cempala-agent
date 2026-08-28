# ADR — Architecture Decision Records

Format: decision · context · consequences · when to revisit.
All ADRs are **accepted** as of 2026-08-22 unless stated otherwise.

---

## ADR-001 · Next.js (frontend) + FastAPI (backend), two services

**Decision.** A Next.js App Router frontend separate from a Python FastAPI backend.

**Context.** The alternative was fullstack Next.js. Rejected because the backend's core is
orchestration: subprocess management, streaming, `asyncio`, and a state machine. Python is a good
fit for that.

**Consequences.** Two processes (wrapped by MAP-005 `make dev`), CORS must be handled, API types
are not shared automatically. The latter is accepted as-is — no OpenAPI→TS codegen until it
proves to be a problem.

**v0.2 note.** The "Python is a good fit for the agent loop" argument weakened after
[ADR-006](#adr-006): we no longer write an agent loop. What remains is subprocess management and
orchestration — still comfortable in Python, but no longer the deciding reason. The decision is
kept because changing it now buys nothing.

**Revisit when.** The backend shrinks to CRUD + spawning subprocesses, and you're tired of running
two processes.

---

## ADR-002 · SQLite as the only storage

**Decision.** SQLite via SQLAlchemy + Alembic. No Postgres, no Redis.

**Context.** Local application, single user, ≤3 concurrent runs, ≤5,000 tickets. A single
`map.db` file is easy to back up, inspect, and delete when an experiment fails.

**Consequences.** Limited concurrent writes — mitigation: WAL mode and short transactions. Event
volume accumulates; a retention strategy is needed if the DB grows. Alembic is used from the
start so a later migration to Postgres isn't a rewrite.

**Revisit when.** The portal leaves the laptop, or the `event` table exceeds a few GB.

---

## ADR-003 · SSE, not WebSocket

**Decision.** Realtime via one-way Server-Sent Events per workspace.

**Context.** All realtime here is server→client (opencode output, tool calls, status changes).
Client actions are already covered by REST. SSE has built-in auto-reconnect and `Last-Event-ID` —
exactly what's needed to resume a feed after a disconnect.

**Consequences.** No client push over the same channel (not needed). The 6-connection-per-domain
limit of HTTP/1.1 — hence **one** EventSource per workspace in a React context, not one per
component (MAP-024).

**Revisit when.** A bidirectional need appears, e.g. the owner intervening in a running run.

---

## ADR-004 · In-process orchestrator (`asyncio.Task`), not Celery/Redis

**Decision.** Each run is an `asyncio.Task` managing one opencode subprocess, inside the same
FastAPI process.

**Context.** Runs are I/O-bound (waiting on a subprocess). A distributed queue means adding a
broker, workers, state serialization, and a whole new class of bugs — for ≤3 concurrent runs on
one laptop.

**Consequences.** Restarting the backend kills all running runs — covered by MAP-026, which marks
them `interrupted` and leaves a system comment. Cannot scale to multiple machines. The entire
code path must be `async`, and subprocesses must use `asyncio.create_subprocess_exec`.

**Revisit when.** You need >20 concurrent runs, or runs must survive restarts.

---

## ADR-005 · No auth, single user

**Decision.** No login, no users, no RBAC in the MVP.

**Context.** It runs on one person's `localhost`. Auth is real work that adds none of the
requested capabilities.

**Consequences.** The backend binds to `127.0.0.1` and **must not** be exposed to the network.
This consequence got much sharper after [ADR-010](#adr-010): an unauthenticated endpoint that can
run arbitrary commands on your machine. The README states this explicitly. Every "who did this"
is currently `agent_id` or NULL (= owner); adding `user_id` later is a migration, not a rewrite.

**Revisit when.** A second person uses it, or the portal is deployed to a machine others can
reach.

---

## 6 · Not building our own coding agent — delegate to opencode

**Decision.** The portal has no tool-calling loop, no filesystem tools, and no LLM client. Each
run is one `opencode` process that receives a prompt and returns a result. `AgentTool` has one
active implementation: `OpenCodeTool`. (Replaces v0.1's plan of having a `SelfTool`.)

**Context.** v0.1 planned an in-house coding agent: a tool-calling loop against Ollama,
`read_file`, `write_file`, `edit_file`, `search`, `run_command` with an allowlist and a path
jail. That was re-building opencode — a project in its own right, with its own bug surface and
security surface — inside a project that is actually about orchestration, not about coding
agents.

What makes this portal valuable is tickets, handoffs, guardrails, and visibility. Not the quality
of its agent loop. A good agent loop already exists and is free.

**Consequences.**
- 8 tickets lost (Ollama client, tool-calling loop, filesystem tools, ticketing tool, path jail,
  command allowlist). ~9 working days removed from the MVP.
- We lose control over what happens inside a run: no step caps, no per-tool control. The
  guardrails that remain are time, cost, and handoff topology ([02-tsd.md](02-tsd.md) §6).
- We lose the path jail. Sandboxing becomes opencode's business, and opencode doesn't provide it
  ([ADR-010](#adr-010)).
- Agents become a black box that can't call our ticket API → a return contract is required
  ([ADR-009](#adr-009)).
- The portal becomes fully dependent on one external binary and its authentication.
- Coding output quality goes up, at no effort to us.

**Revisit when.** A need for fine-grained in-run control appears (e.g. denying a specific tool per
role) that can't be achieved via the prompt. That's a signal for an MCP server, not for
re-building the agent loop.

---

## ADR-007 · The adapter pattern is kept even with only one active implementation

**Decision.** The `AgentTool.run(ctx) -> AsyncIterator[Event]` protocol stays, with `OpenCodeTool`
and `ClaudeTool` as real implementations and `StubTool` for `agy`/`codex`.

**Context.** An abstraction with one implementation is usually over-engineering. Here it pays for
itself in two concrete ways: (a) users explicitly asked for a per-agent tool selection, so the
enum stays in the UI no matter what; (b) `StubTool` must fail cleanly through the same path,
not through a special `if` in the orchestrator.

If not for those, `OpenCodeTool` could simply be called directly.

**Consequences.** The UI shows not-yet-available tools as disabled, not silently failing (MAP-021).
The `Event`/`AdapterEvent` shape was originally opencode's JSON format, but `ClaudeTool` (the
second real adapter, shelling out to the `claude` CLI's `--output-format stream-json`) mapped onto
it without protocol changes — `assistant_text`/`tool_call`/`tool_result`/`error`/`run_ended` turned
out generic enough for both CLIs. The per-run MCP config *did* need a parallel code path
(`claude_mcp_config_path` in `mcp_config.py`), since Claude's `--mcp-config` uses a differently
shaped JSON (`mcpServers: {name: {command, args, env}}`) from opencode's (`mcp: {name: {type,
command: [...], env}}`).

**Revisit when.** A third real adapter arrives (`agy`/`codex` still stubbed), or a year passes
without one (at that point, remove the abstraction).

---

## ADR-008 · The `event` table as the single source of activity

**Decision.** Every event is persisted to `event` **before** being broadcast to SSE subscribers.
The live feed and post-refresh replay read the same table.

**Context.** The cheaper alternative: broadcast from memory, store only the summary. That means
what you see live differs from what you can read back — exactly when something goes wrong and you
most need the trace. With agents as black boxes ([ADR-006](#adr-006)), this trace is the only way
to understand why a run failed.

**Consequences.** High write volume from opencode output. MVP mitigation: batch insert every
~100 ms. A retention strategy is needed later ([ADR-002](#adr-002)). The payoff: every run can be
replayed in full.

**Revisit when.** Write load hurts responsiveness — first option: stop persisting raw text events,
keep persisting `tool_call`/`status_change`/`comment`/`error`.

---

## ADR-009 · Return contract via a ```map block, not an MCP server

**Decision.** Agents report back by closing their answer with one ```map block containing YAML
(`status`, `mention`, `summary`, `tickets[]`). The orchestrator parses and executes it.

**Context.** opencode is a black box ([ADR-006](#adr-006)) — it can't call our ticket API. Three
options: (a) a structured block at the end of the output, (b) an MCP server exposing ticketing
tools configured into opencode, (c) a second LLM pass to infer the result.

(c) rejected: adds cost and another layer that can misinterpret, for a problem that format can
solve. (b) is cleaner and removes the format risk entirely, but adds an MCP server, per-run
opencode configuration, and a new debugging surface — before we know whether the autonomous flow
itself works at all. (a) adds no infrastructure whatsoever.

**Consequences.**
- **This is the biggest technical risk in the MVP.** If a model doesn't comply with the format,
  the flow stops. Mitigation: make the format as simple as possible, repeat the contract at the
  end of every prompt, and a parse failure **always** blocks the ticket with a slice of the raw
  output — never guessed, never silent ([02-tsd.md](02-tsd.md) §4.3).
- Agents can only report **at the end**, not mid-work. A PM can't create tickets while thinking;
  it must gather everything into one closing block.
- MAP-033 measures format compliance as a number. That number decides whether the MCP server
  moves up in priority.

**Revisit when.** Format compliance is poor in dogfooding, or a need for mid-run agent reporting
appears. Both point to option (b).

---

## ADR-010 · Accepting `--auto` without a sandbox

**Decision.** opencode runs with `--auto` (approving all permission) and `--dir <repo_path>`. No
container, no sandbox. The risk is documented, not mitigated.

**Context.** No human approves permission dialogs in the autonomous flow, so `--auto` is required.
The alternative was running each run inside Docker with the repo mounted — safe, but it adds
image builds, opencode auth inside the container, and much more painful debugging, for an
application running on the owner's own laptop on their own repo.

**Consequences — state plainly.**
- An agent can run any command with the privileges of the user running the backend.
- `--dir` sets a working directory, **not** a boundary. Nothing stops an agent from touching
  files outside `repo_path`. `repo_path` validation in the API is a convenience check, not
  security.
- Therefore: backend binds `127.0.0.1` ([ADR-005](#adr-005)); explicit warnings in the README
  (MAP-001) and on the settings page that can't be dismissed (MAP-032); don't put production
  secrets inside `repo_path`; only run it on repos you trust.
- The kill switch is therefore not a convenience feature but the primary security control, and it
  must genuinely kill child processes — verified with `ps`, not by DB status (MAP-031).

**Revisit when.** The portal is used on third-party repos, on a shared machine, or by anyone other
than the owner. Any of the three immediately makes a sandbox mandatory.

---

## ADR-011 · MCP server for ticket/artifact/memory access, not for coding

**Decision.** Each opencode run gets one local MCP server (`app/mcp_server.py`) via a per-run
`opencode.json` config (`OPENCODE_CONFIG`), exposing read/write tools for tickets, artifacts, and
agent memory to the agent — proxied to the backend over HTTP (`MAP_API_BASE`, default
`127.0.0.1:8000/api`). Tools provided: `list_tickets`, `get_ticket`, `post_comment`,
`create_ticket`, `update_ticket`, `list_artifacts`, `read_artifact`, `get_memory`,
`create_memory`, `update_memory`.

**Context.** Previous routine runs depended entirely on the prompt: the agent had no way to read
ticket status (Board) or write follow-up comments, so a "check for stuck tickets and follow up"
routine failed — the agent refused to guess a status. ADR-009 chose the ```map block over MCP
because MCP infrastructure hadn't yet proven necessary; the dogfooding failure proved the need.

**Consequences.**
- The MCP server is a STDIO subprocess per run only (not TCP) — no new network surface. opencode
  spawns the server as a child process with `MAP_WORKSPACE_ID`/`MAP_AGENT_ID` in the env, so
  every tool is automatically scoped to the workspace and agent of the running run.
- All validation stays on the backend (state machine, role gates, mentions, PM approval). The MCP
  server is only a thin HTTP proxy; no business logic is duplicated.
- No auth on MCP (ADR-005): the MCP server can't be reached from outside, it can only be spawned
  by the backend itself.
- `update_ticket`/`post_comment` are automatically attributed to the running agent
  (`actor_agent_id`/`author_agent_id`) so activity stays recorded per agent.
- `create_ticket` creates a backlog ticket without auto-scheduling — agents can freely grow the
  backlog without triggering a run. Without `epic` (optional parameter, ADR-012) the ticket
  becomes a new top-level epic; with `epic` set, the ticket attaches as a child of an existing
  epic.
- Replaces the temporary need to inject a ticket list into routine prompts (an approach rejected
  because it bloated the prompt and remained blind to comments) — routine prompts are concise
  again, the agent reads the Board via a tool.

**Revisit when.** The MCP format fails in dogfooding (the agent can't find or doesn't use the
tools), or a need for mid-run interaction emerges (then: a richer MCP server, not heuristics).

---

## ADR-012 · Epic stays `Ticket` (reusable), sprint is a pure timebox

**Decision.** "Epic" does not become a new entity — it stays `Ticket` with `parent_id IS NULL`,
exactly as before. What changes: epics are now **persistent/reusable by design**, not a
one-shot container per request. Three new mechanisms enforce this:

1. An epic catalog (top-level tickets, ~100 most recently updated) and a sprint catalog (all
   names) are injected into the ```map contract for roles allowed `tickets[]` (pm/qa/pentester),
   with a MANDATORY reuse rule — the exact same pattern as the Artifact Groups catalog (ADR around
   `_map_contract_block`'s `groups_rule`, see docs/03-agent-design.md §3).
2. New `tickets[].epic` field (```map block) and new `create_ticket(epic=...)` parameter (MCP
   tool, ADR-011) — both let an agent attach a new ticket to an existing epic, instead of always
   being a child of the ticket being worked.
3. Sprints are enforced as a **pure timebox** — the old instruction asking the PM to state
   "each sprint's focus" is removed (it was why sprint names leaked feature names, e.g. "Sprint 2 -
   Quality & Security of Articles"). Scope/features are now exclusively the epic's business.

**Context.** Before this: the only way to create a top-level ticket was `is_new_epic: true`
(manual API) or the default `tickets[]`/`create_ticket` (agent) — both always created a new epic,
never reused one. The effect: every owner request (via chat or via MCP) created its own epic, and
an epic was never reused as a parent for subsequent tickets — contradicting the intended model:
workspace/project → epic (large feature area, reusable) → feature/story/bug/enhancement.

Two alternatives rejected:
- **Epic as a new entity** (its own table, without status/board column) — conceptually "more
  correct" but a big migration: new table, new API, migrate all old top-level tickets, new Epic
  management page. Rejected for this MVP feature — enough to keep epic as `Ticket` plus reuse
  tooling on top.
- **A new "Epics" page** (like Artifacts) — rejected; it's enough to fix the existing Epic
  dropdown in the Create Ticket dialog plus the existing badge on the Board/Timeline.

The reuse rule lives in the ```map contract (code), not per-workspace `workflow_prompt`
(Settings), for two reasons: (a) the epic/sprint catalog needs live data that only the
orchestrator can query — a static text field can't; (b) the contract block is always assembled
LAST in the prompt (after `workflow_prompt`), so this rule can't be silently overridden by a
workspace's custom `workflow_prompt`.

Bug found and patched in the same change: 1-level nesting (`_validate_parent`,
`nesting_too_deep`) was only enforced on the manual API path, never on the agent `tickets[]` path
— QA/Pentester reporting a bug from a ticket that already has a parent (feature/story under an
epic) silently created a grandchild (2 levels). Patched with a new default resolution: without an
explicit `epic:`, a new ticket attaches to `ticket.parent_id` when it exists (instead of
`ticket.id`) — staying flat under the same epic.

**Consequences.**
- `TicketDraft.epic` (parser, `core/report.py`) and the helpers `_resolve_ticket_parent`/
  `_resolve_epic_target` (orchestrator) — unknown keys or non-top-level epic values are skipped
  with a note in a system comment, not failing the whole report (same tolerance as other fields
  in the ```map block).
- The PM's "final plan" in the exploratory chat phase (before owner approval) must now name the
  target epic explicitly — part of the five required sections (requirement, goal, target epic,
  sprint breakdown, duration estimate), owner request outside the initial audit.
- `_maybe_wake_parent_pm`'s assumption "epic is always closed as soon as all children finish" is
  softened in the prompt (not code): the PM may keep an epic open if it's truly a large feature
  area that will keep receiving tickets.

**Revisit when.** The epic catalog grows very large (hundreds of epics) so that the ~100-most-
recent list no longer represents it, or the owner needs epic metadata that can't be overlaid on
`Ticket` (e.g. structured description, tags, release target) — only then does a separate `Epic`
entity make sense.

## ADR-013 · Agents can only be scheduled for tickets in the active sprint

**Decision.** New guardrail, `ticket_not_in_active_sprint`, checked in `check_guardrails()`
(`core/guardrails.py`) before a `Run` is created — at that point all 6 scheduling paths (manual,
retry, mention, handoff, auto `tickets[]`, wake-parent-PM) have already passed. Rule: a ticket
whose `sprint_id` is `NULL` (backlog) or points to a sprint that isn't `status == "active"`
cannot be run. The run is refused (409 `guardrail_blocked`) with a system comment naming the
guardrail — but the ticket's **status is never touched**: a ticket outside the active sprint is
not a failure, it's just not due yet, and an agent must not be able to move any status on it
(owner request after dogfooding: agents were observed moving such tickets to `blocked` via the
guardrail's old block-on-trip path). **Exemption**: any role in `workspace.sprint_creator_roles`
(default only `pm`) — those roles are responsible for planning sprints, so they must always be
able to respond to any ticket (including brand-new tickets from chat not yet triaged into any
sprint) to do that triage. This guardrail is always active, no toggle in
`workspace.guardrails`/Settings (owner request: this rule is team working policy, not a limit
to tune per workspace).

**Context.** Owner request: the PM decides when the next sprint becomes active (via the existing
`PATCH /sprints/{id}` mechanism, ADR in [03-agent-design.md](03-agent-design.md) §4); other
agents may only work what's in that active sprint — so the team doesn't silently work tickets
from a sprint that isn't due yet (or tickets never triaged at all) while the active sprint itself
isn't done.

Hidden consequence found and patched during implementation: the "start a new chat with the PM"
flow (`frontend/app/w/[key]/chat/page.tsx`) creates a ticket **without a sprint** and immediately
`@mention`s the PM — without the role exemption above, the PM would be blocked in the very first
conversation, before ever setting up a sprint. That's the direct reason the exemption is attached
to `sprint_creator_roles` (an existing concept used for something else: who may declare `sprints:`
in the ```map block) rather than hardcoding the role `"pm"`.

Two alternatives rejected:
- **Backlog excluded, only non-active sprints blocked** — simpler (no need to think about the
  chat flow above at all), but contradicts the owner's decision: backlog (not yet triaged into
  any sprint) is *less* ready to work than a planned-but-not-active sprint, so it should be
  blocked too, not excluded.
- **Guardrail configured per workspace** (new field in `workspace.guardrails`, toggle in
  Settings) — follows the other guardrails' pattern, but the owner explicitly didn't ask for this
  to be disableable; adding a toggle for something never requested as opt-out only grows the
  UI/API surface without a real need.

**Consequences.**
- `core/orchestrator.py::schedule()` now passes `agent.role` and `workspace.sprint_creator_roles`
  to `check_guardrails()` — two new keyword-only parameters, defaulting to `None`/`[]` so other
  callers aren't broken.
- `schedule()`'s `GuardrailBlocked` handler special-cases `ticket_not_in_active_sprint`: it
  writes the system comment but skips `_block_ticket()` — no status transition, no
  `status_change` event. All other guardrails still block the ticket as before.
- The `_make_ticket` fixture in nearly all orchestrator test files (`test_orchestrator.py`,
  `test_guardrails.py`, `test_handoff.py`, `test_kill_switch.py`, `test_loop_detector.py`,
  `test_run_retry_api.py`, `test_agent_memory_orchestrator.py`) now creates/reuses the
  workspace's active sprint by default unless `sprint_id` is explicitly overridden — some tests
  auditing sprint list contents (`test_updates_sprint_and_duration_apply_to_target`,
  `test_pm_tickets_with_sprint_creates_and_links_sprint`,
  `test_sprint_creator_roles_setting_gates_sprints_declaration`) were adjusted so they aren't
  affected by this sprint bootstrap.
- `_get_or_create_sprint` (orchestrator, agent-facing) unchanged — dates/status remain entirely
  outside the agent's control (see the separate sprint start/end date note).

**Revisit when.** The owner wants other agents (not just `sprint_creator_roles`) to be able to
respond to tickets outside the active sprint for specific cases (e.g. an urgent hotfix) — at that
point this guardrail may need a new explicit bypass path, rather than the existing role exemption.

## ADR-014 · Auto-retry failed runs with a re-adapted prompt, not a resume

**Decision.** A *retryable* failed run (missing/malformed ```map block, opencode subprocess
failure) is retried automatically up to `workspace.guardrails["max_auto_retries"]` (default 3,
per workspace, editable on the Settings page) before the ticket is blocked. Each retry is a new
`Run` row (`trigger="auto"`, `parent_run_id` chained to the failed run) scheduled through the
existing `schedule()` queue, so every attempt persists its own `event` trace, passes through
all schedule-time guardrails again, and is visible in the live feed like any other run. The
ticket is NOT blocked between attempts; only when the budget is exhausted does it get blocked,
with the reason naming `max_auto_retries`.

**Context.** In dogfooding, most failures were format/adaptation problems — the agent finished
but forgot or malformed its ```map block (MAP-033), or opencode crashed/errored. Those are
exactly the failures a re-run can fix, and opencode's own continuation session (`-s`) is
useless for them: the conversation that produced the bad output is the very thing to discard.
The retry instead rebuilds a fresh prompt (ADR-001's build-prompt contract unchanged) with a
"PERINGATAN: RUN SEBELUMNYA GAGAL" notice carrying the parent run's `error` and the tail of the
agent's accumulated `assistant_text` (replayed from the `event` table), plus an instruction to
re-read the ```map contract and change approach. No `-s` resume.

**Decisions inside the design:**
- **Retryable failure set is explicit, per call site** — decided in `_finish_run`'s failure
  branches (`retryable=`), not by string-matching `run.error`. Missing ```map / opencode
  failures retry; state-machine rejections, runtime guardrail trips (`cancelled`), user stops,
  and routine runs do not. Never retry a deliberate brake.
- **Manual Resume is the one exception to "never resume a stop"** — `POST /runs/{id}/retry`
  also accepts `cancelled` runs with `error=None`, which is exactly an owner-initiated Stop
  (the UI labels this Resume). It is an explicit owner decision, so it is not "resuming a
  brake"; the session-continuation lookup (`-s`) already handles the opencode side. A
  `cancelled` run with `error` set is a runtime guardrail kill and stays non-retryable.
- **Budget is per (ticket, agent)** — the attempt count walks the `parent_run_id` chain; the
  chain only exists between auto-retries, so any manual retry (`POST /runs/{id}/retry`) or
  `trigger="mention"` run breaks it and resets the budget — the same "fresh window on owner
  intervention" semantics as `loop_reset_at`/`handoff_depth`. Agent B's failures never consume
  agent A's budget on the same ticket.
- **No schema change** — reuses `trigger="auto"` + `parent_run_id`. `max_auto_retries=0`
  restores the pre-MAP fail → block behavior, which keeps the old tests meaningful.
- **Guardrails still apply to retries** — `max_cost_per_ticket` accumulates failed attempts'
  cost, so even a 3x retry budget is bounded by the cost brake.

## ADR-014 · Chat is a first-class entity, separate from ticket comments

**Decision.** Chat with the PM lives in its own tables (`conversation`,
`conversation_message`, `conversation_attachment`) instead of being comments on a
ticket. Chat runs are no-ticket runs (`Run.ticket_id=NULL`, new `trigger="chat"`,
`Run.conversation_id`). The PM's reply is the ```map `summary` written into the
conversation; the two-way follow-up onto real tickets is the ```map `comments[]`
field (previously routine-only, now also valid in chat runs). Owner comments on
tickets stay exactly as they were — manual comments on the ticket detail page.

**Context.** The original design reused the epic ticket as the chat container: every
owner chat message was a `Comment` on a PM-assigned epic, and the ticket detail page
rendered those comments. That coupling had three problems: (1) chat history polluted
the ticket's comment thread (and vice versa — ticket comments showed up in the chat
feed); (2) chat runs were subject to ticket guardrails (`ticket_not_in_active_sprint`,
`max_cost_per_ticket`, `handoff_depth`) that made no sense for a conversation; (3) the
"chat" concept was invisible in the data model — no way to list conversations, attach
files to a chat without attaching them to a ticket, or distinguish a chat message from
a comment.

**Alternatives rejected.**
- **Keep comments, add `chat_id` nullable** — less code, but the comment table stays
  the shared sink for both concepts; the separation is cosmetic, not structural.
- **Chat runs still on a ticket** — keeps the sprint guardrail problem and the
  ticket-as-chat-container smell; rejected for the same reasons as the status quo.

**Consequences.**
- New tables + migration; `run.trigger` gains `"chat"`; `Event.type` gains
  `"conversation_message"` (SQLite enums are unconstrained VARCHAR, so no ALTER
  needed).
- Chat runs only check `max_concurrent_runs` (now counting no-ticket runs too, so a
  chat run can't sneak past the cap). Failures land as System messages in the
  conversation — there is no ticket to block.
- One chat run per conversation at a time: a second owner message while a run is
  queued/running is persisted and answered by the in-flight run (its prompt includes
  the full transcript), not scheduled as a second run.
- Old chat data (comments on epic tickets) is NOT migrated — it stays as ticket
  comments, readable as history.
- The `_PM_MENTION_EXTRA_INSTRUCTIONS` exploratory-chat flow (owner chat on a ticket
  via `@mention`) remains for ticket-scoped conversations; the new chat page is the
  ticket-free channel.

## ADR-015 · Workspace and global settings move from the DB to `.cempala` YAML files

**Decision.** Per-workspace settings (`guardrails`, `workflow_prompt`, `sprint_creator_roles`,
`time_unit`, `timezone`, `main_branch`) move off the `workspace` table's columns to a YAML file at
`<repo_path>/.cempala/settings.yaml`, read/written via `core/settings_store.py`. The single global
setting (`orchestrator_model`) moves off the `global_setting` key-value table to
`~/.cempala/settings.yaml`. The public API contract is unchanged — `WorkspaceOut`/`WorkspaceUpdate`
and `GET/PUT /api/settings/orchestrator-model` keep the same shape; `app/api/workspaces.py`
composes DB identity fields (`id`, `name`, `key`, `repo_path`, `description`, `paused`,
`ticket_counter`, `created_at`) with the file-backed settings before responding.

Settings are keyed purely by a workspace's **current** `repo_path`, not any database identity.
Changing `repo_path` via PATCH does not migrate the old `.cempala/settings.yaml` to the new
location — if the new path already has one committed (a clone of a repo whose owner already
checked it in), that becomes the effective settings immediately; if not, the workspace reverts to
defaults until customized again. `create_workspace` never eagerly writes a settings file for the
same reason: a pre-existing committed file should simply be picked up on the next read.

`.cempala/settings.yaml` is owner-authored config, not agent output, unlike the
` ```map ` block `core/report.py` tolerantly parses. A malformed file raises `SettingsLoadError`
and surfaces as a 500 at the API boundary — never a silent fallback to defaults (CLAUDE.md: no
silent failure path). Two narrow, pre-existing exceptions keep their old "never raises" contracts
instead: `orchestrator._global_orchestrator_model()` (always returned `None` on any error, with a
5s in-process cache) and `core/auto_check.py`'s per-workspace scan tick (already documented as
"never block, never fail loudly" for paused workspaces/guardrail trips) — both now catch
`SettingsLoadError` the same way they already caught every other error, rather than becoming a new
way for one bad file to take down a run or a whole scan.

**Context.** Workspace settings were tied to this backend's own SQLite DB, which meant they lived
and died with one install — a teammate cloning the same repo into a fresh `map.db` started from
defaults every time. Storing them as YAML inside the project's own `repo_path` lets them travel
with the repo: commit `.cempala/settings.yaml` and every clone/install that points a workspace at
that repo picks up the same guardrails/workflow prompt/etc. Global (portal-wide) settings — today
just `orchestrator_model` — move to `~/.cempala/settings.yaml` for the same reason: a
user-machine-scoped default that shouldn't reset with the DB.

This is a deliberate, narrow exception to "the portal never touches files inside `repo_path`"
(CLAUDE.md, ADR-006's consequence): that line is about not giving *agents* filesystem tools, not a
literal ban on the backend's own bookkeeping — `.worktrees/` (`core/git.py`) already does exactly
this for git worktrees. `.cempala/` is the same category: backend-managed, not agent-facing.
Unlike `.worktrees/`, it is deliberately **not** gitignored — the whole point is for it to be
committed.

**Alternatives rejected.**
- **File as a cache, DB stays source of truth** — keeps the DB as the single source of truth and
  the file just for visibility/portability, but that defeats the actual goal (a teammate cloning
  the repo into a fresh DB still wouldn't get the committed settings) and adds a cache-invalidation
  problem for no benefit.
- **New settings only, existing DB fields untouched** — avoids the API composition/migration work,
  but leaves the exact problem (settings tied to one DB) unsolved for the fields that actually
  matter (guardrails, workflow prompt) and only relocates it for whatever new field would be added.
- **JSON instead of YAML** — `pyyaml` is already a dependency (used for the ```map contract), and
  YAML is more human-editable (comments, less punctuation) for a file meant to be hand-edited and
  reviewed in a diff.

**Consequences.**
- `Workspace` (db/models.py) keeps only identity/lifecycle columns; a data migration
  (`d97ad763fc97`) backfills every existing workspace's current settings into its
  `.cempala/settings.yaml` (best-effort per row — a missing/unwritable `repo_path` is skipped with
  a stderr warning, not a migration failure) before dropping the columns, and does the same for
  `orchestrator_model` into `~/.cempala/settings.yaml` before dropping `global_setting`. Downgrade
  restores the columns/table with their original defaults but does not restore values from YAML —
  there's no safe generic inverse.
- Every `orchestrator.py` call site that read `workspace.<field>` now loads
  `core.settings_store.load_workspace_settings(workspace.repo_path)` once per function instead;
  `core/guardrails.py`, `core/report.py`, and `agents/prompts.py` were already pure functions
  taking plain `dict`/`set`/`str` parameters and needed no changes.
- A `.cempala/settings.yaml` read-modify-write (PATCH `/api/workspaces/{id}`, PUT
  `/api/settings/orchestrator-model`) is guarded by an in-process `asyncio.Lock` keyed by the
  resolved settings path — this backend is single-process, so no cross-process lock is needed;
  `os.replace()` already makes each individual write atomic, so plain reads never see a torn file.
- End users should commit `.cempala/settings.yaml` to their own project's repo if they want
  settings to travel with it, and should check their project's own `.gitignore` doesn't already
  blanket-ignore dotfolders.

**Revisit when.** A second global or per-workspace setting is added that genuinely needs to be
DB-backed (e.g. something that must never be hand-editable, or that needs a foreign key) — at that
point a hybrid store may be worth it, rather than forcing everything through one mechanism.
