# Agent Memory Count on Agents Page

Date: 2026-08-23

## Problem

The Agents page lists each agent as a card with a "Memory" button that opens a
dialog. There is no way to see, at a glance, how many memory notes each agent
has — the count is only visible after opening the dialog.

## Goal

Show the memory count on the Memory button of each agent card, e.g. `Memory (3)`,
so the list communicates at a glance which agents have accumulated memory and
which are still at zero.

## Approach

Add `memory_count` to the agents **list** response, computed in one query via a
correlated subquery on `agent_memory`. The frontend renders it on the button.

### Why not fetch per-agent on the frontend?

The dialog already fetches `GET /agents/{id}/memory` when opened. Reusing that
endpoint for every card would cause N+1 requests on page load (one per agent),
which is wasteful for a full squad. A single count column in the list response
is one query regardless of squad size.

### Why a separate list schema?

`AgentOut` is also the response model for create/patch. Adding `memory_count`
there would make those responses carry a count that is stale the moment the
response is serialized (the count is computed from a subquery, not the ORM
object). A list-only schema keeps create/patch responses unchanged and avoids
implying the count is part of the agent record.

## Changes

### Backend

- `backend/app/schemas/agent.py`: add `AgentListOut` — same fields as `AgentOut`
  plus `memory_count: int`.
- `backend/app/api/agents.py`: `list_agents` selects agents with a correlated
  subquery count of `agent_memory` rows per agent, and returns `AgentListOut`
  objects. Create/patch endpoints keep returning `AgentOut`.

### Frontend

- `frontend/lib/api.ts`: add `memory_count: number` to the `Agent` interface
  (the list response now includes it).
- `frontend/app/w/[key]/agents/page.tsx`: Memory button label becomes
  `Memory ({agent.memory_count})`.

### Tests

- `backend/tests/test_agents_api.py`:
  - list returns `memory_count == 0` for a fresh agent;
  - list returns the correct count after memory notes are added (via the
    memory API), and the count is per-agent.

## Out of scope

- No change to the memory dialog, create/patch responses, or the memory API.
- No change to other pages that consume `listAgents` (they ignore the new field).
