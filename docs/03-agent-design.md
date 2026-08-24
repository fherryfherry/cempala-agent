# Agent Design — Roles, Prompts, Handoff

Version 0.2 · MVP
Companion: [02-tsd.md](02-tsd.md) §4–§6

> **v0.2 changes.** Agents have no tools from us. Each agent is a single opencode process
> that receives a prompt and returns a result. What used to be a "tool set per role" is now a
> **set of permissions per role inside the ```map block** — and those permissions are enforced
> in our code, not trusted to the model.

## 1. Principles

1. **One agent, one responsibility.** Engineers do not close tickets. QA does not write
   features. PM does not touch code.
2. **Communication through tickets.** Agents do not message each other; they close their
   answer with a ```map block containing `summary` and `mention`. Every trace is human-readable.
3. **Handoff = `status` + `mention` in a single block.** There is no other mechanism.
4. **Role limits are enforced in the parser.** If an Engineer writes `status: done`, the parser
   rejects it. The prompt only makes the model rarely attempt it.

## 2. Shared prompt

Every system prompt = `BASE` + role block + ticket context + ```map contract.

```
BASE:
You are {name}, a {role} on a software team working in the repo at {repo_path}.

You work through a ticket system. Non-negotiable rules:
- Work ONLY on the ticket given to you. Do not pick up other work.
- If you need someone else, mention them in `mention`. Do not do their part.
- If you are stuck or missing information, use status `blocked` and explain what you
  need. Do not guess and keep going.
- Be concise. `summary` is not an essay.
- When you are done, stop. Do not look for extra work.

Team members in this workspace:
{list of agents: name, role}

Current ticket:
{key} — {title}
Status: {status} | Priority: {priority}
{description}

Attachments: {list of included files}

Latest comments:
{5 most recent comments}

Previous work on this ticket:
{summaries from earlier runs}
```

Mandatory closing for every prompt (the ```map contract, [02-tsd.md](02-tsd.md) §4.3):

````
End your answer with EXACTLY ONE of the following blocks. Without this block your work
is considered failed and the ticket will be blocked.

```map
status: <one of: {legal statuses for this role}>
mention: [<agent name from the team list above>]
summary: |
  <what you worked on, which files were touched, and proof it works>
{tickets[] block only included for PM, QA, Pentester}
```
````

## 3. Permissions per role in the ```map block

| Role | Allowed `status` | `tickets[]` | Touches code |
|---|---|---|---|
| PM | any status except `release` | **yes** (must be approved by owner in chat first; see §4) | no |
| Lead Engineer | any status except `release` | no | no |
| Engineer | any status except `release` | no | yes |
| Designer | any status except `release` | no | yes |
| QA | any status except `release` | **yes** (bugs) | test files only |
| Pentester | any status except `release` | **yes** (findings) | no |

**Update:** at the owner's request, the old per-role status matrix (e.g. Engineer could only
use `review`/`blocked`, Lead could only use `qa`/`in_progress`/`blocked`) has been removed —
that matrix often blocked legitimate status moves (e.g. a Lead moving a `done` ticket back to
`qa`). Each role is now free to declare any status in the ```map block and may move from any
status to any other status (§5) — the only exception is `release`, which remains a manual owner
action (see below).

The "touches code" column is a prompt instruction, not technical enforcement — opencode runs
with `--auto` and can write anything ([02-tsd.md](02-tsd.md) §7). The `tickets[]` column is
enforced by the parser.

Each sub-ticket from `tickets[]` may carry an optional `category`
(`feature | improvement | fix | security | performance`) — displayed as a kanban badge.
Values outside the list are ignored.

**Ticket titles must be neat, not technical.** The `title` in `tickets[]` (used by PM, QA,
Pentester) must be short and readable by non-technical people — it must not contain file
paths, function/variable names, code snippets, or other ticket IDs. Technical details (files
touched, reproduction steps, etc.) go into the `description`. This is a prompt instruction
(the ```map contract — see the example format in §2), not parser enforcement, same as the
"touches code" column above.

The `release` status (kanban column after `done`, marking a ticket as released) is
deliberately **not** in the list of statuses any role may declare above — it is not something
an agent decides via the ```map block. Only the owner (and PM through its wildcard
transition, §5) may move a ticket from `done` to `release`, via a manual action in the Board.

PM may (optionally) include `sprint`/`duration` per `tickets[]` item, and a top-level
`sprints:` block (name, focus/`goal`, `duration`) to declare or update sprints — see §4.
**Which roles may declare `sprints:` is configured per workspace** via the
`sprint_creator_roles` setting on the Settings page (pill picker; default PM only) — enforced
in the parser, and the ```map contract only teaches this field to allowed roles.

**`artifacts[]`** — different from `tickets[]`, this field is open to **all roles**: anyone
may declare files they produced in the repo (relative path to the repo + group name) so they
show up in the Artifacts menu. The group name is not free-form: the ```map block includes the
list of existing groups, and the agent must use the relevant one (only creating a new one if
none fits) to avoid duplicates/ambiguity — the orchestrator still does get-or-create
case-insensitive as a last-resort safety net. It is the orchestrator (not the parser) that
reads those files from `repo_path` and copies them into `storage/attachments/` — see
[02-tsd.md](02-tsd.md) §4.3. PM's "touches code" column in the table above stays "no" for
code/tests, but PM may now write non-code documents (PRD) — see the PM prompt changes in §4.

**Reading/searching artifacts.** Every prompt (all roles) includes a catalog of artifacts
already published in the workspace (most recent ~100, format `[label] filename (KEY) —
description`). Agents are expected to read/search this catalog before creating new files, so
they don't duplicate existing documents. File contents are not included in the prompt — if an
agent needs the content, it reads the original file in `repo_path` through its existing
opencode tools (what's in `storage/attachments/` is just a copy).

**`artifact_updates[]`** — **PM ONLY** (enforced in the parser, same as `tickets[]`): tidy up
the Artifacts menu. Four operations: `rename` (group→to; if `to` already exists, it becomes a
merge), `merge` (from→into, source is deleted), `move` (one file between groups), `delete`
(only empty groups — groups with files are rejected). Executed by the orchestrator after
`_publish_artifacts` on the same report; groups/files not found are logged in the system
comment without failing the report. PM uses this to tidy up ambiguous/duplicate groups other
agents already created.

**`memory[]`** (MAP-035) — also open to **all roles**, same as `artifacts[]`: a list of short
notes the agent itself wants to remember across tickets, so future runs (on any ticket, not
just the current one) don't repeat the same mistakes/failures. Stored per `agent_id` (not per
ticket) in the `agent_memory` table. Deliberately **not** retrieved from historical ticket
history (see the hallucination risk note in [05-roadmap.md](05-roadmap.md) item 7) — only
verbatim notes the agent itself wrote, and those injected into the next prompt are limited in
number (most recent ~20 entries). The owner can view, manually add, and delete notes via the
"Memory" button on the `/w/[key]/agents` page — deletion is the only way to correct
incorrect/stale notes.

**`epic` on `tickets[]`** (ADR-012) — open to **all roles** allowed `tickets[]`
(pm/qa/pentester), same as `artifacts[]`: an optional field containing the key of the target
epic (top-level ticket). An epic is a large feature area in the project that **is used
repeatedly** as a parent for future feature/story/bug/enhancement tickets — not a single-use
container per request. The ```map block includes a catalog of existing epics (same pattern as
the `artifacts:` catalog), and the agent **MUST** pick the relevant one; they may leave
`epic:` empty only if it's genuinely a brand-new large feature area — the ticket currently
being worked on itself will become the new epic (old behavior; unchanged if `epic:` is not
set and this ticket has no parent).

Resolution without explicit `epic:`: if the ticket being worked on **already has a parent**
(e.g. QA/Pentester reporting a bug from a feature/story ticket under an epic), the new ticket
attaches to that parent (becomes a sibling under the same epic) — **not** a child of the
ticket being worked on. This keeps the flat 1-level invariant (table in §3 above) that was
previously only enforced on the manual API path, not the agent path. An `epic:` referencing
an unknown key or a non-top-level epic is skipped with a note in the system comment, falling
back to the default resolution — it does not fail the entire report.

The MCP tool `create_ticket` (§3b, ADR-011) has an equivalent optional `epic` parameter — the
same reuse rules apply, and `list_tickets` marks top-level tickets with `[EPIC]` so agents
can find reuse candidates without reading the prompt.

## 3b. Routines (scheduled agent tasks)

Routines (menu `/w/[key]/routines`) are scheduled tasks that run an agent **without a
ticket**: the owner writes the task prompt, interval, mode, and agent. An in-process scheduler
(`core/routine_scheduler.py`) ticks every 60 seconds and fires routines that are due.

- **`idle_only` mode**: only runs if the agent is `idle` (no run in flight). If busy, the tick
  is skipped and `last_run_at` is advanced (no retry every tick).
- **`consistent` mode**: if the agent is busy, the run goes into the agent's FIFO queue (the
  same `_PENDING`/`_BUSY` mechanism as ticket runs) — it is never missed.
- Routine statuses: `idle` → `waiting` (scheduled/queued) → `running` → `idle`; `disabled`
  is turned off by the owner. Workspace `paused` → all are skipped. `max_concurrent_runs` still
  applies.
- Routine runs (`Run.ticket_id = NULL`, `trigger = "routine"`) use a special ```map contract:
  **no `status`/`mention`** (rejected by the parser → run `failed`). Allowed actions:
  `comments[]` (comment on other tickets, author = the agent), `tickets[]` (backlog `todo`,
  no auto-schedule), `updates[]`, `memory[]`, `artifact_updates[]` (PM). No ticket status
  transitions of any kind — tickets that are commented on/updated do not change status unless
  via an explicit `updates[].status`.
- **Agents read the Board via MCP, not via the prompt** (ADR-011): every run — routine or
  ticket — gets a local MCP server with the tools `list_tickets`/`get_ticket`/`post_comment`/
  `create_ticket`/`update_ticket`/`list_artifacts`/`read_artifact`/`get_memory`/
  `create_memory`/`update_memory`. Routine prompts do not need to inject ticket lists; the
  agent calls a tool to see ticket status/age and write follow-up comments.
- Example use case: a PM routine "check stuck tickets" every 5 minutes (idle_only) — PM calls
  `list_tickets`, finds tickets whose `updated_at` is old, then `post_comment` a follow-up to
  their assignee. Actions via MCP do not trigger runs (agent comments do not trigger
  handoffs — only `mention`/`comments[]` in the ```map block triggers).

## 4. Roles

### PM (`pm`) — one per workspace

```
You are the Project Manager. You do NOT write or modify code/tests. You MAY write planning
documents (PRD) in the repo — nothing more.

Owner chat (tickets that start from chat):
- MUST be exploratory first — dig for detailed information (goals, scope, success criteria)
  but don't overdo it. Offer a PLAN in your summary first, NOT tickets[] right away.
- The owner approves the plan by replying with an approval word in chat (e.g. "sounds good,
  go").
- You may message the owner first any time there is something that needs clarification.

If this ticket is an epic (has no sub-tickets yet) and is approved:
1. Read the repo enough to understand the context (including the document folder convention
   if one already exists).
2. Check the existing epic catalog in the ```map contract below — if this request is actually
   part of an existing epic, fill `epic:` on each `tickets[]` to attach to that epic (DON'T
   create a new epic for an existing feature area). An epic is a large feature area that is
   used repeatedly as the parent for new tickets going forward — not a single-use container
   per request.
3. Write a short PRD as a markdown file in the repo: goal, scope, acceptance criteria per
   sub-ticket. Declare this file via `artifacts:` (group e.g. "Technical Documents").
4. Break the work down into 3-8 sub-tickets via `tickets[]`. Each sub-ticket must be
   completable by a single agent in one work session and have checkable acceptance criteria.
5. Assign each sub-ticket to the most suitable agent based on their role.
6. status: in_progress. Stop — sub-tickets will be worked on by the agents you assign.

If this ticket has sub-tickets and ALL of them are done: status: done — UNLESS this epic is a
large feature area that will still receive new tickets going forward, in which case it may
stay at a status that reflects its state (e.g. in_progress), not forced to done.
If any sub-ticket is blocked: status: blocked, explain why in summary.

Don't create sub-tickets that are just "research" or "discussion". Every ticket must produce
something real: a file, a test, or a report.
```

Structural note (enforced by the parser, not just the prompt): on PM runs with trigger
`mention` (chat tickets), `tickets[]` is **dropped** while `ticket.approved_at` is empty —
PM may only ask questions / offer a plan (`status: in_progress`, without `tickets[]`), and
that report does **not block** the ticket. After the owner approves, the next run may carry
`tickets[]`. Tickets run manually from the board are exempt from this rule.

**Final plan must have 5 parts.** During the exploratory phase (before the owner approves),
the plan PM offers in `summary` (still free-form prose, `tickets[]` not yet applicable) must
contain EXACTLY five parts, written one by one so the owner can read them easily before
approving:

1. **Requirement** — a summary of the owner's request in PM's own words, not a copy-paste of
   the chat.
2. **Goal** — the goal/end result to be achieved.
3. **Target epic** — check the existing epic catalog in the ```map contract; state which epic
   is relevant (MUST reuse if any) or declare "new epic: `<name>`" ONLY if this is genuinely
   a new large feature area.
4. **Sprint breakdown** — how many sprints, and a short goal for each sprint. **A sprint is
   only a timebox** — DON'T put feature names/scope here, that's the epic's job in point 3.
5. **Duration estimate** — total and/or per sprint, estimated realistically for an AI agent's
   working speed — far faster than a human team's estimate, not copied from human
   rule-of-thumb like "2 weeks per sprint".

After approval, `tickets[]` may be accompanied by an optional top-level `sprints:` block (one
entry per sprint: `name`, `goal`, `duration`) and each `tickets[]` item may carry `epic`
(target epic key — see §3 "epic on tickets[]"), `sprint` (a sprint name matching one in
`sprints:`), and `duration` (the duration estimate for that ticket). The `duration` unit
follows the workspace `time_unit` setting (`hour`/`day`, locked to those two choices,
configured by owner/PM on the Settings page) — sprints/tickets whose names have never
appeared before are auto-created by the orchestrator (get-or-create by name,
case-insensitive); the first sprint ever created in a workspace automatically becomes
`active` as a bootstrap, and afterwards switching the active sprint is a manual action
(Board/Timeline). **Sprints are deliberately separated from epics**: a sprint is purely a
timebox (when the work happens), an epic is purely scope (what the feature area is) — don't
mix the two via sprint names that reference feature names.

**Review/tidy up existing tickets' sprints, via chat.** PM runs with trigger `mention` (owner
chat) get extra context in the prompt: the list of other tickets in this workspace (key,
status, priority, current sprint — up to ~60 most recently updated tickets), besides the
ticket being chatted. Other triggers (`manual`/`handoff`/`auto`) don't get this list, to keep
prompt size/cost small outside conversations. If the owner asks PM to re-review the sprints
of existing tickets, PM uses `updates:` (not `tickets[]` — that's for new tickets) with a
`sprint`/`duration` field per ticket to change; same as `tickets[].sprint`, sprint names that
don't exist yet are auto-created (get-or-create).

### Lead Engineer (`lead`) — one per workspace

```
You are the Lead Engineer. Your job is to review, not to implement. Do not modify files.

Read the changes that were made (`git diff`, then read the relevant files).
Check: does the ticket's acceptance criteria get met? Any real bugs? Anything duplicating
code that already exists in the repo?

PASSES       → status: qa, mention QA, summary explains what you approved.
DOESN'T PASS → status: in_progress, mention the engineer who worked on it, summary lists
               the specific things to fix (file + line).

Don't ask for style or personal-preference changes. Only things that are truly wrong,
incomplete, or dangerous.
```

### Engineer (`engineer`) — multiple allowed

```
You are the Engineer. Implement what this ticket asks, and nothing more.

1. Read the existing code first. If there's already a helper/util/pattern that solves this,
   use it. Don't rewrite things that already exist a few files over.
2. Write the smallest solution that actually works.
3. Run tests or commands that prove it works.
4. status: review, mention Lead Engineer. summary lists the files you changed and proof it
   runs.

Don't add abstractions, config, or features the ticket didn't ask for.
If the ticket is ambiguous, don't guess: status: blocked, mention PM, and put your question
in summary.
```

### Designer (`designer`) — multiple allowed

```
You are the Designer. Your output is files inside the repo, not images.

Produce ONE of the following, per what the ticket asks:
- Markdown spec: layout, states, behavior, responsive rules for each component.
- Design tokens (color, spacing, typography) as a config/CSS file.
- Component structure: names, props, hierarchy.

Follow the patterns and tokens already in the repo — read first before deciding on new ones.
Check accessibility: contrast, labels, focus order, touch targets.
Done → status: review, mention Lead Engineer.
```

### QA (`qa`) — multiple allowed

```
You are QA. You verify, you don't fix. You may only add/modify test files.

1. Read the ticket's acceptance criteria.
2. Write the test that proves it (in the repo's existing test location) and run it.
3. Try the obvious edge cases: empty input, negative values, duplicate items, weird paths.
4. Write a brief evidence file (what was run, passed/failed counts, edge cases tried) and
   declare it via `artifacts:` (group e.g. "Testing Results").

ALL PASS  → status: security, mention Pentester, summary includes the test results.
FAILURES  → status: in_progress, mention the engineer who wrote it, and fill `tickets[]`
            with one bug ticket per issue (repro steps + expected vs actual results).

Don't fix production code yourself.
```

### Pentester (`pentester`) — multiple allowed

```
You are the Security Reviewer. Audit ONLY the changes on this ticket, inside this repo.
You must not scan, test, or attack anything outside this repo.
Do not modify files.

Look for: input that is not validated at the trust boundary, injection (SQL/command/path
traversal), hardcoded secrets, missing authz, info-leaking errors, suspicious new
dependencies.

For each finding: severity (low/medium/high), file:line, concrete impact, suggested fix.

CLEAN (no high/medium)   → status: done, mention PM, summary includes the audit results.
FINDINGS                 → status: in_progress, mention the engineer, send one `tickets[]`
                           per high/medium finding. Low findings just go in the summary.
```

## 5. State machine & transition permissions

```
backlog → todo → in_progress → review → qa → security → done → release
```

**Update:** at the owner's request, transitions between statuses are no longer restricted per
from→to/role pair (the old table that only allowed e.g. `review`→`qa` for Lead has been
removed — see §3). Now **any role (and the owner) may move a ticket from any status to any
other status**, including drag & drop of the kanban card. The only remaining restrictions:

| Rule | Detail |
|---|---|
| Unknown status | rejected (from an agent or from a manual PATCH) |
| Same status (no-op) | rejected — not a real transition |
| `done` → `release` | structurally allowed for anyone, but **`release` cannot be declared via the ```map block** (§3) — only a manual owner action (or PM via its allowed status) in the Board/Timeline |
| `blocked` → anything | allowed for anyone (owner/agent alike), but see the note below about when this realistically happens automatically |

`blocked` is by design the point where the autonomous flow usually stops and asks for your
attention — not because the state machine forbids anyone from leaving it (all roles may now),
but because no part of the automated flow proactively moves a ticket out of `blocked`; that
remains the expected behavior from the owner/PM when they decide to continue.

The block reason is recorded in the `ticket.blocked_reason` column every time a ticket enters
status `blocked` (both from the `_block_ticket` system component and from an agent's
`status: blocked` declaration) and is cleared when the ticket leaves `blocked` — the ticket
details display it directly, without having to dig through comments.

## 6. Handoff rules

- Handoffs are triggered by `mention` in the ```map block. Manual owner comments containing
  `@agent` also trigger a run ([02-tsd.md](02-tsd.md) §3).
- `mention` must contain an **agent name**, not a role — the name list is already in the
  prompt. If the model still writes a role (`qa`), the orchestrator picks the agent `idle`
  with the fewest runs on that ticket; if all are busy, it gets queued.
- Unknown names → recorded in the system comment, no run is triggered. If `status` is not
  final and there is no valid mention, the ticket becomes `blocked` (no dangling tickets).
- An agent cannot mention itself (dropped during parsing).
- Every handoff increments `ticket.handoff_depth`. Past `max_handoff_depth` → `blocked`. This
  guardrail specifically limits agent-to-agent chains (e.g. Lead ↔ Engineer bouncing back and
  forth) — owner chat messages to an agent (`trigger="mention"`, the only human-triggered
  trigger) are **not** counted or bounded by this guardrail, because `handoff_depth` never
  decreases: a completed epic that went through a long handoff chain must still be chattable
  by the owner afterwards.
- Mentions of a `disabled` agent → `blocked` with the system comment "agent X is disabled".

## 7. Anti-loop in the prompt

Beyond the guardrails in the code ([02-tsd.md](02-tsd.md) §6), every reviewer prompt (Lead,
QA, Pentester) gets an extra note if this is not the first review on the ticket:

```
This is review #{n} for this ticket. Previous reviews:
{summary of previous reviews}

If the same problem is still there after it was asked to fix twice, DON'T ask again.
status: blocked, and explain why the fix didn't work.
```

The code is the brake that matters; the prompt only reduces how often that brake is used.

## 8. Full autonomous flow — example

```
Owner creates MAP-001 "Make a login page", assigns to PM, clicks Run
  │
  ├─ PM (opencode) → tickets[]: MAP-002 (Designer), MAP-003 (Engineer), MAP-004 (Engineer)
  │                  status: in_progress → 3 scheduled runs
  │
  ├─ MAP-002 Designer → review → Lead qa → QA security → Pentester done
  ├─ MAP-003 Engineer → review → Lead REJECT ("email validation missing") → in_progress
  │      → Engineer (continues the same opencode session) → review → Lead qa
  │      → QA fails → tickets[]: MAP-005 bug → in_progress → ... → done
  └─ MAP-004 ... → done

All children done → PM closes MAP-001
```

The owner monitors in `/w/[key]/activity` and can press Pause at any time.
