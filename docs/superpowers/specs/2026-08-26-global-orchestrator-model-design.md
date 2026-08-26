# Global AI-Orchestrator (Default Model) Setting — Design

## Problem

Today an agent run's model comes exclusively from the per-workspace `Agent.model`
(`provider/model`, passed to `opencode run -m`). There is no global model
setting. The owner wants one on the global `/settings` page ("tambahkan setting
API untuk AI utamanya / AI Orchestrator").

Confirmed scope:

- One **global** setting, editable on `/settings`.
- It is the **default model for all agent runs** — chat (owner↔PM orchestrator),
  per-ticket runs, and routines — whenever an agent has no model of its own.
- When an agent *does* have its own model, that wins (no behavior change).
- LLM credentials remain in `opencode auth`; this setting only picks a
  `provider/model` string. It stores no secret.

## Non-goals

- No per-workspace override of this setting (owner chose global-only).
- No auth/credential storage.
- No changes to the `opencode models` source (still `backend/app/api/models.py`).

## Approach (chosen)

DB-backed key-value global setting + a resolution helper applied wherever a
run's model is stamped. This matches the existing "settings persisted in DB,
surfaced via API, editable in a UI card" pattern and survives restarts.

Rejected alternatives:

- **Env-var only** (`ORCHESTRATOR_MODEL` in `app/config.py`): trivial but not
  editable from the UI — fails the core requirement.
- **Reusing a workspace JSON column**: this is global, not workspace-scoped, so
  a workspace column is the wrong home.

## Backend

### 1. New `global_setting` table

Key-value storage for a small number of global settings.

| column | type | notes |
|--------|------|-------|
| `name` | `String`, primary key | e.g. `orchestrator_model` |
| `value` | `JSON` | scalar or structured value |
| `updated_at` | `DateTime(timezone=True)` | onupdate now |

Alembic migration `add_global_setting_table`.

Primary row used by this feature: `name = "orchestrator_model"`, `value` is a
`provider/model` string or `null` (absent).

### 2. New API router `backend/app/api/global_settings.py`

- `GET /api/settings/orchestrator-model` → `{"model": "provider/model" | null}`.
  Reads the `orchestrator_model` row; returns `null` when the row is absent.
- `PUT /api/settings/orchestrator-model` → same shape. Body `{"model": ...}`.
  Accepts a non-empty string or `null` (to clear). Upserts the row; `null`
  removes/deletes the row.

Registered in `main.py` with the other routers (prefix `/api`).

### 3. Default-resolution helper

In `orchestrator.py`, a small function:

```
def resolve_agent_model(agent_model: str | None) -> str | None:
    if agent_model:
        return agent_model
    # read global orchestrator_model (cached briefly); None if unset
    return _global_orchestrator_model()
```

`_global_orchestrator_model()` loads the `global_setting` row value, with a short
in-process cache (a few seconds) to avoid a query per run; can be a plain lookup
given run volume. Must never raise — return `None` on any error (a missing model
is surfaced as a run failure message downstream, not an exception here).

### 4. Agent.model becomes nullable

`Agent.model` (`db/models.py:108`) is currently `nullable=False`. Relax to
`nullable=True` via migration so "agent has no model → use global default" is
expressible. This is a **schema only** change; no existing rows are migrated
(their models stay set and continue to win).

Update the pydantic schema (`schemas/agent.py`):
- `AgentCreate.model: str | None = None`
- `AgentOut.model: str | None`
- `AgentUpdate.model` unchanged (already optional).

`agents.py` create/patch already handle `model` generically; verify the patch
route doesn't require it.

### 5. Apply resolution where a run's model is stamped

Four sites in `orchestrator.py` — replace `agent.model` with
`resolve_agent_model(agent.model)`:

- Line 650 — ticket run `Run.model`
- Line 698 — routine run `Run.model`
- Line 780 — chat run `Run.model`
- Line 1437 — `RunContext.agent_model`

Note: `schedule_chat` (chat runs) already derives from `pm.model` via the same
`Run.model` stamp, so a global fallback flows through with no extra code. The
`RunContext.agent_model` is what the opencode/claude adapters actually consume,
so resolving at 1437 is the authoritative point for execution; resolving at the
`Run.model` stamps keeps the stored `run.model` consistent with what ran.

### 6. Error edge case

If a run would end up with **no** model (agent has none and global is unset),
the run fails with a clear system message/comment stating a model is required
(and pointing at the global setting). Never fail silently. Concretely: resolve
before scheduling; if `resolve_agent_model` returns `None`, write a system
comment/message naming the missing-model condition and do not create the run.

## Frontend

New card **"AI Orchestrator (default model)"** on `frontend/app/settings/page.tsx`
(global settings).

- Dropdown populated from `GET /models` (already exists as `getModels()`).
- Current value from `GET /api/settings/orchestrator-model`.
- Save via `PUT /api/settings/orchestrator-model`.
- Helper text: this is the default model for any agent (PM, Engineer, etc.) that
  doesn't have its own model set; credentials come from `opencode auth login`.
- If `opencode models` is empty or the backend is down, show the note plus a
  free-text fallback input so a `provider/model` string can still be typed and
  saved.

## Data flow summary

```
/settings card → PUT /api/settings/orchestrator-model → global_setting row
schedule()/schedule_routine_run()/schedule_chat() → resolve_agent_model(agent.model)
execute() → RunContext.agent_model = resolve_agent_model(agent.model) → opencode run -m <model>
```

## Testing

- Migration applies cleanly up/down.
- `resolve_agent_model`: agent-model wins when set; global used when agent model
  is None; `None` when neither.
- API: GET returns stored value; PUT upserts; PUT `null` clears; validation.
- Orchestrator: run stamps global model when agent has none (ticket, routine,
  chat); clear system comment when neither model exists.
- Frontend: card renders, dropdown populates from `/models`, save/load round-trip.
- Follow the repo's existing test conventions (pytest, fake opencode binary).

## Docs

Update `docs/02-tsd.md` (settings/global config section) to mention the global
orchestrator-model setting and default-resolution rule.
