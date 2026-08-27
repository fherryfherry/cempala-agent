"""Tests for POST /runs/{id}/retry: re-trigger a failed/interrupted run's agent on the

same ticket. Same fake-opencode-binary technique as test_orchestrator.py.
"""

import stat
import time
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import session as db_session
from app.db.models import Base, Run, Ticket
from app.db.session import get_session
from app.main import app
from app.schemas.workspace import DEFAULT_GUARDRAILS


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


def _write_python_binary(path, code):
    path.write_text(f"#!/usr/bin/env python3\n{code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _make_workspace(client, tmp_path, key="MAP", **overrides):
    guardrails = overrides.pop("guardrails", None)
    payload = {"name": "Map", "key": key, "repo_path": str(tmp_path)}
    payload.update(overrides)
    resp = client.post("/api/workspaces", json=payload)
    assert resp.status_code == 201, resp.text
    ws_id = resp.json()["id"]
    if guardrails is not None:
        patch = client.patch(f"/api/workspaces/{ws_id}", json={"guardrails": guardrails})
        assert patch.status_code == 200, patch.text
    return ws_id


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


def _make_ticket(client, ws_id, title="Do the thing"):
    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={
            "title": title,
            "is_new_epic": True,
            "sprint_id": _active_sprint_id(client, ws_id),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_status(client, key, status):
    resp = client.patch(f"/api/tickets/{key}", json={"status": status})
    assert resp.status_code == 200, resp.text


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


_MISSING_MAP_SCRIPT = '''
import json
print(json.dumps({
    "type": "assistant_text",
    "text": "I forgot to close with a map block.",
    "session_id": "sess-orig",
}))
'''


def _valid_map_script(session_id="sess-new", echo_argv=False):
    argv_line = (
        'print(json.dumps({"type": "assistant_text", '
        '"text": "ARGV:" + json.dumps(sys.argv[1:])}))\n'
        if echo_argv
        else ""
    )
    return f'''
import json
import sys
{argv_line}text = """Done.

```map
status: done
mention: []
summary: |
  resumed and finished
```
"""
print(json.dumps({{"type": "assistant_text", "text": text, "session_id": "{session_id}"}}))
'''


async def _insert_run(maker, *, ticket_id, agent_id, status, trigger="manual", session_id=None):
    async with maker() as session:
        run = Run(
            ticket_id=ticket_id,
            agent_id=agent_id,
            status=status,
            trigger=trigger,
            tool_kind="opencode",
            model="opencode/big-pickle",
            session_id=session_id,
        )
        session.add(run)
        await session.commit()
        return run.id


def test_retry_nonexistent_run_404(client):
    resp = client.post("/api/runs/does-not-exist/retry")
    assert resp.status_code == 404


def test_retry_routine_run_not_retryable_409(client, tmp_path):
    """A routine/chat run (ticket_id is None) can never be retried."""
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")

    run_id = _run_sync(
        _insert_run(
            db_session.async_session,
            ticket_id=None,
            agent_id=eng_id,
            status="failed",
        )
    )

    resp = client.post(f"/api/runs/{run_id}/retry")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_retryable"


def test_retry_paused_workspace_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)

    run_id = _run_sync(
        _insert_run(
            db_session.async_session,
            ticket_id=ticket["id"],
            agent_id=eng_id,
            status="failed",
        )
    )
    client.post(f"/api/workspaces/{ws_id}/pause")

    resp = client.post(f"/api/runs/{run_id}/retry")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "workspace_paused"


def test_stop_queued_run_cancels(client, tmp_path, monkeypatch):
    from app.core import orchestrator

    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)

    run_id = _run_sync(
        _insert_run(
            db_session.async_session,
            ticket_id=ticket["id"],
            agent_id=eng_id,
            status="queued",
        )
    )
    cancelled = []
    async def _fake_cancel(agent_id, run_id):
        cancelled.append(run_id)
        return True
    monkeypatch.setattr(orchestrator, "cancel_queued", _fake_cancel)

    resp = client.post(f"/api/runs/{run_id}/stop")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    assert cancelled == [run_id]


def test_stop_nonexistent_run_404(client):
    resp = client.post("/api/runs/does-not-exist/stop")
    assert resp.status_code == 404


def test_start_run_no_agent_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)
    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_agent"


def test_start_run_paused_workspace_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    client.post(f"/api/workspaces/{ws_id}/pause")

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "workspace_paused"


def test_list_runs_status_filter(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    run_id = _run_sync(
        _insert_run(
            db_session.async_session,
            ticket_id=ticket["id"],
            agent_id=eng_id,
            status="failed",
        )
    )

    resp = client.get(f"/api/workspaces/{ws_id}/runs", params={"status": "failed"})
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [run_id]

    resp = client.get(f"/api/workspaces/{ws_id}/runs", params={"status": "done"})
    assert resp.json() == []


def test_retry_running_run_not_retryable_409(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)

    run_id = _run_sync(_insert_run(db_session.async_session, ticket_id=ticket["id"], agent_id=eng_id, status="running"))

    resp = client.post(f"/api/runs/{run_id}/retry")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_retryable"


def test_retry_done_run_not_retryable_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)

    run_id = _run_sync(_insert_run(db_session.async_session, ticket_id=ticket["id"], agent_id=eng_id, status="done"))

    resp = client.post(f"/api/runs/{run_id}/retry")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_retryable"


def test_retry_cancelled_run_not_retryable_409(client, tmp_path):
    """A `cancelled` run killed by a runtime guardrail (error set) is a deliberate
    brake — never resumable."""
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)

    run_id = _run_sync(
        _insert_run(
            db_session.async_session,
            ticket_id=ticket["id"],
            agent_id=eng_id,
            status="cancelled",
            trigger="auto",
        )
    )
    _run_sync(_set_run_error(db_session.async_session, run_id, "max_cost_per_run exceeded"))

    resp = client.post(f"/api/runs/{run_id}/retry")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_retryable"


def test_resume_cancelled_run_completes(client, tmp_path, monkeypatch):
    """A `cancelled` run stopped by the owner (error None) is resumable — the UI
    shows this as "Resume". Same mechanics as retry: new run, session continued."""
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "in_progress")

    run_id = _run_sync(
        _insert_run(
            db_session.async_session,
            ticket_id=ticket["id"],
            agent_id=eng_id,
            status="cancelled",
            session_id="sess-stop",
        )
    )

    script = _write_python_binary(tmp_path / "opencode", _valid_map_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/runs/{run_id}/retry")
    assert resp.status_code == 201, resp.text
    new_run = resp.json()
    assert new_run["id"] != run_id
    assert new_run["ticket_id"] == ticket["id"]

    final = _wait_for_run(client, new_run["id"])
    assert final["status"] == "done", final


async def _set_run_error(maker, run_id, error):
    async with maker() as session:
        run = await session.get(Run, run_id)
        run.error = error
        await session.commit()


def test_retry_failed_run_unblocks_ticket_and_completes(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 0}
    )
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script1 = _write_python_binary(tmp_path / "opencode1", _MISSING_MAP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script1)

    run1 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    final1 = _wait_for_run(client, run1["id"])
    assert final1["status"] == "failed", final1

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"
    assert detail["blocked_reason"] is not None

    script2 = _write_python_binary(tmp_path / "opencode2", _valid_map_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script2)

    resp = client.post(f"/api/runs/{run1['id']}/retry")
    assert resp.status_code == 201, resp.text
    run2 = resp.json()
    assert run2["id"] != run1["id"]
    assert run2["ticket_id"] == run1["ticket_id"]
    assert run2["agent_id"] == eng_id

    final2 = _wait_for_run(client, run2["id"])
    assert final2["status"] == "done", final2

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "done"
    assert detail["blocked_reason"] is None


def test_retry_interrupted_run_completes(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "in_progress")

    run_id = _run_sync(
        _insert_run(db_session.async_session, ticket_id=ticket["id"], agent_id=eng_id, status="interrupted")
    )

    script = _write_python_binary(tmp_path / "opencode", _valid_map_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/runs/{run_id}/retry")
    assert resp.status_code == 201, resp.text
    new_run = resp.json()

    final = _wait_for_run(client, new_run["id"])
    assert final["status"] == "done", final


def test_retry_resumes_prior_session(client, tmp_path, monkeypatch):
    """A failed run that had already gotten a session_id from opencode (e.g. it did

    respond, just without a valid ```map block) should have that session_id passed
    to opencode again via `-s` on retry.
    """
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 0}
    )
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script1 = _write_python_binary(tmp_path / "opencode1", _MISSING_MAP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script1)

    run1 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    final1 = _wait_for_run(client, run1["id"])
    assert final1["status"] == "failed", final1
    assert final1["session_id"] == "sess-orig"

    script2 = _write_python_binary(tmp_path / "opencode2", _valid_map_script(echo_argv=True))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script2)

    run2 = client.post(f"/api/runs/{run1['id']}/retry").json()
    _wait_for_run(client, run2["id"])

    events = client.get(f"/api/runs/{run2['id']}").json()["events"]
    argv_event = next(
        e for e in events if e["type"] == "assistant_text" and e["payload"]["text"].startswith("ARGV:")
    )
    argv = argv_event["payload"]["text"][len("ARGV:"):]
    assert "-s" in argv and "sess-orig" in argv


def test_retry_resets_stale_handoff_depth_guardrail(client, tmp_path, monkeypatch):
    """A ticket blocked (by an unrelated failure) while already at max_handoff_depth

    would immediately re-trip that guardrail on a naive `schedule()` retry. Retry
    resets it first (same as PATCH-unblock), so the retry itself succeeds.
    """
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 0}
    )
    patch_resp = client.patch(
        f"/api/workspaces/{ws_id}", json={"guardrails": {"max_handoff_depth": 1}}
    )
    assert patch_resp.status_code == 200, patch_resp.text
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)

    # Seed: ticket already blocked with stale handoff_depth at/over the limit, plus a
    # failed run to retry.
    seeded = _run_sync(_patch_ticket_direct(ticket["key"], status="blocked", handoff_depth=5))
    assert seeded
    run_id = _run_sync(
        _insert_run(db_session.async_session, ticket_id=ticket["id"], agent_id=eng_id, status="failed")
    )

    script = _write_python_binary(tmp_path / "opencode", _valid_map_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/runs/{run_id}/retry")
    assert resp.status_code == 201, resp.text
    new_run = resp.json()
    final = _wait_for_run(client, new_run["id"])
    assert final["status"] == "done", final

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["handoff_depth"] == 0
    assert detail["blocked_reason"] is None


async def _patch_ticket_direct(key, *, status, handoff_depth):
    async with db_session.async_session() as session:
        from sqlalchemy import select as _select

        ticket = (await session.scalars(_select(Ticket).where(Ticket.key == key))).one()
        ticket.status = status
        ticket.handoff_depth = handoff_depth
        ticket.blocked_reason = "stale reason from a previous failure"
        await session.commit()
        return True


def _run_sync(coro):
    import asyncio

    return asyncio.run(coro)


def test_retry_guardrail_blocked_409(client, tmp_path, monkeypatch):
    """A retry that trips a schedule-time guardrail (e.g. max_cost_per_ticket)
    returns 409 guardrail_blocked."""
    from app.core import orchestrator
    from app.core.guardrails import GuardrailBlocked

    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)

    run_id = _run_sync(
        _insert_run(
            db_session.async_session,
            ticket_id=ticket["id"],
            agent_id=eng_id,
            status="failed",
        )
    )

    async def _blocked(*args, **kwargs):
        raise GuardrailBlocked("max_cost_per_ticket", "cost exceeded")

    monkeypatch.setattr(orchestrator, "schedule", _blocked)

    resp = client.post(f"/api/runs/{run_id}/retry")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "guardrail_blocked"
