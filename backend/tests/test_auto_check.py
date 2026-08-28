"""Tests for the built-in auto-check scheduler (MAP-050): stale tickets in the
active sprint get a follow-up run for their (idle) assigned agent; busy agents
and disabled check settings are skipped; the PM picks up unassigned/busy cases.
"""

import asyncio
import stat
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core import auto_check, orchestrator
from app.db import session as db_session
from app.db.models import Base, Ticket, TicketAutoCheck
from app.db.session import get_session
from app.main import app

# conftest.py's autouse `_disable_background_schedulers` fixture monkeypatches the
# `run_auto_check` module attribute to a no-op for every test. Capture the real
# function here at collection time (before any per-test monkeypatch runs) so the
# loop-body test below can call the actual implementation.
_REAL_RUN_AUTO_CHECK = auto_check.run_auto_check


@pytest.fixture
def client_maker(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))

    db_path = tmp_path / f"{uuid.uuid4().hex}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    monkeypatch.setattr(db_session, "async_session", maker)

    with TestClient(app) as c:
        yield c, maker

    app.dependency_overrides.clear()

    async def _dispose():
        await engine.dispose()

    asyncio.run(_dispose())


def _make_workspace(client, tmp_path, key="MAP"):
    resp = client.post(
        "/api/workspaces", json={"name": "Map", "key": key, "repo_path": str(tmp_path)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_agent(client, ws_id, role, name):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": "opencode/big-pickle", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_ticket(client, ws_id, sprint_id, title="Do the thing", assignee_id=None):
    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={
            "title": title,
            "is_new_epic": True,
            "sprint_id": sprint_id,
            "assignee_id": assignee_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _age_ticket(maker, ticket_id: str, minutes: int) -> None:
    """Backdate a ticket's updated_at so the staleness check trips."""

    async def _run():
        async with maker() as s:
            t = await s.get(Ticket, ticket_id)
            t.updated_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            await s.commit()

    asyncio.run(_run())


def _run_tick(client_maker):
    _, maker = client_maker

    async def _t():
        async with maker() as s:
            await auto_check._tick(s, maker)

    asyncio.run(_t())


def _write_python_binary(path, code):
    path.write_text(f"#!/usr/bin/env python3\n{code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _tick_and_run_auto(client_maker):
    """Run one auto-check tick and, in the same event loop, wait for any newly
    scheduled `trigger=auto` run to finish.

    `schedule()` fires the actual run as a background `asyncio.create_task` on
    whatever loop calls it (`orchestrator.RUNNING`). A fresh `asyncio.run()` per
    tick (like `_run_tick`) returns and tears down its loop the instant `_tick()`
    itself returns, orphaning/cancelling that task before the fake opencode
    subprocess ever gets to run — so tests that need to observe the finished
    result (posted comment, dedup state) must await it inside the same loop.

    Returns the finished run's id, or None if nothing new was scheduled (e.g.
    still within backoff).
    """
    _, maker = client_maker

    async def _t():
        before = dict(orchestrator.RUNNING)
        async with maker() as s:
            await auto_check._tick(s, maker)
        new_tasks = {rid: t for rid, t in orchestrator.RUNNING.items() if rid not in before}
        for t in new_tasks.values():
            await t
        return list(new_tasks.keys())

    new_ids = asyncio.run(_t())
    assert len(new_ids) <= 1
    return new_ids[0] if new_ids else None


def _auto_check_state(maker, ticket_id: str) -> TicketAutoCheck | None:
    async def _get():
        async with maker() as s:
            return await s.get(TicketAutoCheck, ticket_id)

    return asyncio.run(_get())


def _backdate_auto_check(maker, ticket_id: str, minutes: int) -> None:
    async def _run():
        async with maker() as s:
            state = await s.get(TicketAutoCheck, ticket_id)
            state.last_nudge_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            await s.commit()

    asyncio.run(_run())


def test_auto_check_nudges_stale_ticket_for_idle_agent(client_maker, tmp_path, monkeypatch):
    client, maker = client_maker
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")

    ws = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws["id"], "engineer", "eng-1")
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    assert sprint["status"] == "active"  # first sprint bootstraps active
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Stale ticket", assignee_id=eng["id"])
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})
    _age_ticket(maker, ticket["id"], minutes=10)

    _run_tick(client_maker)

    runs = client.get(f"/api/workspaces/{ws['id']}/runs").json()
    auto_runs = [r for r in runs if r["trigger"] == "auto"]
    assert len(auto_runs) == 1
    assert auto_runs[0]["ticket_id"] == ticket["id"]
    assert auto_runs[0]["agent_id"] == eng["id"]


def test_auto_check_skips_fresh_ticket(client_maker, tmp_path):
    client, maker = client_maker
    ws = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws["id"], "engineer", "eng-1")
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    _make_ticket(client, ws["id"], sprint["id"], "Fresh ticket", assignee_id=eng["id"])
    # No backdating — ticket is fresh, must NOT be nudged.

    _run_tick(client_maker)

    runs = client.get(f"/api/workspaces/{ws['id']}/runs").json()
    assert all(r["trigger"] != "auto" for r in runs)


def test_auto_check_skips_when_disabled(client_maker, tmp_path):
    client, maker = client_maker
    ws = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws["id"], "engineer", "eng-1")
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Stale ticket", assignee_id=eng["id"])
    _age_ticket(maker, ticket["id"], minutes=10)

    # Disable the auto-check: interval 0.
    client.patch(
        f"/api/workspaces/{ws['id']}",
        json={"guardrails": {**ws["guardrails"], "auto_check_interval_minutes": 0}},
    )

    _run_tick(client_maker)

    runs = client.get(f"/api/workspaces/{ws['id']}/runs").json()
    assert runs == []


def test_auto_check_no_active_sprint_skips(client_maker, tmp_path):
    client, maker = client_maker
    ws = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws["id"], "engineer", "eng-1")
    # No sprint at all -> no active sprint -> skip.
    ticket = client.post(
        f"/api/workspaces/{ws['id']}/tickets",
        json={"title": "No sprint", "is_new_epic": True, "assignee_id": eng["id"]},
    ).json()
    _age_ticket(maker, ticket["id"], minutes=10)

    _run_tick(client_maker)

    runs = client.get(f"/api/workspaces/{ws['id']}/runs").json()
    assert runs == []


# ---------------------------------------------------------------------------
# Anti-spam: a repeated "nothing new" auto-nudge is deduped and the schedule
# backs off, instead of posting an identical comment every tick forever.
# ---------------------------------------------------------------------------

_NOOP_SCRIPT = '''
import json
text = """Checked in.

```map
status: in_progress
summary: |
  Tidak ada instruksi baru dari owner.
```
"""
print(json.dumps({
    "type": "assistant_text",
    "text": text,
    "session_id": "sess-noop",
    "tokens_in": 5,
    "tokens_out": 5,
    "cost": 0.01,
}))
'''

_REAL_UPDATE_SCRIPT = '''
import json
text = """Owner replied with new instructions.

```map
status: in_progress
summary: |
  Owner minta prioritaskan halaman legal dulu.
```
"""
print(json.dumps({
    "type": "assistant_text",
    "text": text,
    "session_id": "sess-real",
    "tokens_in": 5,
    "tokens_out": 5,
    "cost": 0.01,
}))
'''


def test_auto_check_dedups_repeated_noop_and_backs_off(client_maker, tmp_path, monkeypatch):
    client, maker = client_maker
    script = _write_python_binary(tmp_path / "opencode", _NOOP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    ws = _make_workspace(client, tmp_path)
    _make_agent(client, ws["id"], "pm", "pm-1")  # unassigned ticket -> PM gets nudged
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Stuck ticket")
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})

    # First nudge: nothing to dedupe against yet -> posts once.
    _age_ticket(maker, ticket["id"], minutes=10)
    run_id = _tick_and_run_auto(client_maker)
    assert run_id is not None

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    non_system = [c for c in detail["comments"] if not c["is_system"]]
    assert len(non_system) == 1
    assert _auto_check_state(maker, ticket["id"]) is None  # first post isn't a dup

    # Second nudge, same boilerplate: deduped, no new comment, skip_count bumped.
    _age_ticket(maker, ticket["id"], minutes=10)
    run_id2 = _tick_and_run_auto(client_maker)
    assert run_id2 is not None

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    non_system = [c for c in detail["comments"] if not c["is_system"]]
    assert len(non_system) == 1  # still just the one comment
    state = _auto_check_state(maker, ticket["id"])
    assert state is not None and state.skip_count == 1

    # Third tick right away: still within the backoff window (stale_min * 2 = 6min
    # since last_nudge_at), so no new run gets scheduled at all even though the
    # ticket itself is stale again.
    _age_ticket(maker, ticket["id"], minutes=10)
    run_id3 = _tick_and_run_auto(client_maker)
    assert run_id3 is None  # backoff window still open

    # Clear the backoff window manually (simulating time passing) -> nudged again,
    # still deduped (same boilerplate), skip_count keeps climbing.
    _backdate_auto_check(maker, ticket["id"], minutes=10)
    run_id4 = _tick_and_run_auto(client_maker)
    assert run_id4 is not None

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    non_system = [c for c in detail["comments"] if not c["is_system"]]
    assert len(non_system) == 1  # still deduped
    state = _auto_check_state(maker, ticket["id"])
    assert state.skip_count == 2


def test_auto_check_real_update_resets_backoff(client_maker, tmp_path, monkeypatch):
    """A genuinely new report (not a near-duplicate) resets the backoff state so the
    next auto-check goes back to the tight cadence."""
    client, maker = client_maker
    script = _write_python_binary(tmp_path / "opencode", _NOOP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    ws = _make_workspace(client, tmp_path)
    _make_agent(client, ws["id"], "pm", "pm-1")
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Stuck ticket")
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})

    _age_ticket(maker, ticket["id"], minutes=10)
    assert _tick_and_run_auto(client_maker) is not None

    # Second nudge dedupes and records a skip.
    _age_ticket(maker, ticket["id"], minutes=10)
    assert _tick_and_run_auto(client_maker) is not None
    assert _auto_check_state(maker, ticket["id"]).skip_count == 1

    # Now the owner actually said something new -> next nudge reports real content.
    script2 = _write_python_binary(tmp_path / "opencode2", _REAL_UPDATE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script2)
    _backdate_auto_check(maker, ticket["id"], minutes=10)
    _age_ticket(maker, ticket["id"], minutes=10)
    run_id3 = _tick_and_run_auto(client_maker)
    assert run_id3 is not None

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    non_system = [c for c in detail["comments"] if not c["is_system"]]
    assert len(non_system) == 2  # the real update posted this time
    assert "prioritaskan halaman legal" in non_system[-1]["body"]
    assert _auto_check_state(maker, ticket["id"]) is None  # backoff state cleared


def test_auto_check_busy_assignee_nudges_pm(client_maker, tmp_path, monkeypatch):
    client, maker = client_maker
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")

    ws = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws["id"], "engineer", "eng-1")
    pm = _make_agent(client, ws["id"], "pm", "pm-1")
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Busy ticket", assignee_id=eng["id"])
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})
    _age_ticket(maker, ticket["id"], minutes=10)

    # Make the assigned engineer busy -> PM gets the nudge.
    import asyncio
    from app.db.models import Agent as AgentModel

    async def _set_busy():
        async with db_session.async_session() as s:
            a = await s.get(AgentModel, eng["id"])
            a.status = "working"
            await s.commit()

    asyncio.run(_set_busy())
    _run_tick(client_maker)

    runs = client.get(f"/api/workspaces/{ws['id']}/runs").json()
    auto_runs = [r for r in runs if r["trigger"] == "auto"]
    assert len(auto_runs) == 1
    assert auto_runs[0]["agent_id"] == pm["id"]


def test_auto_check_no_assignee_nudges_pm(client_maker, tmp_path, monkeypatch):
    client, maker = client_maker
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")

    ws = _make_workspace(client, tmp_path)
    pm = _make_agent(client, ws["id"], "pm", "pm-1")
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Unassigned ticket")
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})
    _age_ticket(maker, ticket["id"], minutes=10)

    _run_tick(client_maker)

    runs = client.get(f"/api/workspaces/{ws['id']}/runs").json()
    auto_runs = [r for r in runs if r["trigger"] == "auto"]
    assert len(auto_runs) == 1
    assert auto_runs[0]["agent_id"] == pm["id"]


def test_auto_check_loop_stops_on_event(client_maker):
    import asyncio

    stop_event = asyncio.Event()
    stop_event.set()
    asyncio.run(auto_check.run_auto_check(db_session.async_session, stop_event))


def test_auto_check_nudge_swallows_guardrail_blocked(client_maker, tmp_path, monkeypatch):
    from app.core.guardrails import GuardrailBlocked

    client, maker = client_maker
    ws = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws["id"], "engineer", "eng-1")
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Stale ticket", assignee_id=eng["id"])
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})
    _age_ticket(maker, ticket["id"], minutes=10)

    async def _blocked(*args, **kwargs):
        raise GuardrailBlocked("max_concurrent_runs", "too many runs")

    monkeypatch.setattr(orchestrator, "schedule", _blocked)

    _run_tick(client_maker)  # must not raise
    runs = client.get(f"/api/workspaces/{ws['id']}/runs").json()
    assert runs == []


def test_auto_check_nudge_swallows_runtime_error(client_maker, tmp_path, monkeypatch):
    client, maker = client_maker
    ws = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws["id"], "engineer", "eng-1")
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Stale ticket", assignee_id=eng["id"])
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})
    _age_ticket(maker, ticket["id"], minutes=10)

    async def _paused(*args, **kwargs):
        raise RuntimeError("workspace paused")

    monkeypatch.setattr(orchestrator, "schedule", _paused)

    _run_tick(client_maker)  # must not raise
    runs = client.get(f"/api/workspaces/{ws['id']}/runs").json()
    assert runs == []


def test_auto_check_normalizes_naive_last_nudge_at(client_maker, tmp_path, monkeypatch):
    """A naive last_nudge_at (e.g. left on an unexpired in-session object) must
    be normalized to UTC-aware before the backoff comparison, not raise."""
    client, maker = client_maker
    script = _write_python_binary(tmp_path / "opencode", _NOOP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    ws = _make_workspace(client, tmp_path)
    _make_agent(client, ws["id"], "pm", "pm-1")  # unassigned ticket -> PM gets nudged
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Stale ticket")
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})
    _age_ticket(maker, ticket["id"], minutes=10)
    _tick_and_run_auto(client_maker)  # first nudge, no TicketAutoCheck row yet
    _age_ticket(maker, ticket["id"], minutes=10)
    _tick_and_run_auto(client_maker)  # second (duplicate) nudge creates the row
    assert _auto_check_state(maker, ticket["id"]) is not None
    _age_ticket(maker, ticket["id"], minutes=10)  # keep it actionable for the next tick

    async def _tick_with_naive_last_nudge_at():
        async with maker() as s:
            state = await s.get(TicketAutoCheck, ticket["id"])
            state.last_nudge_at = datetime.now()  # naive, no tzinfo
            # Same session/identity map: _tick's select() reuses this in-memory
            # object rather than re-fetching (which would re-apply the
            # UTCDateTime type decorator and mask the naive value).
            await auto_check._tick(s, maker)

    asyncio.run(_tick_with_naive_last_nudge_at())  # must not raise TypeError


def test_auto_check_loop_runs_one_tick_then_stops(client_maker, tmp_path, monkeypatch):
    monkeypatch.setattr(auto_check, "_TICK_SECONDS", 0.01)
    client, maker = client_maker
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")

    ws = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws["id"], "engineer", "eng-1")
    sprint = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"}).json()
    ticket = _make_ticket(client, ws["id"], sprint["id"], "Stale ticket", assignee_id=eng["id"])
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})
    _age_ticket(maker, ticket["id"], minutes=10)

    async def _run_loop():
        stop_event = asyncio.Event()
        task = asyncio.create_task(_REAL_RUN_AUTO_CHECK(maker, stop_event))
        await asyncio.sleep(0.3)  # let at least one real tick fire
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(_run_loop())
    runs = client.get(f"/api/workspaces/{ws['id']}/runs").json()
    assert any(r["trigger"] == "auto" for r in runs)
