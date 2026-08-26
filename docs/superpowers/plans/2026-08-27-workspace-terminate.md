# Workspace Terminate Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single "Terminate workspace" button in workspace settings that pauses the workspace, waits for all runs to stop, deletes all workspace data and the workspace itself, then redirects to the homepage workspace list.

**Architecture:** A new backend endpoint `POST /workspaces/{id}/terminate` performs the whole teardown server-side (pause → poll runs → delete data + workspace → 204). The frontend calls it once, invalidates the workspaces query, and `router.push("/")`. The repo folder on disk is never touched.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), Next.js App Router + TanStack Query + shadcn/ui (frontend), pytest (backend tests).

---

### Task 1: Backend — `terminate` endpoint

**Files:**
- Modify: `backend/app/api/workspaces.py`
- Test: `backend/tests/test_workspaces_api.py`

- [ ] **Step 1: Write the failing tests**

Append these tests to `backend/tests/test_workspaces_api.py`. Add `import asyncio` at the top if not present.

```python
def test_terminate_deletes_workspace_and_all_data(client, tmp_path):
    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    ticket = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": "t1", "is_new_epic": True}
    ).json()
    client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "hi"})
    client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"})

    resp = client.post(f"/api/workspaces/{ws_id}/terminate")
    assert resp.status_code == 204

    assert client.get(f"/api/workspaces/{ws_id}").status_code == 404
    assert client.get(f"/api/workspaces/{ws_id}/tickets").status_code == 404
    assert client.get(f"/api/workspaces/{ws_id}/agents").status_code == 404
    assert client.get(f"/api/tickets/{ticket['key']}").status_code == 404


def test_terminate_does_not_touch_disk(client):
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        marker = os.path.join(tmp_dir, "keep-me.txt")
        with open(marker, "w") as f:
            f.write("hello")

        create = client.post(
            "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": tmp_dir}
        )
        ws_id = create.json()["id"]

        resp = client.post(f"/api/workspaces/{ws_id}/terminate")
        assert resp.status_code == 204

        assert os.path.isdir(tmp_dir)
        assert os.path.isfile(marker)


def test_terminate_missing_workspace_404(client):
    resp = client.post("/api/workspaces/nope/terminate")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_workspaces_api.py -k terminate -v`
Expected: FAIL with 404/405 (route not found) — the endpoint doesn't exist yet.

- [ ] **Step 3: Write the endpoint**

In `backend/app/api/workspaces.py`:

1. Update the imports to include `asyncio`, `Conversation`, and `Routine`:

```python
import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models import (
    ArtifactGroup,
    Conversation,
    Routine,
    Run,
    Sprint,
    Ticket,
    Workspace,
)
from app.db.session import get_session
from app.schemas.workspace import (
    DEFAULT_GUARDRAILS,
    DEFAULT_WORKFLOW_PROMPT,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)
```

2. Add a helper to build the workspace-scoped run query (mirrors `list_runs` in `app/api/runs.py`), a module-level timeout constant, and the terminate endpoint after `delete_workspace`:

```python
# How long terminate waits for running/queued runs to stop before giving up.
# Module-level so tests can monkeypatch it to a small value.
_TERMINATE_TIMEOUT_SEC = 60.0


def _workspace_runs_stmt(workspace_id: str):
    """Select every run belonging to a workspace — ticket, chat, and routine runs.

    Run has no direct workspace_id; it links via Ticket (ticket runs), Routine
    (routine runs), or Conversation (chat runs). Mirrors list_runs in app/api/runs.py.
    """
    return (
        select(Run)
        .outerjoin(Ticket, Run.ticket_id == Ticket.id)
        .where(
            (Ticket.workspace_id == workspace_id)
            | (
                Run.routine_id.in_(
                    select(Routine.id).where(Routine.workspace_id == workspace_id)
                )
            )
            | (
                Run.conversation_id.in_(
                    select(Conversation.id).where(Conversation.workspace_id == workspace_id)
                )
            )
        )
    )


@router.post("/{workspace_id}/terminate", status_code=204)
async def terminate_workspace(workspace_id: str, session: AsyncSession = Depends(get_session)):
    """Permanently delete a workspace and all its data.

    Pauses the workspace (kill switch), waits for every running/queued run to
    actually stop (so a background run's own DB writes can't race the delete out
    from under it), then deletes all tickets/sprints/artifact groups and the
    workspace row (cascades to agents, runs, events, conversations, routines,
    memories). The repo folder on disk is NOT touched.
    """
    from app.core import orchestrator  # deferred: circular import otherwise

    ws = await _get_workspace_or_404(session, workspace_id)
    ws.paused = True

    runs = (await session.scalars(_workspace_runs_stmt(workspace_id))).all()
    for run in runs:
        if run.status == "running":
            await orchestrator.stop(run.id)
        elif run.status == "queued":
            await orchestrator.cancel_queued(run.agent_id, run.id)
            run.status = "cancelled"

    await session.commit()

    # Wait for running runs to actually finish (their _finish_run commits before
    # the status leaves running/queued, so once none remain it's safe to delete).
    deadline = asyncio.get_event_loop().time() + _TERMINATE_TIMEOUT_SEC
    while True:
        active = await session.scalar(
            _workspace_runs_stmt(workspace_id).where(
                Run.status.in_(("running", "queued"))
            )
        )
        if active is None:
            break
        if asyncio.get_event_loop().time() >= deadline:
            raise AppError(
                409,
                "runs_in_progress",
                "runs did not stop within the timeout; workspace is paused, try again",
            )
        await asyncio.sleep(0.5)

    await session.execute(delete(Ticket).where(Ticket.workspace_id == workspace_id))
    await session.execute(delete(Sprint).where(Sprint.workspace_id == workspace_id))
    await session.execute(delete(ArtifactGroup).where(ArtifactGroup.workspace_id == workspace_id))
    await session.delete(ws)
    await session.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_workspaces_api.py -k terminate -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full workspaces test file**

Run: `cd backend && pytest tests/test_workspaces_api.py -q`
Expected: PASS (all existing + new tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/workspaces.py backend/tests/test_workspaces_api.py
git commit -m "feat: add POST /workspaces/{id}/terminate endpoint"
```

---

### Task 2: Backend — terminate with a running run

**Files:**
- Modify: `backend/tests/test_workspaces_api.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_workspaces_api.py`. This seeds a `running` Run row
directly via the DB session (no subprocess), then makes terminate's `stop` flip
it to `cancelled` shortly after polling starts.

```python
def test_terminate_waits_for_running_run_then_succeeds(client, tmp_path, monkeypatch):
    import asyncio

    from app.core import orchestrator
    from app.db import session as db_session
    from app.db.models import Agent, Run, Ticket

    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    async def _seed_running_run():
        async with db_session.async_session() as s:
            agent = Agent(
                workspace_id=ws_id, name="eng", role="engineer",
                model="opencode/big-pickle", tool_kind="opencode", status="working",
            )
            s.add(agent)
            await s.flush()
            ticket = Ticket(
                workspace_id=ws_id, key="ACM-1", title="t", status="in_progress",
            )
            s.add(ticket)
            await s.flush()
            run = Run(
                ticket_id=ticket.id, agent_id=agent.id, status="running",
                trigger="manual", tool_kind="opencode", model="opencode/big-pickle",
            )
            s.add(run)
            await s.commit()
            return run.id

    run_id = asyncio.run(_seed_running_run())

    # Simulate the run finishing shortly after terminate starts polling.
    async def _fake_stop(run_id):
        async def _finish():
            await asyncio.sleep(0.2)
            async with db_session.async_session() as s:
                r = await s.get(Run, run_id)
                r.status = "cancelled"
                await s.commit()
        asyncio.create_task(_finish())
        return True

    monkeypatch.setattr(orchestrator, "stop", _fake_stop)

    resp = client.post(f"/api/workspaces/{ws_id}/terminate")
    assert resp.status_code == 204
    assert client.get(f"/api/workspaces/{ws_id}").status_code == 404
```

Note: this test needs `from app.db import session as db_session` and
`from app.db.models import Agent, Run, Ticket` imported at the top of the test
file. Add them if missing.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_workspaces_api.py -k waits_for_running -v`
Expected: FAIL — the endpoint doesn't exist yet (405).

- [ ] **Step 3: Run test to verify it passes**

Run: `cd backend && pytest tests/test_workspaces_api.py -k waits_for_running -v`
Expected: PASS (endpoint from Task 1 handles the wait).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_workspaces_api.py
git commit -m "test: terminate waits for running run before deleting"
```

---

### Task 3: Backend — terminate timeout returns 409

**Files:**
- Modify: `backend/tests/test_workspaces_api.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_workspaces_api.py`:

```python
def test_terminate_timeout_409_keeps_workspace(client, tmp_path, monkeypatch):
    import asyncio

    from app.core import orchestrator
    from app.db import session as db_session
    from app.db.models import Agent, Run, Ticket

    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    async def _seed_running_run():
        async with db_session.async_session() as s:
            agent = Agent(
                workspace_id=ws_id, name="eng", role="engineer",
                model="opencode/big-pickle", tool_kind="opencode", status="working",
            )
            s.add(agent)
            await s.flush()
            ticket = Ticket(
                workspace_id=ws_id, key="ACM-1", title="t", status="in_progress",
            )
            s.add(ticket)
            await s.flush()
            run = Run(
                ticket_id=ticket.id, agent_id=agent.id, status="running",
                trigger="manual", tool_kind="opencode", model="opencode/big-pickle",
            )
            s.add(run)
            await s.commit()

    asyncio.run(_seed_running_run())

    # The run never actually stops — terminate must time out and leave the
    # workspace paused and intact. Shrink the timeout so the test is fast.
    import app.api.workspaces as ws_api

    monkeypatch.setattr(ws_api, "_TERMINATE_TIMEOUT_SEC", 0.5)

    async def _never_stop(run_id):
        return True

    monkeypatch.setattr(orchestrator, "stop", _never_stop)

    resp = client.post(f"/api/workspaces/{ws_id}/terminate")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "runs_in_progress"

    ws = client.get(f"/api/workspaces/{ws_id}").json()
    assert ws["paused"] is True
    assert client.get(f"/api/workspaces/{ws_id}/tickets").json() != []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_workspaces_api.py -k timeout_409 -v`
Expected: FAIL — endpoint doesn't exist yet (405).

- [ ] **Step 3: Run test to verify it passes**

Run: `cd backend && pytest tests/test_workspaces_api.py -k timeout_409 -v`
Expected: PASS (fast, ~1s thanks to the shrunk timeout).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_workspaces_api.py
git commit -m "test: terminate times out with 409 and keeps workspace paused"
```

---

### Task 4: Frontend — `terminateWorkspace` API client

**Files:**
- Modify: `frontend/lib/api.ts`

- [ ] **Step 1: Update `apiFetch` to handle 204**

`apiFetch` currently always calls `res.json()`, which throws on a 204 (no body). Update it to return `undefined` for 204:

```ts
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
    } catch {
      // response wasn't JSON; keep statusText
    }
    throw new ApiError(message, res.status);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
```

- [ ] **Step 2: Add `terminateWorkspace`**

Add after `resetWorkspace` (around line 129):

```ts
/** Permanently deletes the workspace and all its data (pauses, waits for runs
 * to stop, then deletes). The repo folder on disk is left intact. */
export function terminateWorkspace(workspaceId: string): Promise<void> {
  return apiFetch<void>(`/workspaces/${workspaceId}/terminate`, { method: "POST" });
}
```

- [ ] **Step 3: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/api.ts
git commit -m "feat: frontend API client for workspace terminate"
```

---

### Task 5: Frontend — Terminate workspace card

**Files:**
- Modify: `frontend/app/w/[key]/settings/page.tsx`

- [ ] **Step 1: Add imports and the card**

1. Add `useRouter` to the `next/navigation` import (line 4):

```tsx
import { useParams, useRouter } from "next/navigation";
```

2. Add `terminateWorkspace` to the `@/lib/api` import (lines 7-18):

```tsx
  resetWorkspace,
  resumeWorkspace,
  terminateWorkspace,
  updateWorkspace,
```

3. In `SettingsPage`, add `const router = useRouter();` after `const queryClient = useQueryClient();` (line 67).

4. Add `<TerminateWorkspaceCard workspace={workspace} />` after `<ResetDataCard workspace={workspace} />` (line 126).

5. Add the card component at the end of the file (after `ResetDataCard`):

```tsx
function TerminateWorkspaceCard({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const mutation = useMutation({
    mutationFn: () => terminateWorkspace(workspace.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setConfirmOpen(false);
      toast.success("Workspace terminated");
      router.push("/");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Terminate failed");
    },
  });

  return (
    <Card className="border-red-300 dark:border-red-900">
      <CardHeader>
        <CardTitle>Terminate workspace</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-zinc-600">
          Permanently deletes &quot;{workspace.name}&quot; and everything in it — every
          ticket, chat, and activity record, plus the workspace itself. This pauses the
          workspace, stops all running agents, and cannot be undone. The repo folder on
          disk is left intact.
        </p>
        <div>
          <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
            <DialogTrigger render={<Button variant="destructive">Terminate workspace</Button>} />
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Terminate &quot;{workspace.name}&quot;?</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-zinc-600">
                This pauses the workspace, kills all running agents, and permanently
                deletes every ticket, chat, and activity record along with the workspace
                itself. It cannot be undone. The repo folder on disk is not deleted.
              </p>
              <DialogFooter>
                <Button variant="outline" onClick={() => setConfirmOpen(false)}>
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  disabled={mutation.isPending}
                  onClick={() => mutation.mutate()}
                >
                  {mutation.isPending ? "Terminating…" : "Terminate workspace"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/w/[key]/settings/page.tsx
git commit -m "feat: add terminate workspace card to settings"
```

---

### Task 6: Docs — API spec

**Files:**
- Modify: `docs/08-api-spec.md`

- [ ] **Step 1: Document the terminate endpoint**

Add after the "Delete workspace" section (around line 65):

```markdown
### Terminate workspace
```
POST /workspaces/{id}/terminate
```
Pauses the workspace (kill switch), waits for all running/queued runs to stop
(timeout 60s), then deletes every ticket/sprint/artifact group and the workspace
itself (cascades to agents, runs, events, conversations, routines, memories).
Does NOT delete the repo folder. Returns 409 `runs_in_progress` if runs don't
stop within the timeout (workspace stays paused).
Response: 204
```

- [ ] **Step 2: Commit**

```bash
git add docs/08-api-spec.md
git commit -m "docs: document POST /workspaces/{id}/terminate"
```

---

### Task 7: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && pytest -q`
Expected: PASS.

- [ ] **Step 2: Run the frontend type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 3: Run the frontend lint**

Run: `cd frontend && npm run lint`
Expected: PASS.

- [ ] **Step 4: Commit any remaining changes**

```bash
git add -A
git commit -m "chore: final verification"
```
