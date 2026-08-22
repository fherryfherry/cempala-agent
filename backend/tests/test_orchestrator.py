"""Integration tests for MAP-023: run API + orchestrator.

Fake `opencode` binaries (shell/python scripts, same technique as MAP-020's
test_opencode_tool.py) drive controlled scenarios end to end through the real
HTTP API and a real (in-memory) DB — no mocking of the orchestrator's DB layer.
"""

import stat
import subprocess
import time
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core import orchestrator
from app.db import session as db_session
from app.db.models import Base
from app.db.session import get_session
from app.main import app


@pytest.fixture
async def client(tmp_path, monkeypatch):
    # A real file-backed DB, not :memory:. Orchestrator background tasks hold a DB
    # session open concurrently with the request's own session (e.g. polling GET
    # /runs/{id} while a run is mid-flight) — :memory: forces SQLAlchemy onto a single
    # shared StaticPool connection, and two SQLAlchemy Sessions issuing overlapping
    # transactions on literally the same DBAPI connection corrupts session state
    # ("Could not refresh instance ..."). A file DB gets the normal connection pool
    # (one real connection per session) and SQLite's own file locking + busy_timeout
    # (set below, same as app/db/session.py) serializes writers correctly — this is
    # exactly what production uses (DATABASE_URL points at a file, not :memory:).
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
    # The orchestrator's background tasks open their own sessions outside the
    # request/response cycle — point them at the same test engine/maker.
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


# ---------------------------------------------------------------------------
# (a) valid ```map block -> ticket transitions, summary comment, mentions recorded
# ---------------------------------------------------------------------------

_VALID_MAP_SCRIPT = '''
import json
text = """Done working on it.

```map
status: review
mention: [lead-1]
summary: |
  Implemented the thing. Tests pass.
```
"""
print(json.dumps({
    "type": "assistant_text",
    "text": text,
    "session_id": "sess-happy",
    "tokens_in": 12,
    "tokens_out": 8,
    "cost": 0.05,
}))
'''


def test_valid_map_block_transitions_ticket_and_records_mentions(client, tmp_path, monkeypatch):
    """This test predates MAP-029's handoff engine: a valid `mention` to a real,
    enabled agent now automatically schedules a follow-up run for them. Since the
    fake binary here is shared and always reports `status: review` (only legal for
    engineer/designer), a real lead-1 follow-up run would deterministically fail its
    own role check and re-block the ticket — racily, depending on whether that async
    follow-up finishes before this test's assertions run. Disabling lead-1 keeps this
    test focused on MAP-023's original concern (single run -> parse -> apply) by making
    the handoff resolve synchronously to "agent nonaktif" with no second subprocess."""
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    lead_id = _make_agent(client, ws_id, "lead", "lead-1")
    resp = client.patch(f"/api/agents/{lead_id}", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _VALID_MAP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    assert resp.status_code == 201, resp.text
    run = resp.json()

    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["session_id"] == "sess-happy"
    assert final["report"]["status"] == "review"
    assert final["report"]["mention"] == ["lead-1"]

    # lead-1 is disabled, so the handoff engine can't schedule a follow-up run for the
    # mention; per docs/03-agent-design.md §6 that's recorded as "agent X nonaktif", and
    # since "review" isn't a final status with no valid target, the ticket is blocked.
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"
    bodies = [c["body"] for c in detail["comments"]]
    mentions = next(c["mentions"] for c in detail["comments"] if not c["is_system"])
    assert any("Implemented the thing" in b for b in bodies)
    assert mentions == ["lead-1"]
    assert any("lead-1" in b and "nonaktif" in b for b in bodies)

    agent = client.get(f"/api/workspaces/{ws_id}/agents").json()
    eng = next(a for a in agent if a["id"] == eng_id)
    assert eng["status"] == "idle"


# ---------------------------------------------------------------------------
# (b) missing/garbage map block -> ticket blocked, system comment has last 2000 chars
# ---------------------------------------------------------------------------

_GARBAGE_SCRIPT = '''
import json
print(json.dumps({"type": "assistant_text", "text": "I did some stuff but forgot the block."}))
'''


def test_missing_map_block_blocks_ticket_with_tail_output(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _GARBAGE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "failed"

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"
    system_comments = [c for c in detail["comments"] if c["is_system"]]
    assert any("forgot the block" in c["body"] for c in system_comments)


# ---------------------------------------------------------------------------
# (c) nonzero exit -> ticket blocked, run failed
# ---------------------------------------------------------------------------


def test_nonzero_exit_blocks_ticket_and_fails_run(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_script(
        tmp_path / "opencode",
        r""">&2 printf 'boom: it broke\n'
exit 1""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "failed"
    assert "boom: it broke" in final["error"]

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"


# ---------------------------------------------------------------------------
# (d) hanging binary, stopped via POST /runs/{id}/stop -> cancelled, process killed
# ---------------------------------------------------------------------------


def test_stop_cancels_running_run_and_kills_process(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    # Unique sleep duration per test run so `pgrep -f` can never collide with a
    # process leaked by an unrelated, unclean prior test run still alive on the box.
    sleep_marker = f"sleep {uuid.uuid4().int % 900000 + 100000}"
    script = _write_script(tmp_path / "opencode", f"exec {sleep_marker}\n")
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    run = resp.json()

    # Wait for the subprocess to actually spawn.
    deadline = time.time() + 5
    pids = []
    while time.time() < deadline and not pids:
        found = subprocess.run(
            ["pgrep", "-f", sleep_marker], capture_output=True, text=True
        ).stdout.split()
        pids = [int(p) for p in found]
        if not pids:
            time.sleep(0.05)
    assert pids, f"expected fake opencode's `{sleep_marker}` child to have spawned"

    stop_resp = client.post(f"/api/runs/{run['id']}/stop")
    assert stop_resp.status_code == 200, stop_resp.text

    final = _wait_for_run(client, run["id"])
    assert final["status"] == "cancelled"

    for pid in pids:
        result = subprocess.run(["ps", "-p", str(pid)], capture_output=True, text=True)
        assert result.returncode != 0, f"pid {pid} still alive after stop"

    agent = client.get(f"/api/workspaces/{ws_id}/agents").json()
    eng = next(a for a in agent if a["id"] == eng_id)
    assert eng["status"] == "idle"


# ---------------------------------------------------------------------------
# (e) two runs scheduled for the same agent -> second queues, never both running
# ---------------------------------------------------------------------------

_SLOW_DONE_SCRIPT = '''
import json, time
time.sleep(0.5)
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


def test_two_runs_same_agent_never_both_running(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    t1 = _make_ticket(client, ws_id, "first")
    t2 = _make_ticket(client, ws_id, "second")
    for t in (t1, t2):
        _set_status(client, t["key"], "todo")
        _set_status(client, t["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _SLOW_DONE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    r1 = client.post(f"/api/tickets/{t1['key']}/run", json={"agent_id": eng_id}).json()
    r2 = client.post(f"/api/tickets/{t2['key']}/run", json={"agent_id": eng_id}).json()

    # Poll repeatedly until both runs reach a terminal state, recording every status
    # combo observed along the way. Must never see both "running" simultaneously, and
    # must see the "one running, one queued" combo at least once (proves the FIFO
    # queue actually held the second run back rather than starting it concurrently).
    seen_running_queued = False
    combos_seen = []
    deadline = time.time() + 10
    while time.time() < deadline:
        s1 = client.get(f"/api/runs/{r1['id']}").json()["status"]
        s2 = client.get(f"/api/runs/{r2['id']}").json()["status"]
        combos_seen.append((s1, s2))
        assert not (s1 == "running" and s2 == "running"), "both runs running simultaneously"
        if {s1, s2} == {"running", "queued"}:
            seen_running_queued = True
        if s1 not in ("queued", "running") and s2 not in ("queued", "running"):
            break
        time.sleep(0.02)
    else:
        raise TimeoutError("runs did not both finish in time")

    assert seen_running_queued, f"expected to observe one running + one queued; saw {combos_seen}"

    final1 = client.get(f"/api/runs/{r1['id']}").json()
    final2 = client.get(f"/api/runs/{r2['id']}").json()
    assert final1["status"] == "done"
    assert final2["status"] == "done"


# ---------------------------------------------------------------------------
# (f) unexpected exception inside orchestrator -> ticket blocked, agent idle
# ---------------------------------------------------------------------------


def test_orchestrator_exception_blocks_ticket_and_frees_agent(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    async def _boom(*args, **kwargs):
        raise RuntimeError("kaboom from a forced orchestrator bug")

    monkeypatch.setattr(orchestrator, "_build_prompt_for", _boom)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "failed"
    assert "kaboom from a forced orchestrator bug" in final["error"]

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"
    system_comments = [c for c in detail["comments"] if c["is_system"]]
    assert any("kaboom from a forced orchestrator bug" in c["body"] for c in system_comments)

    agent = client.get(f"/api/workspaces/{ws_id}/agents").json()
    eng = next(a for a in agent if a["id"] == eng_id)
    assert eng["status"] == "idle"


# ---------------------------------------------------------------------------
# Misc API behavior
# ---------------------------------------------------------------------------


def test_run_without_agent_id_uses_ticket_assignee(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id, assignee_id=eng_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _VALID_MAP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    _make_agent(client, ws_id, "lead", "lead-1")

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["agent_id"] == eng_id


def test_run_without_agent_id_or_assignee_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_agent"


def test_list_workspace_runs_filters_by_status(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_script(
        tmp_path / "opencode",
        r""">&2 printf 'nope\n'
exit 1""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])

    resp = client.get(f"/api/workspaces/{ws_id}/runs", params={"status": "failed"})
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert run["id"] in ids

    resp = client.get(f"/api/workspaces/{ws_id}/runs", params={"status": "done"})
    assert run["id"] not in [r["id"] for r in resp.json()]


# ---------------------------------------------------------------------------
# backlog/todo run-start auto-transition
# ---------------------------------------------------------------------------

_PM_IN_PROGRESS_SCRIPT = '''
import json
text = """working on it

```map
status: in_progress
mention: []
summary: |
  breakdown in progress
```
"""
print(json.dumps({"type": "assistant_text", "text": text, "session_id": "sess-backlog"}))
'''


def test_run_from_backlog_auto_transitions_and_completes(client, tmp_path, monkeypatch):
    """A ticket created and run immediately (never dragged to todo first) must not get
    its otherwise-valid report bounced by the state machine (found via MAP-033
    dogfooding: can_transition has no (backlog, in_progress) entry for any role)."""
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)
    assert ticket["status"] == "backlog"

    script = _write_python_binary(tmp_path / "opencode", _PM_IN_PROGRESS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id})
    assert resp.status_code == 201, resp.text
    run = resp.json()

    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["report"]["status"] == "in_progress"

    # The report itself was accepted (run "done", not bounced by the state machine) —
    # that's the fix under test. What happens next (handoff blocks it since PM reported
    # a non-final status with no mention/tickets[]) is unrelated existing behavior.
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    bodies = [c["body"] for c in detail["comments"] if c["is_system"]]
    assert any("Status changed from backlog to in_progress" in b for b in bodies)


def test_run_from_todo_still_auto_transitions_and_completes(client, tmp_path, monkeypatch):
    """Regression coverage: a ticket already manually moved to todo still works
    identically to before this change."""
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(tmp_path / "opencode", _PM_IN_PROGRESS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id})
    assert resp.status_code == 201, resp.text
    run = resp.json()

    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    bodies = [c["body"] for c in detail["comments"] if c["is_system"]]
    assert any("Status changed from todo to in_progress" in b for b in bodies)


# ---------------------------------------------------------------------------
# extra_instructions: PM mention-triggered runs only
# ---------------------------------------------------------------------------

_MARKER = "BOLEH balas cuma dengan pertanyaan klarifikasi"


def test_extra_instructions_marker_present_only_for_pm_mention_trigger(
    client, tmp_path, monkeypatch
):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)

    script = _write_python_binary(tmp_path / "opencode", _PM_IN_PROGRESS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    # trigger="manual" via direct run endpoint, PM role -> no marker.
    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    _wait_for_run(client, run["id"])
    detail = client.get(f"/api/runs/{run['id']}").json()
    prompt = detail["events"][0]["payload"]["prompt"]
    assert _MARKER not in prompt

    # trigger="mention" via owner comment mentioning pm-1 -> marker present. (Comments
    # don't care about ticket status, so no need to touch it here even if the first
    # run above left it blocked via the handoff engine.)
    resp = client.post(
        f"/api/tickets/{ticket['key']}/comments", json={"body": "@pm-1 thoughts?"}
    )
    assert resp.status_code == 201, resp.text

    # Find the (only) mention-triggered run for pm-1 on this ticket.
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    mention_run = next(r for r in runs if r["agent_id"] == pm_id and r["trigger"] == "mention")
    _wait_for_run(client, mention_run["id"])
    detail2 = client.get(f"/api/runs/{mention_run['id']}").json()
    prompt2 = detail2["events"][0]["payload"]["prompt"]
    assert _MARKER in prompt2

    # trigger="mention" but engineer role -> no marker.
    resp = client.post(
        f"/api/tickets/{ticket['key']}/comments", json={"body": "@eng-1 thoughts?"}
    )
    assert resp.status_code == 201, resp.text
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    eng_mention_run = next(r for r in runs if r["agent_id"] == eng_id and r["trigger"] == "mention")
    _wait_for_run(client, eng_mention_run["id"])
    detail3 = client.get(f"/api/runs/{eng_mention_run['id']}").json()
    prompt3 = detail3["events"][0]["payload"]["prompt"]
    assert _MARKER not in prompt3


# ---------------------------------------------------------------------------
# updates[]: a report modifying OTHER existing tickets
# ---------------------------------------------------------------------------


def _updates_script(entries_yaml: str, status: str = "in_progress") -> str:
    return f'''
import json
text = """working

```map
status: {status}
mention: []
summary: |
  handled updates
updates:
{entries_yaml}
```
"""
print(json.dumps({{"type": "assistant_text", "text": text}}))
'''


def test_updates_legal_status_change_applies_to_target(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    source = _make_ticket(client, ws_id, "source")
    target = _make_ticket(client, ws_id, "target")
    # legal transition for pm role: in_progress -> done
    _set_status(client, target["key"], "todo")
    _set_status(client, target["key"], "in_progress")

    entries = f"  - ticket: {target['key']}\n    status: done\n"
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["report"]["updates"] == [{"ticket": target["key"], "applied": ["status → done"], "skipped": []}]

    target_detail = client.get(f"/api/tickets/{target['key']}").json()
    assert target_detail["status"] == "done"
    system_bodies = [c["body"] for c in target_detail["comments"] if c["is_system"]]
    assert any(source["key"] in b and "pm-1" in b for b in system_bodies)


def test_updates_illegal_status_change_skipped_with_note(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    source = _make_ticket(client, ws_id, "source")
    target = _make_ticket(client, ws_id, "target")
    # target stays at backlog; (backlog, done) has no allowed role -> illegal for pm
    assert target["status"] == "backlog"

    entries = f"  - ticket: {target['key']}\n    status: done\n"
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["report"]["updates"][0]["applied"] == []
    assert final["report"]["updates"][0]["skipped"]

    target_detail = client.get(f"/api/tickets/{target['key']}").json()
    assert target_detail["status"] == "backlog"

    source_detail = client.get(f"/api/tickets/{source['key']}").json()
    system_bodies = [c["body"] for c in source_detail["comments"] if c["is_system"]]
    assert any("Beberapa updates diabaikan" in b for b in system_bodies)


def test_updates_unknown_or_wrong_workspace_ticket_skipped_cleanly(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path, key="MAPA")
    other_ws_id = _make_workspace(client, tmp_path, key="MAPB")
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    source = _make_ticket(client, ws_id, "source")
    other_ticket = _make_ticket(client, other_ws_id, "other")

    entries = (
        "  - ticket: NOPE-999\n    priority: high\n"
        f"  - ticket: {other_ticket['key']}\n    priority: high\n"
    )
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final

    # The report itself was accepted (run "done"); the source ticket then gets blocked
    # by the (unrelated) handoff engine since a non-final status with no mention/
    # tickets[] always gets blocked so it doesn't hang — not a concern of this test.
    source_detail = client.get(f"/api/tickets/{source['key']}").json()
    system_bodies = [c["body"] for c in source_detail["comments"] if c["is_system"]]
    assert any("Beberapa updates diabaikan" in b for b in system_bodies)

    other_detail = client.get(f"/api/tickets/{other_ticket['key']}").json()
    assert other_detail["priority"] == "medium"  # untouched


def test_updates_priority_and_assignee_only_never_touch_can_transition(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    eng2_id = _make_agent(client, ws_id, "engineer", "eng-2")
    source = _make_ticket(client, ws_id, "source")
    target = _make_ticket(client, ws_id, "target")  # stays at backlog

    entries = f"  - ticket: {target['key']}\n    priority: urgent\n    assignee: eng-2\n"
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["report"]["updates"][0]["skipped"] == []

    target_detail = client.get(f"/api/tickets/{target['key']}").json()
    assert target_detail["status"] == "backlog"  # untouched, no state-machine check ran
    assert target_detail["priority"] == "urgent"
    assert target_detail["assignee_id"] == eng2_id


def test_updates_multiple_entries_mixed_success_and_failure_independent(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    source = _make_ticket(client, ws_id, "source")
    ok_target = _make_ticket(client, ws_id, "ok-target")
    bad_target = _make_ticket(client, ws_id, "bad-target")

    entries = (
        f"  - ticket: {ok_target['key']}\n    priority: high\n"
        f"  - ticket: {bad_target['key']}\n    status: qa\n"  # illegal for pm from backlog
        "  - ticket: GHOST-1\n    priority: low\n"
    )
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final

    ok_detail = client.get(f"/api/tickets/{ok_target['key']}").json()
    assert ok_detail["priority"] == "high"

    bad_detail = client.get(f"/api/tickets/{bad_target['key']}").json()
    assert bad_detail["status"] == "backlog"

    source_detail = client.get(f"/api/tickets/{source['key']}").json()
    system_bodies = [c["body"] for c in source_detail["comments"] if c["is_system"]]
    diagnostic = next(b for b in system_bodies if "Beberapa updates diabaikan" in b)
    assert "bad-target" in diagnostic or bad_target["key"] in diagnostic
    assert "GHOST-1" in diagnostic


def test_get_run_includes_events(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    _make_agent(client, ws_id, "lead", "lead-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _VALID_MAP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])

    detail = client.get(f"/api/runs/{run['id']}").json()
    types = [e["type"] for e in detail["events"]]
    assert types[0] == "run_started"
    assert "prompt" in detail["events"][0]["payload"]
    assert types[-1] == "run_ended"
