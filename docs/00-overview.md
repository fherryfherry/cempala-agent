# CEMPALA — Overview

A Jira-like portal for running a "software team" staffed entirely by AI agents.
You create tickets, the agent team (PM, Lead Engineer, Engineer, Designer, QA, Pentester) works
them autonomously inside a local repo folder, and you watch everything in real time.

## Why it exists

Running many AI agents today means many terminals, no shared memory, and no trace of
who did what. CEMPALA gives them one shared work board: tickets, comments, mentions,
statuses, and one shared working directory.

## Principles

1. **We do not build a coding agent.** The portal assembles a prompt, hands it to an external
   coding tool (opencode), and receives the result. What we build: tickets, orchestration, guardrails,
   visibility.
2. **One workspace = one local repo folder.** Agents work on real files, not a simulation.
3. **Everything that happens becomes an event.** The `event` table is the single source for the feed,
   replay, and debugging.
4. **Autonomous, but with brakes.** Agents may assign and hand off on their own; guardrails (timeout,
   cost budget, loop detector, kill switch) are not optional.
5. **MVP first.** Ship fast, then expand.

## MVP shape

- Multi-workspace, no login (single local user).
- Manual agent setup: pick role, model, and coding tool per agent.
- Coding tools that work in the MVP: **`opencode`** only. `claude` / `agy` / `codex` can be selected
  in config but are not executed yet.
- Models come from `opencode models` (`provider/model`). The portal stores no LLM credentials
  at all — that's `opencode auth`'s job. Ollama Cloud models appear after the `ollama` provider
  is configured in opencode.
- Agents report back via a ```map block at the end of their answer: target status, who is mentioned,
  a summary, and any sub-tickets to create.
- Ticketing: ticket number, title, description, attachments, comments, mentions, assignee.
- Real-time: activity feed + streaming per-agent output via SSE.

> **Security warning.** opencode runs with `--auto`, meaning the agent approves all
> permissions itself and can run any command with your user's privileges. `--dir` is a working
> directory, **not** a sandbox. Run only on repos you trust, on your own machine, and never
> expose the backend to a network. Details: [02-tsd.md](02-tsd.md) §7.

## Glossary

| Term | Meaning |
|---|---|
| **Workspace** | A project. Has a `repo_path` = the local folder where agents work. |
| **Agent** | One AI worker. Has a role, model, coding tool, system prompt. Belongs to one workspace. |
| **Role** | PM, Lead Engineer, Engineer, Designer, QA, Pentester. |
| **Ticket** | A unit of work. Has a key like `MAP-001`, status, assignee, parent (for sub-tickets). |
| **Run** | One opencode process: agent X works ticket Y. Has a status, session, cost. |
| **Event** | One occurrence inside a run: agent output, tool call, status change, comment. |
| **Coding tool** | The external binary that executes the work. MVP: `opencode`. |
| **```map block** | The reply contract: YAML at the end of the agent's answer containing `status`, `mention`, `summary`, `tickets[]`. |
| **Handoff** | An agent moves a ticket to another role via `status` + `mention` in the ```map block. |

## Document index

| Document | Contents |
|---|---|
| [01-prd.md](01-prd.md) | Product requirements, user stories, MVP scope |
| [02-tsd.md](02-tsd.md) | Architecture, data model, API, agent runtime, guardrails |
| [03-agent-design.md](03-agent-design.md) | Each agent's roles & prompt, ticket state machine, handoff rules |
| [04-tasks.md](04-tasks.md) | Task breakdown `MAP-001`…`MAP-033` |
| [05-roadmap.md](05-roadmap.md) | Milestones M0–M3 + definition of done |
| [06-adr.md](06-adr.md) | Architecture decision records |

## Status

Planning phase. No code yet. Start from [05-roadmap.md](05-roadmap.md) → M0.
