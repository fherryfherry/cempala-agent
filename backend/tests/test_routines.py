"""Tests for the Routines menu: CRUD API, scheduler semantics, and routine runs
(no-ticket runs whose ```map block carries side-effect actions only).
"""

import stat
import time
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core import routine_scheduler
from app.db import session as db_session
from app.db.models import Base
from app.db.session import get_session
from app.main import app


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
    No run is scheduled (routine reports have no handoff anyway)."""
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
