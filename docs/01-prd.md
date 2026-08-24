# PRD — CEMPALA

Version 0.2 · MVP · 2026-08-22

> **v0.2 changes.** The portal does not build its own coding agent. Each agent is a single
> `opencode` process that receives a prompt and returns the result via a ```map block. What we
> build: tickets, orchestration, guardrails, visibility. ([06-adr.md](06-adr.md) ADR-006)

## 1. Problem

Running several AI agents for one project today means: many separate terminals,
each agent unaware of what the others are doing, no shared backlog, and no record of
decisions. The result is overlapping work, lost context, and no way to monitor progress
other than reading scrollback.

## 2. Goal

A single local portal where a team of AI agents with different roles collaboratively and
autonomously works a ticket backlog inside a real code repo, with full real-time visibility for
the owner.

### MVP success criteria

- The owner can create a workspace pointing at a local repo folder, add ≥4 agents with
  different roles & models, create 1 epic ticket, press "Run", and then **with no further
  intervention** the epic is split into sub-tickets, worked on, reviewed, and ends `done` or
  `blocked`.
- While that happens, the owner sees who is working on what, live.
- The owner can stop everything in 1 click.

### MVP non-goals

The quality of the resulting code is not a target yet — that's opencode's and your chosen
model's business. What gets tested: the flow runs, is observable, and can be stopped.

## 3. Persona

**Owner** (one person, the machine's owner). A developer. Wants to delegate work to the agent
team and monitor it, rather than typing prompts one by one. Runs the app on their own laptop.

**Agent roles** (not users, but system actors):

| Role | Responsibility | Multiple allowed? |
|---|---|---|
| PM | Break down epics into sub-tickets, prioritization, assign, close tickets | No (1 per workspace) |
| Lead Engineer | Review Engineer results, technical direction, approve/reject | No (1 per workspace) |
| Engineer | Code implementation | Yes |
| Designer | UI specs, assets, design markdown | Yes |
| QA | Write & run tests, report bugs | Yes |
| Pentester | Security audit, findings report | Yes |

## 4. MVP scope — Epics & User stories

### E1 — Workspace

- **US-1.1** As the owner I can create a workspace with a name, ticket key (e.g. `MAP`),
  and a `repo_path` pointing to a local folder.
  - AC: the path is validated to exist and be a directory; otherwise the form rejects it with a clear message.
  - AC: the ticket key is unique, 2–5 uppercase letters.
- **US-1.2** I can have multiple workspaces and switch between them.
  - AC: a switcher in the header; ticket/agent/feed data is filtered per workspace.
- **US-1.3** I can edit and delete a workspace.
  - AC: deleting a workspace deletes its agents, tickets, runs, and events (cascade). The repo
    folder on disk is **not** touched.

### Epic 2 — Agent setup

- **US-2.1** I can add an agent: name, role, model, coding tool, system prompt (optional override).
  - AC: the model list comes from `opencode models` (`provider/model` format); if it fails,
    show an error suggesting `opencode auth login` plus a manual text field.
  - AC: coding tool = `opencode` (active), `claude` | `agy` | `codex` (shown disabled with a
    "coming soon" label).
  - AC: empty system prompt → use the per-role default from [03-agent-design.md](03-agent-design.md).
- **US-2.2** I can edit / delete / disable (`enabled: false`) an agent.
  - AC: a disabled agent is never picked by the orchestrator and cannot be assigned.
  - AC: deleting an agent that has an active run is rejected; it must be stopped first.
- **US-2.3** I can see each agent's status: `idle` / `working` / `error` / `disabled`,
  along with the ticket currently being worked on.

### Epic 3 — Ticketing

- **US-3.1** I can create a ticket with title, description (markdown), priority, assignee.
  - AC: the key is auto-generated `<WORKSPACE_KEY>-<n>` in sequence, never reused.
- **US-3.2** Tickets can have a parent (epic → sub-ticket), one level only in the MVP.
- **US-3.3** I can attach files to a ticket.
  - AC: stored in `storage/attachments/<ticket_id>/`, not inside the workspace repo.
  - AC: size limit 25 MB/file; filenames are sanitized.
- **US-3.4** I can see a kanban board by status and drag tickets between columns.
- **US-3.5** Tickets have a status-change history visible on the ticket detail page.

### Epic 4 — Collaboration

- **US-4.1** I (or an agent) can comment on a ticket.
- **US-4.2** Comments can mention another agent with `@agent-name`.
  - AC: autocomplete in the composer from that workspace's agent list.
  - AC: mention stores `agent_id`, not just text.
- **US-4.3** A mention triggers a run for the mentioned agent.
  - AC: subject to the same depth & budget guardrails as any run (see Epic 6).
  - AC: an agent cannot trigger itself.

### Epic 5 — Agent runtime

- **US-5.1** I can run an agent on a ticket manually ("Run" on the ticket detail page).
- **US-5.2** One run = one `opencode` process in `repo_path`; all of its output becomes events.
  - AC: the prompt is assembled from the role's system prompt + ticket contents + comments + previous run results.
  - AC: ticket attachments are also passed to opencode.
  - AC: an agent returning to the same ticket resumes the previous opencode session.
- **US-5.3** The agent reports back via a ```map block at the end of its answer (`status`, `mention`,
  `summary`, `tickets[]`), and the portal executes it.
  - AC: `summary` becomes a comment on the ticket in the agent's name.
  - AC: `status` is validated against the state machine **and** the role's permissions; illegal → rejected.
  - AC: only PM, QA, and Pentester may create tickets via `tickets[]`.
  - AC: missing or malformed block → ticket `blocked` + system comment with the agent's raw output
    excerpt. Never guessed, never silent.
- **US-5.4** Autonomous flow: epic ticket → PM breakdown & assign → Engineer/Designer work →
  Lead review → QA → Pentester → PM closes.
  - AC: transitions only per the state machine in [03-agent-design.md](03-agent-design.md).
- **US-5.5** A failed run (non-zero exit, timeout, budget exhausted, broken map block) marks the ticket
  `blocked` and writes a comment with the reason. Never dies silently.
- **US-5.6** An agent using the `claude`/`agy`/`codex` tool produces a `failed` run with a
  "adapter not available" message, not a 500 error.

### Epic 6 — Guardrails & kill switch

- **US-6.1** Per-workspace global kill switch: one click stops all active runs.
  - AC: runs are cancelled, **opencode processes actually die** (verified with `ps`, not just a
    status in the DB), agents return to `idle`.
  - AC: the workspace enters `paused` mode; no new runs until resumed.
- **US-6.2** Per-run limits: timeout and cost budget.
- **US-6.3** Per-ticket limits: total cost budget and max handoff depth.
- **US-6.4** Loop detector: a ticket bouncing back and forth between two agents more than N times
  → `blocked` + system comment.
- **US-6.5** I can view & edit guardrail values per workspace on the settings page.
- **US-6.6** Concurrent-run limit per workspace (default 3) — every run is a full opencode process.

### Epic 7 — Real-time monitoring

- **US-7.1** A live activity feed per workspace: who, on what ticket, doing what.
- **US-7.2** I can open a single run and see the streaming opencode output and its tool calls,
  plus the parsed ```map block (or why parsing failed).
- **US-7.3** The feed survives a refresh (re-read from the `event` table).
- **US-7.4** I can stop one specific run without killing others.

## 5. Out of MVP scope

Self-built coding agent (tool-calling loop, filesystem tools) · claude/agy/codex adapters ·
Sandbox/container for opencode · MCP server with ticketing tools · Auth, multi-user, RBAC ·
Git operations (branch, commit, PR) · GitHub/Slack/Linear integrations · More than 1 level of
sub-tickets · Deployment/CI · Mobile view · Notifications · Full-text search.

## 6. Non-functional requirements

| Aspect | Target |
|---|---|
| Deployment | Local, single machine, two processes (`uvicorn` + `next dev`) via `make dev` |
| Prerequisites | `opencode` binary installed and authenticated with `opencode auth login`. The portal checks this in `/api/health`. |
| Scale | ≤5 workspaces, ≤3 concurrent runs per workspace, ≤5,000 tickets |
| Real-time | Events appear in the UI <1 second after they happen |
| Persistence | All state in SQLite. Restarting the backend does not lose tickets/comments/events. Runs active at restart are marked `interrupted`. |
| Security | Backend binds `127.0.0.1`, must not be reachable on a network. The portal stores no LLM credentials. Attachments are stored outside `repo_path`. **Agent filesystems are not sandboxed** — see §7 and [02-tsd.md](02-tsd.md) §7. |
| Observability | Every run can be fully replayed from the `event` table, including the prompt that was sent |

## 7. Risks

| Risk | Mitigation |
|---|---|
| **Model does not comply with the ```map block format** — the biggest MVP risk | Format is kept as simple as possible, the contract is repeated at the end of every prompt, parse failures always block the ticket with the raw output. Compliance rate is measured in dogfooding (MAP-033); if poor → move to an MCP server ([06-adr.md](06-adr.md) ADR-009) |
| **Agent can run arbitrary commands on your machine** (`--auto`, no sandbox) | Accepted deliberately ([06-adr.md](06-adr.md) ADR-010). Localhost-only backend, explicit warning in README + settings page, kill switch that actually kills processes |
| Autonomous agents bounce tickets back and forth forever | Loop detector + max depth + per-ticket cost budget (Epic 6) |
| Costs silently spiral | Per-run & per-ticket budgets from opencode's cost data, visible in the UI; `max_concurrent_runs` default 3 |
| Total dependence on one external binary | Accepted consequence of ADR-006. `/api/health` reports the opencode version; a changed `--format json` will surface as parse failures, not as wrong data |
| Model produces bad code | Out of MVP scope; that's Lead/QA's job in the next iteration |
