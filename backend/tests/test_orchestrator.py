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


def _active_sprint_id(client, ws_id):
    """Idempotent: reuse the workspace's active sprint if one exists, else create
    one (bootstraps active as the first sprint). Tests that actually assert on the
    sprints list opt out with an explicit `sprint_id=None` override to `_make_ticket`."""
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


def test_reset_requires_paused_workspace(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    _make_ticket(client, ws_id, "t1")
    resp = client.post(f"/api/workspaces/{ws_id}/reset")
    assert resp.status_code == 409


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

_MARKER = "JANGAN pernah langsung membuat tickets[]"


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


def test_pm_mention_prompt_includes_other_workspace_tickets(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id, "Chat thread")
    other = _make_ticket(client, ws_id, "Some other ticket")

    script = _write_python_binary(tmp_path / "opencode", _PM_IN_PROGRESS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    # trigger="manual" -> no workspace ticket list (only the chat flow needs it).
    # `other["key"]` may still legitimately appear via the always-on epic-reuse
    # catalog (both tickets here are top-level) — that's a separate, deliberate
    # feature (docs/03-agent-design.md §3), not what this test is about.
    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id})
    _wait_for_run(client, resp.json()["id"])
    detail = client.get(f"/api/runs/{resp.json()['id']}").json()
    assert "Tiket lain di workspace ini" not in detail["events"][0]["payload"]["prompt"]

    # trigger="mention" (owner chat) -> other tickets listed.
    client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "@pm-1 cek semua tiket"})
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    mention_run = next(r for r in runs if r["agent_id"] == pm_id and r["trigger"] == "mention")
    _wait_for_run(client, mention_run["id"])
    detail2 = client.get(f"/api/runs/{mention_run['id']}").json()
    prompt2 = detail2["events"][0]["payload"]["prompt"]
    assert "Tiket lain di workspace ini" in prompt2
    assert other["key"] in prompt2
    # the chat ticket itself isn't duplicated inside the "other tickets" list —
    # scoped to just that one prompt block (parts are "\n\n"-joined), since the
    # separate epic-reuse catalog appended later in the prompt legitimately lists
    # every top-level ticket including the current one.
    other_tickets_block = next(
        p for p in prompt2.split("\n\n") if p.strip().startswith("Tiket lain di workspace ini")
    )
    assert other["key"] in other_tickets_block
    assert ticket["key"] not in other_tickets_block


def test_pm_mention_prompt_requires_five_part_final_plan(client, tmp_path, monkeypatch):
    """Regression test: the owner-chat exploratory-plan instructions must keep

    mandating all five final-plan parts (owner request) — requirement, goal, the
    target epic, sprint breakdown, and duration estimate — so a future prose edit
    can't silently drop one.
    """
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id, "Chat thread")

    script = _write_python_binary(tmp_path / "opencode", _PM_IN_PROGRESS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "@pm-1 tolong bantu"})
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    mention_run = next(r for r in runs if r["agent_id"] == pm_id and r["trigger"] == "mention")
    _wait_for_run(client, mention_run["id"])
    detail = client.get(f"/api/runs/{mention_run['id']}").json()
    prompt = detail["events"][0]["payload"]["prompt"]

    for keyword in ("Requirement", "Goal", "Epic tujuan", "Breakdown sprint", "Estimasi durasi"):
        assert keyword in prompt, f"missing final-plan part: {keyword}"


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


def test_updates_sprint_and_duration_apply_to_target(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    # sprint_id=None: this test asserts the exact sprints list, which the "Sprint 2"
    # created by the report below must be the only entry in.
    source = _make_ticket(client, ws_id, "source", sprint_id=None)
    target = _make_ticket(client, ws_id, "target", sprint_id=None)

    entries = f"  - ticket: {target['key']}\n    sprint: Sprint 2\n    duration: 1.5\n"
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["report"]["updates"] == [
        {"ticket": target["key"], "applied": ["sprint → Sprint 2", "duration → 1.5"], "skipped": []}
    ]

    sprints = client.get(f"/api/workspaces/{ws_id}/sprints").json()
    assert len(sprints) == 1
    assert sprints[0]["name"] == "Sprint 2"

    target_detail = client.get(f"/api/tickets/{target['key']}").json()
    assert target_detail["sprint_id"] == sprints[0]["id"]
    assert target_detail["duration_estimate"] == 1.5


def test_updates_illegal_status_change_skipped_with_note(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    qa_id = _make_agent(client, ws_id, "qa", "qa-1")
    source = _make_ticket(client, ws_id, "source")
    target = _make_ticket(client, ws_id, "target")
    # Any role may now move a ticket between any two distinct known statuses (owner
    # request: the old per-role transition matrix kept producing false blocks) — the
    # only things still illegal here are an unknown status string and `release`
    # (see test_updates_cannot_set_release_on_other_ticket below).
    assert target["status"] == "backlog"

    entries = f"  - ticket: {target['key']}\n    status: not_a_real_status\n"
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": qa_id})
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


def test_updates_cannot_set_release_on_other_ticket(client, tmp_path, monkeypatch):
    # `release` is owner/PM-manual-only (docs/03-agent-design.md §3) — QA filing
    # updates: on another ticket must not be able to slip it past that gate.
    ws_id = _make_workspace(client, tmp_path)
    qa_id = _make_agent(client, ws_id, "qa", "qa-1")
    source = _make_ticket(client, ws_id, "source")
    target = _make_ticket(client, ws_id, "target")
    _set_status(client, target["key"], "todo")
    _set_status(client, target["key"], "in_progress")
    _set_status(client, target["key"], "review")
    _set_status(client, target["key"], "qa")
    _set_status(client, target["key"], "security")
    _set_status(client, target["key"], "done")

    entries = f"  - ticket: {target['key']}\n    status: release\n"
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": qa_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["report"]["updates"][0]["applied"] == []
    assert final["report"]["updates"][0]["skipped"]

    target_detail = client.get(f"/api/tickets/{target['key']}").json()
    assert target_detail["status"] == "done"


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
    qa_id = _make_agent(client, ws_id, "qa", "qa-1")
    source = _make_ticket(client, ws_id, "source")
    ok_target = _make_ticket(client, ws_id, "ok-target")
    bad_target = _make_ticket(client, ws_id, "bad-target")

    entries = (
        f"  - ticket: {ok_target['key']}\n    priority: high\n"
        f"  - ticket: {bad_target['key']}\n    status: not_a_real_status\n"
        "  - ticket: GHOST-1\n    priority: low\n"
    )
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": qa_id})
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
    # run_ended is the adapter's terminal streamed event; the orchestrator's own
    # post-report bookkeeping (the summary `comment` event, here) is appended after it.
    assert "run_ended" in types
    assert types.index("run_ended") < types.index("comment")


# ---------------------------------------------------------------------------
# Live SSE events for comments/status changes emitted from `_finish_run` — the
# `comment`/`status_change` events a toast notification is built from on the
# frontend. Verified here via GET /api/runs/{id} (same technique as
# test_get_run_includes_events above), since these events are persisted to the
# `event` table before anything else, same as every other event type.
# ---------------------------------------------------------------------------


def test_successful_report_publishes_comment_and_status_change_events(
    client, tmp_path, monkeypatch
):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    lead_id = _make_agent(client, ws_id, "lead", "lead-1")
    # disabled, same as test_valid_map_block_transitions_ticket_and_records_mentions,
    # so the mention resolves synchronously with no second subprocess/run.
    client.patch(f"/api/agents/{lead_id}", json={"enabled": False})
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _VALID_MAP_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])

    events = client.get(f"/api/runs/{run['id']}").json()["events"]

    comment_events = [e for e in events if e["type"] == "comment"]
    summary_comment = next(e for e in comment_events if not e["payload"]["is_system"])
    assert summary_comment["payload"]["ticket_key"] == ticket["key"]
    assert summary_comment["payload"]["author"] == "eng-1"
    assert "Implemented the thing" in summary_comment["payload"]["body_preview"]

    status_events = [e for e in events if e["type"] == "status_change"]
    main_transition = next(e for e in status_events if e["payload"]["to"] == "review")
    assert main_transition["payload"]["ticket_key"] == ticket["key"]
    assert main_transition["payload"]["from"] == "in_progress"
    assert main_transition["payload"]["actor"] == "eng-1"


def test_blocked_run_publishes_comment_event(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _GARBAGE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])

    events = client.get(f"/api/runs/{run['id']}").json()["events"]
    comment_events = [e for e in events if e["type"] == "comment"]
    assert comment_events, "blocked run must leave a comment event for the block reason"
    block_comment = comment_events[-1]
    assert block_comment["payload"]["is_system"] is True
    assert block_comment["payload"]["ticket_key"] == ticket["key"]
    assert "Blok ```map hilang/rusak" in block_comment["payload"]["body_preview"]


_PM_TICKETS_SCRIPT = '''
import json
text = """breaking it down

```map
status: in_progress
mention: []
summary: |
  split into one sub-ticket
tickets:
  - title: "Sub-ticket A"
    description: "do the sub-thing"
    priority: medium
```
"""
print(json.dumps({"type": "assistant_text", "text": text, "session_id": "sess-pm"}))
'''


def test_tickets_child_creation_publishes_status_change_event(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    epic = _make_ticket(client, ws_id, "Epic")
    _set_status(client, epic["key"], "todo")
    _set_status(client, epic["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _PM_TICKETS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{epic['key']}/run", json={"agent_id": pm_id}).json()
    _wait_for_run(client, run["id"])

    events = client.get(f"/api/runs/{run['id']}").json()["events"]
    creation_events = [
        e for e in events if e["type"] == "status_change" and e["payload"]["from"] is None
    ]
    assert len(creation_events) == 1
    payload = creation_events[0]["payload"]
    assert payload["to"] == "todo"
    assert payload["ticket_title"] == "Sub-ticket A"
    assert payload["ticket_key"] != epic["key"]


_PM_SPRINT_TICKETS_SCRIPT = '''
import json
text = """breaking it down with sprints

```map
status: in_progress
mention: []
summary: |
  split into sub-tickets with sprint plan
sprints:
  - name: "Sprint 1"
    goal: "ship login"
    duration: 2
tickets:
  - title: "Sub-ticket A"
    priority: medium
    sprint: "Sprint 1"
    duration: 0.5
```
"""
print(json.dumps({"type": "assistant_text", "text": text, "session_id": "sess-pm"}))
'''


def test_pm_tickets_with_sprint_creates_and_links_sprint(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    # sprint_id=None: this test asserts "Sprint 1" bootstraps active as the first
    # sprint ever created in the workspace.
    epic = _make_ticket(client, ws_id, "Epic", sprint_id=None)
    _set_status(client, epic["key"], "todo")
    _set_status(client, epic["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _PM_SPRINT_TICKETS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{epic['key']}/run", json={"agent_id": pm_id}).json()
    _wait_for_run(client, run["id"])

    sprints = client.get(f"/api/workspaces/{ws_id}/sprints").json()
    assert len(sprints) == 1
    assert sprints[0]["name"] == "Sprint 1"
    assert sprints[0]["goal"] == "ship login"
    assert sprints[0]["duration_estimate"] == 2.0
    assert sprints[0]["status"] == "active"  # bootstrapped: first sprint in workspace

    tickets = client.get(f"/api/workspaces/{ws_id}/tickets").json()
    child = next(t for t in tickets if t["title"] == "Sub-ticket A")
    assert child["sprint_id"] == sprints[0]["id"]
    assert child["duration_estimate"] == 0.5


def _tickets_with_epic_script(epic_key: str) -> str:
    return f'''
import json
text = """breaking it down

```map
status: in_progress
mention: []
summary: |
  attaching to existing epic
tickets:
  - title: "Sub-ticket targeting existing epic"
    priority: medium
    epic: "{epic_key}"
```
"""
print(json.dumps({{"type": "assistant_text", "text": text, "session_id": "sess-epic"}}))
'''


_QA_BUG_REPORT_SCRIPT = '''
import json
text = """found a bug

```map
status: in_progress
mention: []
summary: |
  filing bug found during review
tickets:
  - title: "Bug found during review"
    priority: high
```
"""
print(json.dumps({"type": "assistant_text", "text": text, "session_id": "sess-qa"}))
'''


def test_tickets_epic_field_attaches_to_existing_epic(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    existing_epic = _make_ticket(client, ws_id, "Existing Epic")
    working_ticket = _make_ticket(client, ws_id, "Some unrelated request")
    _set_status(client, working_ticket["key"], "todo")
    _set_status(client, working_ticket["key"], "in_progress")

    script = _write_python_binary(
        tmp_path / "opencode", _tickets_with_epic_script(existing_epic["key"])
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(
        f"/api/tickets/{working_ticket['key']}/run", json={"agent_id": pm_id}
    ).json()
    _wait_for_run(client, run["id"])

    tickets = client.get(f"/api/workspaces/{ws_id}/tickets").json()
    child = next(t for t in tickets if t["title"] == "Sub-ticket targeting existing epic")
    assert child["parent_id"] == existing_epic["id"]
    assert child["parent_id"] != working_ticket["id"]


def test_tickets_epic_field_unknown_key_skipped_with_note(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    working_ticket = _make_ticket(client, ws_id, "Some request")
    _set_status(client, working_ticket["key"], "todo")
    _set_status(client, working_ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _tickets_with_epic_script("NOPE-999"))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(
        f"/api/tickets/{working_ticket['key']}/run", json={"agent_id": pm_id}
    ).json()
    _wait_for_run(client, run["id"])

    tickets = client.get(f"/api/workspaces/{ws_id}/tickets").json()
    child = next(t for t in tickets if t["title"] == "Sub-ticket targeting existing epic")
    # `epic:` unresolvable -> falls back to default: working_ticket has no parent of
    # its own, so it becomes the epic itself (unchanged old behavior).
    assert child["parent_id"] == working_ticket["id"]

    detail = client.get(f"/api/tickets/{working_ticket['key']}").json()
    bodies = [c["body"] for c in detail["comments"]]
    assert any("epic tujuan diabaikan" in b and "NOPE-999" in b for b in bodies)


def test_tickets_without_epic_from_child_ticket_attaches_to_same_epic(
    client, tmp_path, monkeypatch
):
    """Regression test: a QA/Pentester bug report filed from a ticket that already

    has a parent (a story/feature under an epic) must attach as a SIBLING under
    that same epic, not a grandchild of the story — keeps the flat 1-level
    invariant that the manual API already enforces (`_validate_parent`) but the
    agent report path previously skipped.
    """
    ws_id = _make_workspace(client, tmp_path)
    qa_id = _make_agent(client, ws_id, "qa", "qa-1")
    epic = _make_ticket(client, ws_id, "Epic")
    story = _make_ticket(client, ws_id, "Story under epic", parent_id=epic["id"])
    _set_status(client, story["key"], "todo")
    _set_status(client, story["key"], "security")

    script = _write_python_binary(tmp_path / "opencode", _QA_BUG_REPORT_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{story['key']}/run", json={"agent_id": qa_id}).json()
    _wait_for_run(client, run["id"])

    tickets = client.get(f"/api/workspaces/{ws_id}/tickets").json()
    bug = next(t for t in tickets if t["title"] == "Bug found during review")
    assert bug["parent_id"] == epic["id"]
    assert bug["parent_id"] != story["id"]


def test_updates_target_change_publishes_status_change_and_comment_events(
    client, tmp_path, monkeypatch
):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    source = _make_ticket(client, ws_id, "source")
    target = _make_ticket(client, ws_id, "target")
    _set_status(client, target["key"], "todo")
    _set_status(client, target["key"], "in_progress")  # legal for pm: in_progress -> done

    entries = f"  - ticket: {target['key']}\n    status: done\n"
    script = _write_python_binary(tmp_path / "opencode", _updates_script(entries))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{source['key']}/run", json={"agent_id": pm_id}).json()
    _wait_for_run(client, run["id"])

    events = client.get(f"/api/runs/{run['id']}").json()["events"]

    target_status_events = [
        e
        for e in events
        if e["type"] == "status_change" and e["payload"]["ticket_key"] == target["key"]
    ]
    assert any(
        e["payload"]["from"] == "in_progress" and e["payload"]["to"] == "done"
        for e in target_status_events
    )

    target_comment_events = [
        e
        for e in events
        if e["type"] == "comment" and e["payload"]["ticket_key"] == target["key"]
    ]
    assert any(
        e["payload"]["is_system"] and "Diperbarui oleh pm-1" in e["payload"]["body_preview"]
        for e in target_comment_events
    )


# ---------------------------------------------------------------------------
# Explorative PM flow (Bagian B): owner chat -> plan first -> approval -> tickets[]
# ---------------------------------------------------------------------------

_PM_PLAN_SCRIPT = '''
import json
text = """here is my plan

```map
status: in_progress
mention: []
summary: |
  Ini rencananya: 1) bikin API login, 2) bikin form login, 3) test.
  Balas "oke lanjut" untuk menyetujui.
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''

_PM_APPROVED_TICKETS_SCRIPT = '''
import json
text = """approved, breaking down

```map
status: in_progress
mention: []
summary: |
  breakdown approved
tickets:
  - title: "Login API"
    assignee: "eng-1"
    category: security
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''

_PM_SNEAKY_TICKETS_SCRIPT = '''
import json
text = """trying anyway

```map
status: in_progress
mention: []
summary: |
  going ahead
tickets:
  - title: "sneaky ticket"
    assignee: "eng-1"
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def test_pm_mention_without_approval_drops_tickets_and_does_not_block(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id, "Chat with PM")

    script = _write_python_binary(tmp_path / "opencode", _PM_SNEAKY_TICKETS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "@pm-1 aku mau bikin fitur X"})
    assert resp.status_code == 201, resp.text

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    pm_run = next(r for r in runs if r["agent_id"] == pm_id and r["trigger"] == "mention")
    final = _wait_for_run(client, pm_run["id"])
    assert final["status"] == "done", final
    # tickets dropped -> no children created.
    children = client.get(f"/api/workspaces/{ws_id}/tickets", params={"parent_id": ticket["id"]}).json()
    assert children == []
    # Ticket NOT blocked (exploration continues).
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] != "blocked"


def test_pm_plan_then_approval_then_tickets(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id, "Chat: bikin fitur")

    # Run 1: owner mentions PM -> PM replies with a plan, no tickets[].
    script = _write_python_binary(tmp_path / "opencode", _PM_PLAN_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    resp = client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "@pm-1 aku ada ide bikin fitur"})
    assert resp.status_code == 201, resp.text
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    plan_run = next(r for r in runs if r["agent_id"] == pm_id and r["trigger"] == "mention")
    _wait_for_run(client, plan_run["id"])
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["approved_at"] is None
    assert detail["status"] != "blocked"

    # Run 2: owner approves -> PM runs again with tickets[].
    resp = client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "@pm-1 oke lanjut"})
    assert resp.status_code == 201, resp.text
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["approved_at"] is not None

    script2 = _write_python_binary(tmp_path / "opencode", _PM_APPROVED_TICKETS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script2)
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    exec_run = next(
        r for r in runs if r["agent_id"] == pm_id and r["trigger"] == "mention" and r["id"] != plan_run["id"]
    )
    _wait_for_run(client, exec_run["id"])
    children = client.get(f"/api/workspaces/{ws_id}/tickets", params={"parent_id": ticket["id"]}).json()
    assert len(children) == 1, children
    assert children[0]["category"] == "security"


def test_sprint_creator_roles_setting_gates_sprints_declaration(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    qa_id = _make_agent(client, ws_id, "qa", "qa-1")
    # ticket needs an active sprint for QA (not yet in sprint_creator_roles) to be
    # allowed to run on it at all — _make_ticket's default bootstraps one ("Sprint
    # 0"), which is why the assertions below compare against sprints_before rather
    # than an exact empty/one-item list.
    ticket = _make_ticket(client, ws_id)
    sprints_before = client.get(f"/api/workspaces/{ws_id}/sprints").json()

    # Default (PM-only): QA declaring sprints: gets it dropped.
    script = _write_python_binary(
        tmp_path / "opencode",
        '''
import json
text = """sprint plan

```map
status: done
mention: []
summary: |
  sprint plan
sprints:
  - name: Sprint 1
    goal: ship login
tickets:
  - title: "Sub-ticket A"
    assignee: "eng-1"
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
''',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": qa_id}).json()
    _wait_for_run(client, run["id"])
    assert client.get(f"/api/workspaces/{ws_id}/sprints").json() == sprints_before

    # Owner widens the setting to include QA -> same report now creates the sprint.
    resp = client.patch(
        f"/api/workspaces/{ws_id}",
        json={"sprint_creator_roles": ["pm", "qa"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sprint_creator_roles"] == ["pm", "qa"]

    run2 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": qa_id}).json()
    _wait_for_run(client, run2["id"])
    sprints = client.get(f"/api/workspaces/{ws_id}/sprints").json()
    assert len(sprints) == len(sprints_before) + 1
    new_sprint = next(s for s in sprints if s["id"] not in {s["id"] for s in sprints_before})
    assert new_sprint["name"] == "Sprint 1"
    assert new_sprint["goal"] == "ship login"


def test_blocked_reason_set_and_cleared(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)

    script = _write_python_binary(
        tmp_path / "opencode",
        '''
import json
text = """cant do it

```map
status: blocked
mention: []
summary: |
  API key belum tersedia, butuh akses ke server.
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
''',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"
    assert detail["blocked_reason"] is not None
    assert "API key" in detail["blocked_reason"]

    # Owner moves it out of blocked -> blocked_reason cleared.
    updated = _set_status(client, ticket["key"], "todo")
    assert updated["blocked_reason"] is None


def test_workflow_prompt_injected_into_prompt(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    _make_agent(client, ws_id, "lead", "lead-1")
    ticket = _make_ticket(client, ws_id)

    resp = client.patch(
        f"/api/workspaces/{ws_id}",
        json={"workflow_prompt": "PM selalu minta QA double-check sebelum merge."},
    )
    assert resp.status_code == 200, resp.text

    script = _write_python_binary(
        tmp_path / "opencode",
        'import json\nprint(json.dumps({"type": "assistant_text", "text": "```map\\nstatus: review\\nmention: [lead-1]\\nsummary: |\\n  done\\n```"}))\n',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])
    detail = client.get(f"/api/runs/{run['id']}").json()
    prompt = detail["events"][0]["payload"]["prompt"]
    assert "PM selalu minta QA double-check sebelum merge." in prompt


def test_workspace_description_injected_into_prompt(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    _make_agent(client, ws_id, "lead", "lead-1")
    ticket = _make_ticket(client, ws_id)

    resp = client.patch(
        f"/api/workspaces/{ws_id}",
        json={"description": "Internal billing platform for Acme Corp."},
    )
    assert resp.status_code == 200, resp.text

    script = _write_python_binary(
        tmp_path / "opencode",
        'import json\nprint(json.dumps({"type": "assistant_text", "text": "```map\\nstatus: review\\nmention: [lead-1]\\nsummary: |\\n  done\\n```"}))\n',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])
    detail = client.get(f"/api/runs/{run['id']}").json()
    prompt = detail["events"][0]["payload"]["prompt"]
    assert "Internal billing platform for Acme Corp." in prompt


# ---------------------------------------------------------------------------
# Artifact catalog in prompt + PM artifact_updates: organizing the Artifacts menu
# ---------------------------------------------------------------------------

_ARTIFACT_PUBLISH_SCRIPT = '''
import json
text = """published

```map
status: done
mention: []
summary: |
  published artifacts
artifacts:
  - path: docs/PRD.md
    group: Dokumen Teknis
    description: initial PRD
  - path: docs/TSD.md
    group: Dokumen Teknis
  - path: docs/evidence.md
    group: Hasil Testing
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''

_SIMPLE_DONE_SCRIPT = '''
import json
text = """done

```map
status: done
mention: []
summary: |
  done
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''

_PM_ARTIFACT_UPDATES_SCRIPT = '''
import json
text = """organizing

```map
status: done
mention: []
summary: |
  organized artifacts
artifact_updates:
  - op: rename
    group: Dokumen Teknis
    to: Docs
  - op: merge
    from: Hasil Testing
    into: QA Reports
  - op: move
    group: Docs
    file: PRD.md
    to: QA Reports
  - op: delete
    group: Kelompok Kosong
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''

_PM_ARTIFACT_UPDATES_ERRORS_SCRIPT = '''
import json
text = """organizing

```map
status: done
mention: []
summary: |
  organized artifacts
artifact_updates:
  - op: delete
    group: Dokumen Teknis
  - op: rename
    group: Tidak Ada
    to: X
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
'''


def _publish_three_artifacts(client, tmp_path, monkeypatch, ticket_key, agent_id):
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "PRD.md").write_text("# PRD")
    (tmp_path / "docs" / "TSD.md").write_text("# TSD")
    (tmp_path / "docs" / "evidence.md").write_text("# evidence")
    script = _write_python_binary(tmp_path / "opencode", _ARTIFACT_PUBLISH_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run = client.post(f"/api/tickets/{ticket_key}/run", json={"agent_id": agent_id}).json()
    _wait_for_run(client, run["id"])


def test_artifact_catalog_included_in_prompt(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _publish_three_artifacts(client, tmp_path, monkeypatch, ticket["key"], eng_id)

    script = _write_python_binary(tmp_path / "opencode", _SIMPLE_DONE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run2 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run2["id"])

    detail = client.get(f"/api/runs/{run2['id']}").json()
    prompt = detail["events"][0]["payload"]["prompt"]
    assert "Artifacts di workspace ini (menu Artifacts)" in prompt
    assert "[Dokumen Teknis] PRD.md" in prompt
    assert "initial PRD" in prompt
    assert "[Hasil Testing] evidence.md" in prompt
    assert ticket["key"] in prompt


def test_pm_artifact_updates_organize_groups(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)
    _publish_three_artifacts(client, tmp_path, monkeypatch, ticket["key"], eng_id)

    groups = client.get(f"/api/workspaces/{ws_id}/artifacts").json()
    assert {g["name"] for g in groups} == {"Dokumen Teknis", "Hasil Testing"}

    script = _write_python_binary(tmp_path / "opencode", _PM_ARTIFACT_UPDATES_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id}).json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final

    groups = client.get(f"/api/workspaces/{ws_id}/artifacts").json()
    by_name = {g["name"]: g for g in groups}
    assert set(by_name) == {"Docs", "QA Reports"}
    assert {a["filename"] for a in by_name["Docs"]["attachments"]} == {"TSD.md"}
    assert {a["filename"] for a in by_name["QA Reports"]["attachments"]} == {"evidence.md", "PRD.md"}

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    system_bodies = [c["body"] for c in detail["comments"] if c["is_system"]]
    assert any("Artifact diorganisir" in b for b in system_bodies)
    assert any("Kelompok Kosong" in b and "diabaikan" in b for b in system_bodies)


def test_artifact_updates_errors_skipped_with_notes(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)
    _publish_three_artifacts(client, tmp_path, monkeypatch, ticket["key"], eng_id)

    script = _write_python_binary(tmp_path / "opencode", _PM_ARTIFACT_UPDATES_ERRORS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id}).json()
    _wait_for_run(client, run["id"])

    # delete of a non-empty group rejected; rename of unknown group skipped -> unchanged
    groups = client.get(f"/api/workspaces/{ws_id}/artifacts").json()
    assert {g["name"] for g in groups} == {"Dokumen Teknis", "Hasil Testing"}

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    system_bodies = [c["body"] for c in detail["comments"] if c["is_system"]]
    diag = next(b for b in system_bodies if "artifact_updates diabaikan" in b)
    assert "masih berisi" in diag
    assert "Tidak Ada" in diag


# ---------------------------------------------------------------------------
# Owner chat notifications (System messages on the epic chat)
# ---------------------------------------------------------------------------


def test_pm_tickets_from_child_mirrors_system_message_to_epic_chat(
    client, tmp_path, monkeypatch
):
    """PM filed tickets[] while working a CHILD ticket (e.g. QA bug report fan-out):
    the owner's chat lives on the epic, so a System message must land there listing
    the new sub-tickets — otherwise the PM "answers" by filing tickets but says
    nothing in the conversation the owner is actually watching."""
    ws_id = _make_workspace(client, tmp_path)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    _make_agent(client, ws_id, "engineer", "eng-1")
    epic = _make_ticket(client, ws_id, "Epic")
    story = _make_ticket(client, ws_id, "Story", parent_id=epic["id"])
    _set_status(client, story["key"], "todo")
    _set_status(client, story["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _PM_TICKETS_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run = client.post(f"/api/tickets/{story['key']}/run", json={"agent_id": pm_id}).json()
    _wait_for_run(client, run["id"])

    epic_detail = client.get(f"/api/tickets/{epic['key']}").json()
    system_bodies = [c["body"] for c in epic_detail["comments"] if c["is_system"]]
    assert any("memecah" in b and story["key"] in b for b in system_bodies), system_bodies

    children = client.get(
        f"/api/workspaces/{ws_id}/tickets", params={"parent_id": epic["id"]}
    ).json()
    child_keys = {c["key"] for c in children if c["id"] != story["id"]}
    assert child_keys, "expected the PM report to have created a child ticket"
    # the chat message must name the newly created tickets so the owner can click through
    breakdown = next(b for b in system_bodies if "memecah" in b)
    assert any(k in breakdown for k in child_keys), (breakdown, child_keys)


def test_blocked_child_mirrors_system_notice_with_reason_to_epic_chat(
    client, tmp_path, monkeypatch
):
    """A blocked/failed child must reach the owner's epic chat with the block reason,
    so the owner is told what happened instead of discovering it by opening the child."""
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    epic = _make_ticket(client, ws_id, "Epic")
    child = _make_ticket(client, ws_id, "Child", parent_id=epic["id"])
    _set_status(client, child["key"], "todo")
    _set_status(client, child["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _GARBAGE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{child['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])

    detail = client.get(f"/api/tickets/{child['key']}").json()
    assert detail["status"] == "blocked"

    epic_detail = client.get(f"/api/tickets/{epic['key']}").json()
    system_bodies = [c["body"] for c in epic_detail["comments"] if c["is_system"]]
    notice = next(
        (b for b in system_bodies if "menandai" in b and child["key"] in b), None
    )
    assert notice is not None, system_bodies
    assert "blocked" in notice
    assert "hilang/rusak" in notice, "the reason excerpt must be included"


def test_failed_child_mirrors_system_notice_to_epic_chat(client, tmp_path, monkeypatch):
    """Nonzero-exit (opencode error) on a child -> failed -> blocked: the epic chat
    must get the System notice with the stderr excerpt."""
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    epic = _make_ticket(client, ws_id, "Epic")
    child = _make_ticket(client, ws_id, "Child", parent_id=epic["id"])
    _set_status(client, child["key"], "todo")
    _set_status(client, child["key"], "in_progress")

    script = _write_script(
        tmp_path / "opencode",
        r""">&2 printf 'opencode crashed: segfault in tool runner\n'
exit 1""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{child['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])

    epic_detail = client.get(f"/api/tickets/{epic['key']}").json()
    system_bodies = [c["body"] for c in epic_detail["comments"] if c["is_system"]]
    notice = next(
        (b for b in system_bodies if "menandai" in b and child["key"] in b), None
    )
    assert notice is not None, system_bodies
    assert "segfault" in notice


def test_top_level_blocked_no_duplicate_epic_notice(client, tmp_path, monkeypatch):
    """Blocking a top-level ticket must NOT mirror an extra comment onto itself —
    its own system comment IS the chat message (no duplication)."""
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _GARBAGE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run["id"])

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"
    system_bodies = [c["body"] for c in detail["comments"] if c["is_system"]]
    block_bodies = [b for b in system_bodies if "hilang/rusak" in b]
    assert len(block_bodies) == 1, system_bodies
