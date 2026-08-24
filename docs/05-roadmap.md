# Roadmap — MAP MVP

Version 0.2 · MVP
Tickets: [04-tasks.md](04-tasks.md) · ~26 working days for one person.

## Map

```
M0 Skeleton      MAP-001…005   ~3 days   ── running, but empty
M1 Ticketing     MAP-006…016   ~9 days   ── complete Jira-like, no agents
M2 Agent Runtime MAP-017…026   ~8 days   ── opencode working & monitored, manual trigger
M3 Autonomy      MAP-027…033   ~6 days   ── fully autonomous  ← MVP RELEASE
```

Each milestone can be demonstrated on its own. If M3 never gets finished, what already exists
remains useful: a ticket portal with an opencode agent run manually per ticket.

---

## M0 — Skeleton (~3 days)

**Goal.** Two processes start, the DB has a schema, a single command runs everything.

MAP-001 repo · MAP-002 FastAPI · MAP-003 DB schema · MAP-004 Next.js · MAP-005 dev runner

**Definition of done**
- `make dev` starts the backend on :8000 (binding `127.0.0.1`) and the frontend on :3000.
- The root page shows the backend status **and** the detected opencode version.
- `alembic upgrade head` creates all tables; `downgrade base` is clean.
- README: setup from scratch ≤5 steps, including `opencode auth login`, and contains the `--auto`
  security warning.

**Critical path.** MAP-003 — a wrong schema propagates to every milestone. Lock down the shape of
`run` (especially `session_id` and `report`) and `event` first.

---

## M1 — Ticketing (~9 days)

**Goal.** A Jira-like portal that humans can use, before a single agent runs.

Backend: MAP-006 workspace · MAP-007 models · MAP-008 agent · MAP-009 ticket ·
MAP-010 comment · MAP-011 attachment · MAP-012 state machine
Frontend: MAP-013 workspace+agent · MAP-014 board · MAP-015 ticket detail
Test: MAP-016

**Definition of done** (all tested manually without touching the terminal)
1. Create a workspace pointing at a real repo folder; a bogus `repo_path` is rejected with a clear
   message.
2. Add 6 agents with different roles; the model dropdown is populated from `opencode models`.
3. Create an epic + 3 sub-tickets, assign, attach a file, drag between columns.
4. `@agent-name` comments; autocomplete works; the mention is stored (does not trigger anything
   yet).
5. Dragging to a column whose transition is illegal is rejected with a toast.
6. `make test` is green.

**A cheap early check.** Before writing MAP-007, run `opencode models` yourself in the terminal
and make sure the provider you want (e.g. `ollama`) actually shows up. If not, take care of
`opencode auth` first — that prerequisite is outside our code.

---

## M2 — Agent Runtime (~8 days)

**Goal.** One opencode agent actually works one ticket in a real repo, the result comes back into
the ticket system, and you see it live. The trigger is still manual.

Foundation: MAP-017 EventBus · MAP-022 SSE · MAP-024 SSE frontend
Contract: MAP-018 ```map parser · MAP-019 prompt builder
Execution: MAP-020 opencode adapter · MAP-021 stub adapter · MAP-023 run API + orchestrator ·
MAP-026 recovery
UI: MAP-025 feed + run panel

**Sequence.** MAP-018 (parser) before MAP-019 (prompt) before MAP-020 (adapter). The return
contract is the hardest and most consequential part; building the adapter first means discovering
the wrong contract shape after everything is wired to it.

**Definition of done**
1. Clicking Run on a ticket assigned to Engineer → an opencode process runs in `repo_path`,
   modifies files, and closes with a ```map block.
2. That block is parsed: the `summary` comment appears on the ticket, the status moves to `review`.
3. The activity feed shows opencode output & tool calls <1 second after they happen.
4. Refresh the page: the feed history is intact (read from the DB).
5. Stop kills the opencode process (checked with `ps`); the agent returns to `idle`.
6. An agent that forgets to write the ```map block → ticket `blocked` + a system comment
   containing a slice of its raw output. No silent success.
7. An agent with tool `claude` → run `failed` with the message "adapter not available", not a 500.
8. Kill the backend mid-run, start it again → zero runs `running`.

**Main risk: format compliance.** This replaces the tool-calling risk of v0.1, and it looks the
same — small models often invent the format or forget to close the block. Test at least two
different models (one large, one small) before declaring M2 done. If the small model can't do it,
that's a limitation that must be written on the agent setup page (MAP-013), not a bug. If even the
large model fails often, that's a signal to move to an MCP server exposing ticketing tools
([ADR-009](06-adr.md)) — that decision gets made here, not after M3.

---

## M3 — Autonomy (~6 days) — **MVP RELEASE**

**Goal.** One click on an epic → the agent team completes it on its own, and you can stop them.

Brakes first: MAP-027 guardrail · MAP-028 loop detector · MAP-031 kill switch · MAP-032 settings UI
Then throttle: MAP-029 handoff engine · MAP-030 autonomous flow
Wrap-up: MAP-033 dogfood

**The order is non-negotiable.** Guardrails, the loop detector, and the kill switch **finished and
tested** before MAP-029/030 are switched on. Switching on autonomy without brakes means opencode
processes multiply while burning money — and each run here is a full process, not a single HTTP
call. That is why `max_concurrent_runs` defaults to 3.

**Definition of done**
1. One epic, one click on Run → PM breaks it into sub-tickets via `tickets[]`, agents work them,
   Lead reviews, QA tests, Pentester audits, PM closes. No intervention.
2. No dangling tickets: every ticket ends `done` or `blocked` with a written reason.
3. Every fired guardrail leaves a system comment naming which guardrail fired.
4. A Lead↔Engineer ping-pong loop stops at `loop_threshold` with the ticket `blocked`.
5. Pausing in the middle of the action → zero opencode processes within ≤5 seconds, agents
   `idle`, banner displayed.
6. An Engineer returning to the same ticket resumes the previous opencode session.
7. `docs/07-dogfood-report.md` is filled in, including the ```map block compliance rate.

---

## After MVP (not scheduled)

Ordered by value per effort, not commitment:

1. **Git operations** — branch per ticket, commit, diff in the UI. This is what makes Lead review
   real, and what keeps several Engineers working in parallel on one repo from overwriting each
   other. A strong candidate for promotion into the MVP if M3 dogfooding shows agents colliding
   on files.
2. **MCP server exposing ticketing tools** — replaces the ```map block. Agents can create tickets
   and change status mid-work, not only at the end, and the format risk is gone. Raise its
   priority if format compliance is bad in M2/M3 ([ADR-009](06-adr.md)).
3. **claude / agy / codex adapters** — after the opencode pattern proves itself; `AgentTool`
   already has a slot for them.
4. **Sandbox (Docker)** — if the portal is used on repositories that are not fully trusted.
5. **Sub-tickets deeper than 1 level.**
6. **Auth & multi-user** — once the portal leaves the laptop. Before that, don't expose it to the
   network.
7. ~~**Agent memory across tickets**~~ — **built as MAP-035**, narrower than originally pictured
   here to avoid the hallucination risk mentioned: not retrieval from old tickets, but verbatim
   notes the agent writes itself via the ```map block's `memory:` (docs/03-agent-design.md §3),
   limited in number when injected into the next prompt, and manually curatable (deletable) by the
   owner via Agents → Memory.
