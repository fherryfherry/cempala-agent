"""Tests for MAP-027: schedule-time + runtime guardrails.

Reuses the same client fixture / fake-binary technique as test_orchestrator.py — real HTTP
API, real (file-backed) DB, no mocking of the orchestrator's DB layer.
"""

import stat
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


def _make_workspace(client, tmp_path, key="MAP", guardrails=None):
    resp = client.post(
        "/api/workspaces", json={"name": "Map", "key": key, "repo_path": str(tmp_path)}
    )
    assert resp.status_code == 201, resp.text
    ws = resp.json()
    if guardrails:
        merged = dict(ws["guardrails"])
        merged.update(guardrails)
        resp = client.patch(f"/api/workspaces/{ws['id']}", json={"guardrails": merged})
        assert resp.status_code == 200, resp.text
        ws = resp.json()
    return ws["id"]


def _make_agent(client, ws_id, role, name):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": "opencode/big-pickle", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _active_sprint_id(client, ws_id):
    """Idempotent: reuse the workspace's active sprint if one exists, else create
    one (bootstraps active as the first sprint)."""
    sprints = client.get(f"/api/workspaces/{ws_id}/sprints").json()
    active = next((s for s in sprints if s["status"] == "active"), None)
    if active:
        return active["id"]
    resp = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 0"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_ticket(client, ws_id, title="Do the thing", **overrides):
    payload = {"title": title, "description": "desc", "is_new_epic": True}
    if "sprint_id" not in overrides:
        payload["sprint_id"] = _active_sprint_id(client, ws_id)
    payload.update(overrides)
    if "parent_id" in overrides:
        payload.pop("is_new_epic", None)
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_status(client, key, status):
    resp = client.patch(f"/api/tickets/{key}", json={"status": status})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _wait_for_run(client, run_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.03)
    raise TimeoutError(f"run {run_id} did not reach a terminal state within {timeout}s")


def _system_comment_bodies(client, key):
    detail = client.get(f"/api/tickets/{key}").json()
    return [c["body"] for c in detail["comments"] if c["is_system"]]


# ---------------------------------------------------------------------------
# max_concurrent_runs
# ---------------------------------------------------------------------------

_SLOW_DONE_SCRIPT = '''
import json, time
time.sleep(2)
text = """ok

```map
status: review
mention: []
summary: |
  done
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def test_max_concurrent_runs_blocks_scheduling_at_limit(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path, guardrails={"max_concurrent_runs": 1})
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    lead_id = _make_agent(client, ws_id, "lead", "lead-1")
    t1 = _make_ticket(client, ws_id, "first")
    t2 = _make_ticket(client, ws_id, "second")
    for t in (t1, t2):
        _set_status(client, t["key"], "todo")
        _set_status(client, t["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _SLOW_DONE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    r1 = client.post(f"/api/tickets/{t1['key']}/run", json={"agent_id": eng_id})
    assert r1.status_code == 201, r1.text

    # Wait for r1 to actually be "running" (a different agent, so it doesn't queue).
    deadline = time.time() + 5
    while time.time() < deadline:
        if client.get(f"/api/runs/{r1.json()['id']}").json()["status"] == "running":
            break
        time.sleep(0.02)
    else:
        raise TimeoutError("run 1 never reached running")

    # Second run, different agent (lead) -> would normally start immediately, but the
    # workspace's max_concurrent_runs=1 is already saturated by r1.
    r2 = client.post(f"/api/tickets/{t2['key']}/run", json={"agent_id": lead_id})
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "guardrail_blocked"

    bodies = _system_comment_bodies(client, t2["key"])
    assert any("max_concurrent_runs" in b for b in bodies)

    detail = client.get(f"/api/tickets/{t2['key']}").json()
    assert detail["status"] == "blocked"

    _wait_for_run(client, r1.json()["id"])  # drain, don't leak a background task


def test_max_concurrent_runs_does_not_block_under_limit(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path, guardrails={"max_concurrent_runs": 3})
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _SLOW_DONE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 201, resp.text
    final = _wait_for_run(client, resp.json()["id"])
    assert final["status"] == "done"


# ---------------------------------------------------------------------------
# max_cost_per_ticket
# ---------------------------------------------------------------------------


def test_max_cost_per_ticket_blocks_scheduling(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path, guardrails={"max_cost_per_ticket": 5.0})
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    # Simulate a ticket that already burned through its budget on prior runs.
    from app.db.models import Ticket

    async def _bump_cost():
        async with db_session.async_session() as session:
            t = await session.get(Ticket, ticket["id"])
            t.cost_used = 21.0
            await session.commit()

    import asyncio

    asyncio.run(_bump_cost())

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "guardrail_blocked"

    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("max_cost_per_ticket" in b and "21.00" in b and "5.00" in b for b in bodies)

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"


# ---------------------------------------------------------------------------
# max_handoff_depth
# ---------------------------------------------------------------------------


def test_max_handoff_depth_blocks_scheduling(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path, guardrails={"max_handoff_depth": 2})
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    from app.db.models import Ticket

    async def _bump_depth():
        async with db_session.async_session() as session:
            t = await session.get(Ticket, ticket["id"])
            t.handoff_depth = 2
            await session.commit()

    import asyncio

    asyncio.run(_bump_depth())

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "guardrail_blocked"

    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("max_handoff_depth" in b for b in bodies)

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"


_REPLY_SCRIPT = '''
import json
text = """reply

```map
status: in_progress
mention: []
summary: |
  chatting along
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def test_max_handoff_depth_does_not_block_owner_chat_mention(client, tmp_path, monkeypatch):
    # A ticket whose handoff_depth is already at/over the limit (e.g. a finished epic
    # that racked up a long real handoff chain) must still let the owner keep chatting
    # with the mentioned agent — max_handoff_depth only bounds agent-to-agent chains.
    ws_id = _make_workspace(client, tmp_path, guardrails={"max_handoff_depth": 2})
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    from app.db.models import Ticket

    async def _bump_depth():
        async with db_session.async_session() as session:
            t = await session.get(Ticket, ticket["id"])
            t.handoff_depth = 5
            await session.commit()

    import asyncio

    asyncio.run(_bump_depth())

    script = _write_python_binary(tmp_path / "opencode", _REPLY_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "@pm-1 hai lagi"})
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] != "blocked"
    assert not any("max_handoff_depth" in b for b in _system_comment_bodies(client, ticket["key"]))

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    mention_runs = [r for r in runs if r["trigger"] == "mention"]
    assert len(mention_runs) == 1
    final = _wait_for_run(client, mention_runs[0]["id"])
    assert final["status"] == "done", final


# ---------------------------------------------------------------------------
# run_timeout_sec — exact AC scenario: lower to 5s, fake binary sleeps 20s.
# ---------------------------------------------------------------------------

_HANGING_THEN_DONE_SCRIPT = '''
import json, time
time.sleep(20)
text = """ok

```map
status: review
mention: []
summary: |
  done
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def _run_timeout_once(client, tmp_path, monkeypatch, suffix):
    key = "TMO" + chr(ord("A") + int(suffix))
    ws_id = _make_workspace(client, tmp_path, key=key, guardrails={"run_timeout_sec": 5})
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / f"opencode{suffix}", _HANGING_THEN_DONE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    start = time.time()
    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    final = _wait_for_run(client, run_id, timeout=15.0)
    elapsed = time.time() - start

    assert final["status"] == "cancelled", final
    # Cancelled around the 5s guardrail mark, not the full 20s sleep.
    assert elapsed < 15.0, f"run took {elapsed:.1f}s, expected cancellation near 5s"

    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("run_timeout_sec" in b for b in bodies), bodies


@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_run_timeout_sec_cancels_run_with_named_comment(client, tmp_path, monkeypatch, attempt):
    _run_timeout_once(client, tmp_path, monkeypatch, suffix=str(attempt))


# ---------------------------------------------------------------------------
# max_cost_per_run — accumulated running cost from streamed JSON lines.
# ---------------------------------------------------------------------------

_COSTLY_STREAM_SCRIPT = '''
import json, sys, time

for i in range(20):
    print(json.dumps({"type": "assistant_text", "text": f"chunk {i}", "cost": 0.5}))
    sys.stdout.flush()
    time.sleep(0.2)

text = """ok

```map
status: review
mention: []
summary: |
  done
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def test_max_cost_per_run_cancels_run_with_named_comment(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path, guardrails={"max_cost_per_run": 1.0})
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _COSTLY_STREAM_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    final = _wait_for_run(client, run_id, timeout=15.0)
    assert final["status"] == "cancelled", final

    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("max_cost_per_run" in b for b in bodies), bodies


# ---------------------------------------------------------------------------
# ticket_not_in_active_sprint
# ---------------------------------------------------------------------------

_DONE_SCRIPT = '''
import json
text = """ok

```map
status: done
mention: []
summary: |
  done
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def test_backlog_ticket_blocks_scheduling_for_non_exempt_role(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id, sprint_id=None)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "guardrail_blocked"

    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("backlog" in b and "active sprint" in b for b in bodies)

    # The sprint gate refuses the run WITHOUT touching the ticket's status — a
    # ticket outside the active sprint is not a failure, it's just not due yet.
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "backlog"


def test_ticket_in_planned_sprint_blocks_scheduling_for_non_exempt_role(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    # First sprint bootstraps active; create a second one, which stays "planned".
    client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"})
    planned = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 2"}).json()
    assert planned["status"] == "planned"
    ticket = _make_ticket(client, ws_id, sprint_id=planned["id"])

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 409, resp.text

    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("Sprint 2" in b and "planned" in b and "active sprint" in b for b in bodies)


def test_ticket_in_active_sprint_allows_scheduling_for_non_exempt_role(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    sprint = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"}).json()
    assert sprint["status"] == "active"
    ticket = _make_ticket(client, ws_id, sprint_id=sprint["id"])

    script = _write_python_binary(tmp_path / "opencode", _DONE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 201, resp.text


def test_pm_role_exempt_from_active_sprint_gate(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    # Backlog ticket (no sprint at all) -- PM must still be reachable to triage it.
    ticket = _make_ticket(client, ws_id, sprint_id=None)

    script = _write_python_binary(tmp_path / "opencode", _DONE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id})
    assert resp.status_code == 201, resp.text


def test_over_run_timeout_and_cost_per_run():
    from app.core.guardrails import over_cost_per_run, over_run_timeout

    assert over_run_timeout({"run_timeout_sec": 10}, 5) is None
    assert "run_timeout_sec" in over_run_timeout({"run_timeout_sec": 10}, 10)

    assert over_cost_per_run({"max_cost_per_run": 1.0}, 0.5) is None
    assert "max_cost_per_run" in over_cost_per_run({"max_cost_per_run": 1.0}, 1.0)
