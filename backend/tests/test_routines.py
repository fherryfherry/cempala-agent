"""Tests for the Routines menu: CRUD API, scheduler semantics, and routine runs
(no-ticket runs whose ```map block carries side-effect actions only).
"""

import stat
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core import routine_scheduler
from app.db import session as db_session
from app.db.models import Base
from app.db.session import get_session
from app.main import app

# conftest.py's autouse `_disable_background_schedulers` fixture monkeypatches the
# `run_scheduler` module attribute to a no-op for every test. Capture the real
# function here at collection time (before any per-test monkeypatch runs) so the
# loop-body test below can call the actual implementation.
_REAL_RUN_SCHEDULER = routine_scheduler.run_scheduler


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))

    db_path = tmp_path / f"{uuid.uuid4().hex}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    monkeypatch.setattr(db_session, "async_session", maker)

    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


def _write_python_binary(path, code):
    path.write_text(f"#!/usr/bin/env python3\n{code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _make_workspace(client, tmp_path, key="MAP"):
    resp = client.post(
        "/api/workspaces", json={"name": "Map", "key": key, "repo_path": str(tmp_path)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_agent(client, ws_id, role, name):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": "opencode/big-pickle", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_ticket(client, ws_id, title="Do the thing"):
    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": title, "is_new_epic": True}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _wait_for_run(client, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.03)
    raise TimeoutError(f"run {run_id} did not reach a terminal state within {timeout}s")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_routine_crud(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "Cek tiket macet",
            "prompt": "Cek semua tiket yang tidak bergerak dan komen.",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Cek tiket macet"
    assert body["status"] == "idle"
    assert body["interval_minutes"] == 5
    rid = body["id"]

    listed = client.get(f"/api/workspaces/{ws_id}/routines").json()
    assert len(listed) == 1

    resp = client.patch(
        f"/api/routines/{rid}",
        json={"interval_minutes": 10, "mode": "consistent", "status": "disabled"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["interval_minutes"] == 10
    assert resp.json()["mode"] == "consistent"
    assert resp.json()["status"] == "disabled"

    assert client.delete(f"/api/routines/{rid}").status_code == 204
    assert client.get(f"/api/workspaces/{ws_id}/routines").json() == []


def test_routine_requires_valid_agent(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "X",
            "prompt": "y",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": "does-not-exist",
        },
    )
    assert resp.status_code == 404


def test_routine_patch_each_field_and_404(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "A",
            "prompt": "p",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    ).json()["id"]

    resp = client.patch(
        f"/api/routines/{rid}",
        json={"name": "B", "prompt": "q", "agent_id": pm_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "B"
    assert body["prompt"] == "q"
    assert body["agent_id"] == pm_id

    assert client.patch("/api/routines/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/routines/nope").status_code == 404
    assert client.post("/api/routines/nope/run").status_code == 404


def test_routine_run_disabled_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "A",
            "prompt": "p",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    ).json()["id"]
    client.patch(f"/api/routines/{rid}", json={"status": "disabled"})

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "routine_disabled"


def test_routine_run_no_valid_agent_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={"name": "A", "prompt": "p", "interval_minutes": 5, "mode": "idle_only"},
    ).json()["id"]

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_agent"


def test_routine_run_paused_workspace_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "A",
            "prompt": "p",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    ).json()["id"]
    client.post(f"/api/workspaces/{ws_id}/pause")

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "workspace_paused"


# ---------------------------------------------------------------------------
# Routine run end-to-end (fake binary): comments on other tickets, backlog
# tickets[], updates, memory — no status transition anywhere.
# ---------------------------------------------------------------------------

_ROUTINE_ACTIONS_SCRIPT = '''
import json
text = """routine done

```map
summary: |
  Cek tiket macet: MAP-002 tidak bergerak, sudah dikomen.
comments:
  - ticket: MAP-002
    body: |
      Tiket ini tidak bergerak sejak lama. Tolong dicek.
tickets:
  - title: "Backlog dari rutinitas"
    description: "dibuat otomatis oleh rutinitas"
    priority: medium
updates:
  - ticket: MAP-002
    priority: high
memory:
  - rutinitas cek tiket macet sudah jalan
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''

_SIMPLE_ROUTINE_SCRIPT = '''
import json
text = """routine done

```map
summary: |
  rutinitas selesai
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''

_ROUTINE_BAD_STATUS_SCRIPT = '''
import json
text = """routine with status

```map
status: done
summary: |
  salah
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def test_routine_run_actions_end_to_end(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)  # MAP-001
    target = _make_ticket(client, ws_id, "Target macet")  # MAP-002

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "Cek tiket macet",
            "prompt": "Cek semua tiket yang tidak bergerak dan komen.",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    rid = resp.json()["id"]

    script = _write_python_binary(tmp_path / "opencode", _ROUTINE_ACTIONS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "waiting"

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    routine_run = next(r for r in runs if r["trigger"] == "routine")
    final = _wait_for_run(client, routine_run["id"])
    assert final["status"] == "done", final
    assert final["ticket_id"] is None
    assert final["report"]["comments"] == [{"ticket": "MAP-002", "applied": True}]

    # Comment landed on the target ticket, authored by the PM agent.
    target_detail = client.get(f"/api/tickets/{target['key']}").json()
    agent_comments = [c for c in target_detail["comments"] if not c["is_system"]]
    assert len(agent_comments) == 1
    assert "tidak bergerak" in agent_comments[0]["body"]
    # Status NOT touched by the routine run.
    assert target_detail["status"] == "backlog"
    # updates: applied.
    assert target_detail["priority"] == "high"

    # Backlog ticket created (todo, not auto-scheduled).
    tickets = client.get(f"/api/workspaces/{ws_id}/tickets").json()
    backlog = next(t for t in tickets if t["title"] == "Backlog dari rutinitas")
    assert backlog["status"] == "todo"
    assert backlog["parent_id"] is None

    # Memory persisted.
    agents = client.get(f"/api/workspaces/{ws_id}/agents").json()
    pm = next(a for a in agents if a["id"] == pm_id)
    memories = client.get(f"/api/agents/{pm_id}/memory").json()
    assert any("cek tiket macet" in m["note"] for m in memories)

    # Routine back to idle.
    routine = client.get(f"/api/workspaces/{ws_id}/routines").json()[0]
    assert routine["status"] == "idle"
    assert routine["last_run_at"] is not None


def test_routine_comments_record_at_mentions_from_body(client, tmp_path, monkeypatch):
    """An `@name` written in a routine's `comments[]` body is recorded as a
    comment_mention on the target comment — the UI shows it as a mention badge/link.
    The mention would normally also schedule a run for lead-1 (comments[] has no
    `mention:` field to use instead), but MAP-002 here has no active sprint, so
    the `ticket_not_in_active_sprint` guardrail blocks it — see
    test_routine_comments_at_mention_schedules_run_when_actionable below for the
    case where it actually fires."""
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    _make_agent(client, ws_id, "lead", "lead-1")
    _make_ticket(client, ws_id)  # MAP-001
    target = _make_ticket(client, ws_id, "Target macet")  # MAP-002 (komen jatuh ke sini)

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "Cek tiket macet",
            "prompt": "Cek semua tiket yang tidak bergerak dan komen.",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    rid = resp.json()["id"]

    script = _write_python_binary(
        tmp_path / "opencode",
        '''
import json
text = """routine done

```map
summary: |
  Cek tiket macet selesai.
comments:
  - ticket: MAP-002
    body: |
      Tiket ini tidak bergerak. @lead-1 tolong cek, lead-1 juga koordinatornya.
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
''',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 200, resp.text
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    routine_run = next(r for r in runs if r["trigger"] == "routine")
    final = _wait_for_run(client, routine_run["id"])
    assert final["status"] == "done", final

    # @lead-1 in the comment body → mention recorded; bare "lead-1" ignored.
    target_detail = client.get(f"/api/tickets/{target['key']}").json()
    agent_comments = [c for c in target_detail["comments"] if not c["is_system"]]
    assert len(agent_comments) == 1
    assert agent_comments[0]["mentions"] == ["lead-1"]

    # Only the routine run itself exists — the mention was blocked by
    # ticket_not_in_active_sprint, not silently skipped for some other reason.
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert len(runs) == 1


def test_routine_comments_at_mention_schedules_run_when_actionable(client, tmp_path, monkeypatch):
    """Same as above, but the target ticket IS in an active sprint and status
    "todo" — an `@name` in a routine's `comments[]` body must actually schedule
    a run for the mentioned agent, since no-ticket-mode reports can't declare a
    `mention:` field at all (comments[] is their only handoff channel)."""
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    lead_id = _make_agent(client, ws_id, "lead", "lead-1")
    _make_ticket(client, ws_id)  # MAP-001

    sprint_id = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "S1"}).json()["id"]
    target = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={"title": "Target macet", "is_new_epic": True, "sprint_id": sprint_id},
    ).json()  # MAP-002
    client.patch(f"/api/tickets/{target['key']}", json={"status": "todo"})

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "Cek tiket macet",
            "prompt": "Cek semua tiket yang tidak bergerak dan komen.",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    rid = resp.json()["id"]

    script = _write_python_binary(
        tmp_path / "opencode",
        '''
import json
text = """routine done

```map
summary: |
  Cek tiket macet selesai.
comments:
  - ticket: MAP-002
    body: |
      Tiket ini tidak bergerak. @lead-1 tolong cek.
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
''',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 200, resp.text
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    routine_run = next(r for r in runs if r["trigger"] == "routine")
    _wait_for_run(client, routine_run["id"])

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    mention_run = next(r for r in runs if r["trigger"] == "mention")
    assert mention_run["ticket_id"] == target["id"]
    assert mention_run["agent_id"] == lead_id


def test_routine_run_rejects_status_declaration(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    rid = resp.json()["id"]

    script = _write_python_binary(tmp_path / "opencode", _ROUTINE_BAD_STATUS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/routines/{rid}/run")
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    routine_run = next(r for r in runs if r["trigger"] == "routine")
    final = _wait_for_run(client, routine_run["id"])
    assert final["status"] == "failed"
    assert "status" in final["error"]

    routine = client.get(f"/api/workspaces/{ws_id}/routines").json()[0]
    assert routine["status"] == "idle"


_ROUTINE_ACTIVATE_SPRINT_ONLY_SCRIPT = '''
import json
text = """routine done

```map
summary: |
  Sprint 2 dibuat dan diaktifkan.
sprints:
  - name: "Sprint 2"
    status: active
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def test_routine_sprints_only_report_still_creates_and_activates_sprint(
    client, tmp_path, monkeypatch
):
    """Regression: sprint creation/activation used to be nested inside
    `if parsed.tickets:`, so a routine report that ONLY declares `sprints:` (no
    new tickets) silently did nothing at all."""
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"})

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    rid = resp.json()["id"]

    script = _write_python_binary(tmp_path / "opencode", _ROUTINE_ACTIVATE_SPRINT_ONLY_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/routines/{rid}/run")
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    routine_run = next(r for r in runs if r["trigger"] == "routine")
    final = _wait_for_run(client, routine_run["id"])
    assert final["status"] == "done", final

    sprints = {s["name"]: s for s in client.get(f"/api/workspaces/{ws_id}/sprints").json()}
    assert sprints["Sprint 2"]["status"] == "active"
    assert sprints["Sprint 1"]["status"] == "planned"


_ROUTINE_MALFORMED_SPRINTS_SCRIPT = '''
import json
text = """routine done

```map
summary: |
  Sprint 6 sudah dibuat lewat rutinitas.
sprints: |
  - name: Sprint 6
    goal: Test
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def test_routine_malformed_sprints_yaml_reported_via_run_error(client, tmp_path, monkeypatch):
    """Same `field: |` mistake as the chat/ticket paths, but a routine run has no
    ticket/conversation to comment on — the drop reason must still land somewhere
    (run.error / Activity), not vanish while `summary` claims success."""
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    rid = resp.json()["id"]

    script = _write_python_binary(tmp_path / "opencode", _ROUTINE_MALFORMED_SPRINTS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/routines/{rid}/run")
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    routine_run = next(r for r in runs if r["trigger"] == "routine")
    final = _wait_for_run(client, routine_run["id"])
    assert final["status"] == "done", final  # run itself succeeded; only sprints[] was dropped
    assert "sprints" in final["error"]
    assert "sprints: |" in final["error"]

    assert client.get(f"/api/workspaces/{ws_id}/sprints").json() == []


# ---------------------------------------------------------------------------
# Scheduler semantics
# ---------------------------------------------------------------------------


def test_scheduler_fires_due_routine_and_skips_idle_only_busy(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 1,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    rid = resp.json()["id"]

    script = _write_python_binary(tmp_path / "opencode", _SIMPLE_ROUTINE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    # Agent busy -> idle_only skips and advances last_run_at (no run created).
    import asyncio
    from app.db.models import Agent as AgentModel

    async def _set_busy():
        async with db_session.async_session() as session:
            agent = await session.get(AgentModel, pm_id)
            agent.status = "working"
            await session.commit()

    async def _run_tick():
        async with db_session.async_session() as session:
            await routine_scheduler._tick(session, db_session.async_session)

    asyncio.run(_set_busy())
    asyncio.run(_run_tick())
    routine = client.get(f"/api/workspaces/{ws_id}/routines").json()[0]
    assert routine["status"] == "idle"
    assert routine["last_run_at"] is not None
    assert client.get(f"/api/workspaces/{ws_id}/runs").json() == []


def test_scheduler_skips_disabled_and_paused(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 1,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    rid = resp.json()["id"]
    client.patch(f"/api/routines/{rid}", json={"status": "disabled"})

    import asyncio

    async def _run_tick():
        async with db_session.async_session() as session:
            await routine_scheduler._tick(session, db_session.async_session)

    asyncio.run(_run_tick())
    assert client.get(f"/api/workspaces/{ws_id}/runs").json() == []

    # Re-enable, pause workspace -> still skipped.
    client.patch(f"/api/routines/{rid}", json={"status": "idle"})
    assert client.post(f"/api/workspaces/{ws_id}/pause").status_code == 200
    asyncio.run(_run_tick())
    assert client.get(f"/api/workspaces/{ws_id}/runs").json() == []


def test_scheduler_skips_waiting_running_and_not_due(client, tmp_path, monkeypatch):
    import asyncio

    from app.db.models import Routine as RoutineModel

    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 1,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    ).json()["id"]

    async def _set_status(status):
        async with db_session.async_session() as session:
            r = await session.get(RoutineModel, rid)
            r.status = status
            await session.commit()

    async def _run_tick():
        async with db_session.async_session() as session:
            await routine_scheduler._tick(session, db_session.async_session)

    # waiting -> skipped (a run is already scheduled).
    asyncio.run(_set_status("waiting"))
    asyncio.run(_run_tick())
    assert client.get(f"/api/workspaces/{ws_id}/runs").json() == []

    # running -> skipped.
    asyncio.run(_set_status("running"))
    asyncio.run(_run_tick())
    assert client.get(f"/api/workspaces/{ws_id}/runs").json() == []

    # idle with a recent last_run_at -> not due yet.
    asyncio.run(_set_status("idle"))
    async def _set_recent_last_run():
        async with db_session.async_session() as session:
            r = await session.get(RoutineModel, rid)
            r.last_run_at = datetime.now(timezone.utc)
            await session.commit()
    asyncio.run(_set_recent_last_run())
    asyncio.run(_run_tick())
    assert client.get(f"/api/workspaces/{ws_id}/runs").json() == []


def test_scheduler_advances_last_run_at_for_missing_agent(client, tmp_path, monkeypatch):
    import asyncio

    ws_id = _make_workspace(client, tmp_path)
    # Routine with no agent assigned -> no valid agent -> advance last_run_at.
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={"name": "R", "prompt": "p", "interval_minutes": 1, "mode": "idle_only"},
    ).json()["id"]

    async def _run_tick():
        async with db_session.async_session() as session:
            await routine_scheduler._tick(session, db_session.async_session)

    asyncio.run(_run_tick())
    routine = client.get(f"/api/workspaces/{ws_id}/routines").json()[0]
    assert routine["last_run_at"] is not None
    assert client.get(f"/api/workspaces/{ws_id}/runs").json() == []


def test_scheduler_loop_stops_on_event(client, tmp_path, monkeypatch):
    import asyncio

    stop_event = asyncio.Event()
    stop_event.set()
    asyncio.run(routine_scheduler.run_scheduler(db_session.async_session, stop_event))


def test_routine_run_guardrail_blocked_409(client, tmp_path, monkeypatch):
    from app.core import orchestrator
    from app.core.guardrails import GuardrailBlocked

    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "A",
            "prompt": "p",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    ).json()["id"]

    async def _blocked(*args, **kwargs):
        raise GuardrailBlocked("max_concurrent_runs", "too many runs")

    monkeypatch.setattr(orchestrator, "schedule_routine_run", _blocked)

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "guardrail_blocked"


def test_routine_run_nonzero_exit_marks_failed_idle(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    ).json()["id"]

    script = _write_python_binary(
        tmp_path / "opencode", "import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n"
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 200, resp.text
    run = next(r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "routine")
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "failed", final
    assert "boom" in final["error"]

    routine = client.get(f"/api/workspaces/{ws_id}/routines").json()[0]
    assert routine["status"] == "idle"
    agent = client.get(f"/api/workspaces/{ws_id}/agents").json()[0]
    assert agent["status"] == "idle"


def test_routine_run_malformed_map_marks_failed(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 5,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    ).json()["id"]

    script = _write_python_binary(
        tmp_path / "opencode",
        '''
import json
print(json.dumps({"type": "assistant_text", "text": "no map block here at all"}))
''',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 200, resp.text
    run = next(r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "routine")
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "failed", final

    routine = client.get(f"/api/workspaces/{ws_id}/routines").json()[0]
    assert routine["status"] == "idle"


def test_scheduler_tick_fires_due_routine_for_idle_agent(client, tmp_path, monkeypatch):
    """The `_tick` try body (schedule_routine_run success) is otherwise only
    exercised indirectly through the HTTP `/routines/{id}/run` endpoint, never
    through the scheduler's own due-routine loop."""
    import asyncio

    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 1,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )

    script = _write_python_binary(tmp_path / "opencode", _SIMPLE_ROUTINE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    async def _run_tick():
        async with db_session.async_session() as session:
            await routine_scheduler._tick(session, db_session.async_session)

    asyncio.run(_run_tick())
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert any(r["trigger"] == "routine" for r in runs)


def test_scheduler_tick_swallows_guardrail_blocked(client, tmp_path, monkeypatch):
    import asyncio

    from app.core import orchestrator
    from app.core.guardrails import GuardrailBlocked

    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 1,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )

    async def _blocked(*args, **kwargs):
        raise GuardrailBlocked("max_concurrent_runs", "too many runs")

    monkeypatch.setattr(orchestrator, "schedule_routine_run", _blocked)

    async def _run_tick():
        async with db_session.async_session() as session:
            await routine_scheduler._tick(session, db_session.async_session)

    asyncio.run(_run_tick())  # must not raise
    assert client.get(f"/api/workspaces/{ws_id}/runs").json() == []


def test_scheduler_tick_swallows_runtime_error_paused(client, tmp_path, monkeypatch):
    import asyncio

    from app.core import orchestrator

    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 1,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )

    async def _paused(*args, **kwargs):
        raise RuntimeError("workspace paused")

    monkeypatch.setattr(orchestrator, "schedule_routine_run", _paused)

    async def _run_tick():
        async with db_session.async_session() as session:
            await routine_scheduler._tick(session, db_session.async_session)

    asyncio.run(_run_tick())  # must not raise
    assert client.get(f"/api/workspaces/{ws_id}/runs").json() == []


def test_scheduler_normalizes_naive_last_run_at(client, tmp_path, monkeypatch):
    """SQLite/expire_on_commit=False can leave a naive last_run_at on an
    already-loaded ORM object; `_tick` must normalize it to UTC-aware before
    comparing, not raise a naive/aware TypeError."""
    import asyncio

    from app.db.models import Routine as RoutineModel

    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    rid = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 1,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    ).json()["id"]

    async def _run_tick_with_naive_last_run_at():
        async with db_session.async_session() as session:
            routine = await session.get(RoutineModel, rid)
            routine.last_run_at = datetime.now()  # naive, no tzinfo
            # Same session/identity map: _tick's select() will reuse this
            # in-memory object rather than re-fetching (which would re-apply
            # the UTCDateTime type decorator and mask the naive value).
            await routine_scheduler._tick(session, db_session.async_session)

    asyncio.run(_run_tick_with_naive_last_run_at())  # must not raise TypeError


def test_scheduler_loop_runs_one_tick_then_stops(client, tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(routine_scheduler, "_TICK_SECONDS", 0.01)

    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={
            "name": "R",
            "prompt": "p",
            "interval_minutes": 1,
            "mode": "idle_only",
            "agent_id": pm_id,
        },
    )
    script = _write_python_binary(tmp_path / "opencode", _SIMPLE_ROUTINE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    async def _run_loop():
        stop_event = asyncio.Event()
        task = asyncio.create_task(_REAL_RUN_SCHEDULER(db_session.async_session, stop_event))
        await asyncio.sleep(0.3)  # let at least one real tick fire
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(_run_loop())
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert any(r["trigger"] == "routine" for r in runs)
