# Workspace Terminate Button — Design

## Problem

The workspace settings page (`/w/[key]/settings`) has a "Reset data" card and a
"Kill switch" (pause/resume) card, but no way to permanently delete a workspace
from the UI. The owner wants a single "terminate" button that tears the whole
workspace down: reset all workspace data, delete the workspace itself, and land
back on the homepage workspace list.

Confirmed scope:

- One **terminate** button in workspace settings.
- It runs the full flow automatically: pause (kill switch) → wait for runs to
  stop → delete all workspace data → delete the workspace → redirect to `/`.
- The repo folder on disk (`repo_path`) is **not** touched — consistent with the
  existing `DELETE /workspaces/{id}` behavior (US-1.3: "The repo folder is not
  deleted").

## Non-goals

- No change to the existing `DELETE /workspaces/{id}` endpoint (still used
  internally; terminate does not call it).
- No change to the existing "Reset data" card (kept as-is).
- No deletion of the repo folder on disk.

## Approach (chosen)

A single backend endpoint `POST /workspaces/{id}/terminate` that performs the
whole teardown server-side, plus a frontend card that calls it once and
redirects. This avoids a fragile frontend-orchestrated sequence (pause → poll →
reset → delete) that could be interrupted by a closed browser tab.

Rejected alternatives:

- **Frontend orchestration** (pause → poll runs → reset → delete from the
  browser): fragile — a closed tab mid-poll leaves the workspace half-torn-down.
- **Terminate refuses while runs are active** (409, mirroring reset): poor UX —
  the user clicks, gets an error, must pause manually, then click again.

## Backend

### New endpoint `POST /workspaces/{id}/terminate` (in `backend/app/api/workspaces.py`)

Returns `204` on success. Flow:

1. `_get_workspace_or_404` — 404 if the workspace doesn't exist.
2. Set `ws.paused = True` and cancel all running/queued runs for the workspace.
   Note: `pause_workspace` only cancels **ticket** runs (it joins on `Ticket`).
   Terminate must cancel **all** run types — ticket, chat, and routine — using
   the same workspace-scoped run query as `list_runs` in `app/api/runs.py`
   (outerjoin `Ticket` + `routine_id`/`conversation_id` subqueries). For each:
   signal `orchestrator.stop` for running runs, `orchestrator.cancel_queued` +
   mark `cancelled` for queued runs.
3. Poll run statuses until no run for this workspace is `running`/`queued`:
   - sleep ~0.5s between checks, overall timeout ~60s.
   - Once a run's status is no longer `running`/`queued`, its `_finish_run` has
     already committed — it is safe to delete the workspace out from under it.
   - On timeout: raise `AppError(409, "runs_in_progress", ...)` — the workspace
     stays paused, nothing is deleted.
4. Delete tickets, sprints, and artifact groups for the workspace (same deletes
   as `reset_workspace`), then `session.delete(ws)` (cascades to agents, runs,
   events, conversations, routines, memories). All in one transaction.
5. Commit. Return `204`.

The repo folder on disk is never touched.

## Frontend

### `frontend/lib/api.ts`

Add `terminateWorkspace(workspaceId: string): Promise<void>` — `POST
/workspaces/{id}/terminate`. Note: `apiFetch` currently always calls
`res.json()`; a `204` has no body. Handle the empty body (return `undefined`
when `res.status === 204`).

### `frontend/app/w/[key]/settings/page.tsx`

New card **"Terminate workspace"** below the existing "Reset data" card:

- Destructive button "Terminate workspace" → confirmation `Dialog` explaining:
  pauses the workspace, kills all running agents, permanently deletes every
  ticket/chat/activity record and the workspace itself, cannot be undone, and
  the repo folder on disk is left intact.
- On confirm: call `terminateWorkspace(workspace.id)`.
  - On success: invalidate `["workspaces"]` query, `router.push("/")` (homepage
    workspace list), toast success.
  - On error (e.g. 409 timeout): toast the error message, stay on the page.

Uses `useRouter` from `next/navigation` (already used elsewhere in the app).

## Data flow summary

```
settings card → POST /workspaces/{id}/terminate
  → pause (cancel running/queued runs)
  → poll until no running/queued runs (timeout 60s)
  → delete tickets/sprints/artifact_groups + workspace row (cascade)
  → 204
frontend → invalidate ["workspaces"] → router.push("/")
```

## Testing

In `backend/tests/test_workspaces_api.py`:

- Terminate deletes the workspace and all its data (tickets, sprints, agents,
  runs, events) — verify via subsequent 404/empty queries.
- Terminate with a running run (mock cancel so status transitions) still
  succeeds.
- Terminate with a run that never stops → 409 `runs_in_progress`, workspace
  still exists and is paused.
- Terminate does not touch the repo folder on disk (mirror
  `test_delete_does_not_touch_disk`).
- Terminate on a missing workspace → 404.

Follow the repo's existing test conventions (pytest, in-memory SQLite with FK
enabled).

## Docs

Update `docs/08-api-spec.md` (workspaces section) to document the new
`POST /workspaces/{id}/terminate` endpoint.
