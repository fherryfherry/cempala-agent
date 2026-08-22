"""Integration tests for MAP-031: kill switch pause/resume.

Same fake-binary + `ps`/`pgrep` verification technique as test_orchestrator.py's
stop-run test, but at workspace scope: pause must kill every live subprocess across
multiple concurrently-running agents within the AC's 5s window, cancel queued runs
without ever spawning them, and reject new schedules while paused with a clear error.
"""

import stat
import subprocess
import time
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import session as db_session
from app.db.models import Base
from app.db.session import get_session
from app.main import app


@pytest.fixture
async def client(tmp_path, monkeypatch):
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


def _write_script(path, body):
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


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


def _make_ticket(client, ws_id, title="Do the thing", **overrides):
    payload = {"title": title, "description": "desc"}
    payload.update(overrides)
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_status(client, key, status):
    resp = client.patch(f"/api/tickets/{key}", json={"status": status})
    assert resp.status_code == 200, resp.text
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


def _run_hanging_ticket(client, ws_id, agent_id, sleep_marker):
    ticket = _make_ticket(client, ws_id, title=f"ticket-{sleep_marker}")
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")
    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": agent_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _find_pids(marker):
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
    return [int(p) for p in found]


# ---------------------------------------------------------------------------
# (1) 3 concurrently-running processes across 3 agents -> pause kills all within 5s,
#     runs cancelled, agents idle.
# ---------------------------------------------------------------------------


def _do_pause_kills_three(client, tmp_path, monkeypatch, run_index):
    ws_id = _make_workspace(client, tmp_path, key=f"WK{chr(65 + run_index)}")
    # Unique sleep duration (not a shell comment, which `exec` strips before it ever
    # reaches the process's argv) so `pgrep -f` can only ever match THIS run's own
    # processes, never a leaked process from an unrelated prior test run.
    marker = f"sleep {uuid.uuid4().int % 900000 + 100000}"
    script = _write_script(tmp_path / f"opencode_{run_index}", f"exec {marker}\n")
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    agent_ids = [_make_agent(client, ws_id, "engineer", f"eng-{run_index}-{i}") for i in range(3)]
    runs = [_run_hanging_ticket(client, ws_id, aid, marker) for aid in agent_ids]

    deadline = time.time() + 5
    pids = []
    while time.time() < deadline and len(pids) < 3:
        pids = _find_pids(marker)
        if len(pids) < 3:
            time.sleep(0.05)
    assert len(pids) == 3, f"expected 3 live processes, found {pids}"

    resp = client.post(f"/api/workspaces/{ws_id}/pause")
    assert resp.status_code == 200, resp.text
    assert resp.json()["paused"] is True

    deadline = time.time() + 5
    alive = pids
    while time.time() < deadline and alive:
        alive = [p for p in pids if subprocess.run(["ps", "-p", str(p)], capture_output=True).returncode == 0]
        if alive:
            time.sleep(0.05)
    assert not alive, f"pids still alive after pause within 5s: {alive}"

    for run in runs:
        final = _wait_for_run(client, run["id"], timeout=5)
        assert final["status"] == "cancelled", final

    agents = client.get(f"/api/workspaces/{ws_id}/agents").json()
    for aid in agent_ids:
        agent = next(a for a in agents if a["id"] == aid)
        assert agent["status"] == "idle", agent


@pytest.mark.parametrize("run_index", [0, 1, 2])
def test_pause_kills_three_concurrent_processes_within_5s(client, tmp_path, monkeypatch, run_index):
    # Parametrized (not a plain loop) to rule out timing flakiness across repeated,
    # fully-isolated runs (fresh client/tmp_path/DB each time).
    _do_pause_kills_three(client, tmp_path, monkeypatch, run_index)


# ---------------------------------------------------------------------------
# (2) queued run cancelled on pause without ever spawning a process.
# ---------------------------------------------------------------------------

def test_pause_cancels_queued_run_without_spawning(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    marker = f"sleep {uuid.uuid4().int % 900000 + 100000}"
    script = _write_script(tmp_path / "opencode", f"exec {marker}\n")
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    r1 = _run_hanging_ticket(client, ws_id, eng_id, marker)

    # wait for the first run's process to actually be executing
    deadline = time.time() + 5
    pids = []
    while time.time() < deadline and not pids:
        pids = _find_pids(marker)
        if not pids:
            time.sleep(0.05)
    assert pids, "expected first run's process to have spawned"

    # second run for the same busy agent -> queued, not started
    r2 = _run_hanging_ticket(client, ws_id, eng_id, marker)
    assert client.get(f"/api/runs/{r2['id']}").json()["status"] == "queued"

    resp = client.post(f"/api/workspaces/{ws_id}/pause")
    assert resp.status_code == 200, resp.text

    final2 = client.get(f"/api/runs/{r2['id']}").json()
    assert final2["status"] == "cancelled"

    # cleanup: only one process should ever have existed for this marker
    assert len(_find_pids(marker)) <= 1


# ---------------------------------------------------------------------------
# (3) run request while paused -> 409, clear message, run never created.
# ---------------------------------------------------------------------------


def test_schedule_while_paused_rejected_with_clear_message(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    resp = client.post(f"/api/workspaces/{ws_id}/pause")
    assert resp.status_code == 200, resp.text

    before = client.get(f"/api/workspaces/{ws_id}/runs").json()

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"]["code"] == "workspace_paused"
    assert "paused" in body["error"]["message"].lower()

    after = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert after == before, "no run should have been created while paused"


# ---------------------------------------------------------------------------
# (4) resume flips paused back off, subsequent run works normally.
# ---------------------------------------------------------------------------

_VALID_MAP_SCRIPT = '''
import json
text = """Done.

```map
status: review
mention: []
summary: |
  All good.
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def test_resume_allows_scheduling_again(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    resp = client.post(f"/api/workspaces/{ws_id}/pause")
    assert resp.status_code == 200

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 409

    resp = client.post(f"/api/workspaces/{ws_id}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["paused"] is False

    script = _write_python_binary(tmp_path / "opencode", _VALID_MAP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 201, resp.text
    final = _wait_for_run(client, resp.json()["id"])
    assert final["status"] == "done", final


# ---------------------------------------------------------------------------
# (5) pausing a workspace with no active runs is a no-op.
# ---------------------------------------------------------------------------


def test_pause_with_no_active_runs_is_noop(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)

    resp = client.post(f"/api/workspaces/{ws_id}/pause")
    assert resp.status_code == 200, resp.text
    assert resp.json()["paused"] is True

    resp = client.get(f"/api/workspaces/{ws_id}")
    assert resp.status_code == 200
    assert resp.json()["paused"] is True
