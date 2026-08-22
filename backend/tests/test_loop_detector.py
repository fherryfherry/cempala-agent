"""Tests for MAP-028: ping-pong loop detector.

Unit tests build run history directly via the DB session (no subprocess runs needed).
The integration test drives the real HTTP API + orchestrator, using the same fake-binary
technique as test_guardrails.py, to confirm a detected loop actually blocks scheduling.
"""

import stat
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core.loop_detector import detect_loop
from app.db import session as db_session
from app.db.models import Agent, Base, Run, Ticket, Workspace
from app.db.session import get_session
from app.main import app


# ---------------------------------------------------------------------------
# Unit tests: detect_loop() directly against a DB session.
# ---------------------------------------------------------------------------


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _setup(session, agent_names=("eng-1", "lead-1", "qa-1")):
    ws = Workspace(name="W", key="MAP", repo_path="/tmp")
    session.add(ws)
    await session.flush()
    ticket = Ticket(workspace_id=ws.id, key="MAP-1", title="t")
    session.add(ticket)
    agents = {}
    role_map = {"eng-1": "engineer", "lead-1": "lead", "qa-1": "qa"}
    for name in agent_names:
        a = Agent(workspace_id=ws.id, name=name, role=role_map[name], model="m", tool_kind="opencode")
        session.add(a)
        agents[name] = a
    await session.flush()
    await session.commit()
    return ws, ticket, agents


async def _add_run(session, ticket, agent, offset_sec):
    run = Run(
        ticket_id=ticket.id,
        agent_id=agent.id,
        status="done",
        trigger="manual",
        tool_kind="opencode",
        model="m",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset_sec),
    )
    session.add(run)
    await session.commit()


async def test_a_b_a_b_a_trips_at_threshold_2(session):
    ws, ticket, agents = await _setup(session)
    # history: A, B, A, B (4 runs done); candidate 5th run: A
    for i, name in enumerate(["eng-1", "lead-1", "eng-1", "lead-1"]):
        await _add_run(session, ticket, agents[name], i)

    result = await detect_loop(session, ticket, {"loop_threshold": 2}, agents["eng-1"].id)
    assert result is not None
    assert "eng-1" in result and "lead-1" in result


async def test_a_b_c_a_never_trips(session):
    ws, ticket, agents = await _setup(session)
    for i, name in enumerate(["eng-1", "lead-1", "qa-1"]):
        await _add_run(session, ticket, agents[name], i)

    result = await detect_loop(session, ticket, {"loop_threshold": 2}, agents["eng-1"].id)
    assert result is None

    # Even a very low threshold shouldn't trip -- the C in the middle breaks alternation.
    result = await detect_loop(session, ticket, {"loop_threshold": 1}, agents["eng-1"].id)
    assert result is None


async def test_boundary_below_threshold_does_not_trip(session):
    ws, ticket, agents = await _setup(session)
    # history: A, B, A (3 runs); candidate 4th run: B -> sequence A,B,A,B, round_trips=1
    for i, name in enumerate(["eng-1", "lead-1", "eng-1"]):
        await _add_run(session, ticket, agents[name], i)

    result = await detect_loop(session, ticket, {"loop_threshold": 2}, agents["lead-1"].id)
    assert result is None


async def test_higher_threshold_requires_more_round_trips(session):
    ws, ticket, agents = await _setup(session)
    # A,B,A,B,A,B,A candidate -> history 6 runs + candidate = 7, round_trips=(7-1)//2=3
    names = ["eng-1", "lead-1", "eng-1", "lead-1", "eng-1", "lead-1"]
    for i, name in enumerate(names):
        await _add_run(session, ticket, agents[name], i)

    # threshold 3: round_trips(3) >= 3 -> trips
    result = await detect_loop(session, ticket, {"loop_threshold": 3}, agents["eng-1"].id)
    assert result is not None

    # threshold 4: round_trips(3) < 4 -> no trip
    result = await detect_loop(session, ticket, {"loop_threshold": 4}, agents["eng-1"].id)
    assert result is None


async def test_lower_threshold_trips_earlier(session):
    ws, ticket, agents = await _setup(session)
    # history: A, B (2 runs); candidate 3rd run: A -> sequence A,B,A, round_trips=1
    for i, name in enumerate(["eng-1", "lead-1"]):
        await _add_run(session, ticket, agents[name], i)

    result = await detect_loop(session, ticket, {"loop_threshold": 1}, agents["eng-1"].id)
    assert result is not None

    result = await detect_loop(session, ticket, {"loop_threshold": 2}, agents["eng-1"].id)
    assert result is None


async def test_empty_history_never_trips(session):
    ws, ticket, agents = await _setup(session)
    result = await detect_loop(session, ticket, {"loop_threshold": 1}, agents["eng-1"].id)
    assert result is None


async def test_short_history_never_trips(session):
    ws, ticket, agents = await _setup(session)
    await _add_run(session, ticket, agents["eng-1"], 0)
    result = await detect_loop(session, ticket, {"loop_threshold": 1}, agents["lead-1"].id)
    assert result is None


# ---------------------------------------------------------------------------
# Integration test: through orchestrator.schedule() / POST /tickets/{key}/run.
# ---------------------------------------------------------------------------


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
        async with maker() as s:
            yield s

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


def _make_agent(client, ws_id, role, name, model="opencode/big-pickle"):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": model, "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_ticket(client, ws_id, title="Do the thing"):
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json={"title": title, "description": "d"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_status(client, key, status):
    resp = client.patch(f"/api/tickets/{key}", json={"status": status})
    assert resp.status_code == 200, resp.text


def _wait_for_run(client, run_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/runs/{run_id}")
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.03)
    raise TimeoutError(f"run {run_id} did not reach a terminal state within {timeout}s")


def _system_comment_bodies(client, key):
    detail = client.get(f"/api/tickets/{key}").json()
    return [c["body"] for c in detail["comments"] if c["is_system"]]


# Both scripts below give a real `mention` (docs/03-agent-design.md §6) instead of
# `mention: []`. Since MAP-029, the orchestrator auto-schedules a follow-up run for
# every valid mention, so only the FIRST run in each test is triggered manually --
# the rest of the sequence plays out through the real handoff engine, exactly the same
# `schedule()`/`detect_loop()` codepath a manual re-trigger used to exercise directly.

_ALTERNATOR_SCRIPT = '''
import json, sys
model = sys.argv[sys.argv.index("-m") + 1]
# Alternate the ticket between in_progress <-> review so eng/lead runs are each valid
# state-machine transitions (engineer: in_progress->review; lead: review->in_progress),
# each one handing off to the other -- this lets the loop detector's history build up
# through *real* schedule()/execute() calls instead of stubbing status transitions.
status = "review" if model == "opencode/eng" else "in_progress"
mention = "lead-1" if model == "opencode/eng" else "eng-1"
text = f"""ok

```map
status: {status}
mention: [{mention}]
summary: |
  done
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''

_ABCA_SCRIPT = '''
import json, sys
model = sys.argv[sys.argv.index("-m") + 1]
# in_progress --eng(review)--> review --lead(qa)--> qa --qa(in_progress)--> in_progress,
# each handing off to the next role in the chain.
status = {"opencode/eng": "review", "opencode/lead": "qa", "opencode/qa": "in_progress"}[model]
mention = {"opencode/eng": "lead-1", "opencode/lead": "qa-1", "opencode/qa": "eng-1"}[model]
text = f"""ok

```map
status: {status}
mention: [{mention}]
summary: |
  done
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def _wait_for_ticket_status(client, key, statuses, timeout=15.0):
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        body = client.get(f"/api/tickets/{key}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.03)
    raise TimeoutError(f"ticket {key} did not reach {statuses} within {timeout}s: {body}")


def test_loop_detector_blocks_scheduling_via_api(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path, guardrails={"loop_threshold": 2})
    _make_agent(client, ws_id, "engineer", "eng-1", model="opencode/eng")
    _make_agent(client, ws_id, "lead", "lead-1", model="opencode/lead")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _ALTERNATOR_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    eng = client.get(f"/api/workspaces/{ws_id}/agents").json()
    eng_id = next(a["id"] for a in eng if a["name"] == "eng-1")

    # Kick off eng-1 manually; eng-1 <-> lead-1 keep handing off to each other
    # automatically from there (A->B->A->B->A) until the loop detector trips at the
    # 5th scheduling attempt (round_trips=2, threshold=2).
    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 201, resp.text

    detail = _wait_for_ticket_status(client, ticket["key"], {"blocked"})
    assert detail["status"] == "blocked"

    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("Loop terdeteksi" in b and "eng-1" in b and "lead-1" in b for b in bodies)


def test_loop_detector_does_not_block_a_b_c_a_via_api(client, tmp_path, monkeypatch):
    # max_handoff_depth=4 caps the auto-chain at exactly eng->lead->qa->eng (3 handoffs,
    # 4 runs) so the test doesn't depend on racing/predicting how many more A<->B hops
    # it'd take to eventually, correctly, trip loop_threshold further down the
    # (otherwise unbounded) chain -- this test only cares that the A,B,C,A pattern
    # itself doesn't trip the loop detector. The 4th run's own handoff attempt (back to
    # lead-1) is what actually hits the depth cap, stopping the chain right after the
    # full A,B,C,A sequence has run.
    ws_id = _make_workspace(
        client, tmp_path, guardrails={"loop_threshold": 2, "max_handoff_depth": 4}
    )
    _make_agent(client, ws_id, "engineer", "eng-1", model="opencode/eng")
    _make_agent(client, ws_id, "lead", "lead-1", model="opencode/lead")
    _make_agent(client, ws_id, "qa", "qa-1", model="opencode/qa")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _ABCA_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    agents = client.get(f"/api/workspaces/{ws_id}/agents").json()
    eng_id = next(a["id"] for a in agents if a["name"] == "eng-1")

    # in_progress --eng--> review --lead--> qa --qa--> in_progress, each a real transition,
    # each auto-handed-off via mention. The chain then stops on max_handoff_depth (not
    # the loop detector) after the 3rd handoff.
    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 201, resp.text

    detail = _wait_for_ticket_status(client, ticket["key"], {"blocked"})
    assert detail["status"] == "blocked"

    bodies = _system_comment_bodies(client, ticket["key"])
    assert not any("Loop terdeteksi" in b for b in bodies)
    assert any("max_handoff_depth" in b for b in bodies)

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    ticket_runs = [r for r in runs if r["ticket_id"] == ticket["id"]]
    assert len(ticket_runs) == 4, ticket_runs
    assert all(r["status"] == "done" for r in ticket_runs)
