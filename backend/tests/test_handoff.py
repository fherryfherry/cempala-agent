"""Tests for MAP-029: handoff engine.

Same fake-binary + real-HTTP-API technique as test_orchestrator.py / test_loop_detector.py.
A report's `mention` now auto-schedules a follow-up run (`trigger="handoff"`,
`parent_run_id` set) instead of just being recorded. Covers: name resolution, role
fallback (with idle/fewest-runs preference), disabled-agent handling, unknown mentions,
`handoff_depth` bookkeeping, and owner-comment-driven `trigger="mention"` runs.
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


def _make_agent(client, ws_id, role, name, model="opencode/big-pickle", enabled=True):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": model, "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    if not enabled:
        resp = client.patch(f"/api/agents/{agent['id']}", json={"enabled": False})
        assert resp.status_code == 200, resp.text
        agent = resp.json()
    return agent


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
            "description": "d",
            "is_new_epic": True,
            "sprint_id": _active_sprint_id(client, ws_id),
        },
    )
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


def _wait_for_ticket_status(client, key, statuses, timeout=15.0):
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        body = client.get(f"/api/tickets/{key}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.03)
    raise TimeoutError(f"ticket {key} did not reach {statuses} within {timeout}s: {body}")


def _wait_for_run_count(client, ws_id, ticket_id, count, timeout=15.0):
    deadline = time.time() + timeout
    runs = []
    while time.time() < deadline:
        runs = [
            r
            for r in client.get(f"/api/workspaces/{ws_id}/runs").json()
            if r["ticket_id"] == ticket_id
        ]
        if len(runs) >= count and all(r["status"] not in ("queued", "running") for r in runs):
            return runs
        time.sleep(0.03)
    raise TimeoutError(f"did not reach {count} finished runs within {timeout}s: {runs}")


def _system_comment_bodies(client, key):
    detail = client.get(f"/api/tickets/{key}").json()
    return [c["body"] for c in detail["comments"] if c["is_system"]]


def _script(status, mention):
    """A fake binary that always reports the same status/mention, regardless of which
    agent runs it. Only safe to use for the FIRST hop of a chain (or single-shot runs)
    -- if the mentioned agent would also run this exact binary, `status` must be legal
    for every role expected to execute it (see `_script_by_model` for branching).
    """
    return f'''
import json
text = """ok

```map
status: {status}
mention: [{mention}]
summary: |
  done
```
"""
print(json.dumps({{"type": "assistant_text", "text": text}}))
'''


def _script_by_model(mapping):
    """A fake binary that branches its ```map report by the `-m <model>` CLI arg, so a
    chain of different agents (sharing one OPENCODE_BIN) can each report differently.
    `mapping`: {{model: (status, mention)}}.
    """
    table = "{" + ", ".join(f'"{m}": ("{s}", "{mn}")' for m, (s, mn) in mapping.items()) + "}"
    return f'''
import json, sys
model = sys.argv[sys.argv.index("-m") + 1]
status, mention = {table}[model]
text = f"""ok

```map
status: {{status}}
mention: [{{mention}}]
summary: |
  done
```
"""
print(json.dumps({{"type": "assistant_text", "text": text}}))
'''


# ---------------------------------------------------------------------------
# (a) one valid mention -> automatic follow-up run
# ---------------------------------------------------------------------------


def test_valid_mention_schedules_followup_run(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws_id, "engineer", "eng-1", model="opencode/eng")
    _make_agent(client, ws_id, "lead", "lead-1", model="opencode/lead")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    # eng reports review+mention lead-1 (auto handoff); lead reports qa+mention [] so
    # the chain stops there (a legal terminal report for lead, no further hop).
    script = _write_python_binary(
        tmp_path / "opencode",
        _script_by_model({"opencode/eng": ("review", "lead-1"), "opencode/lead": ("qa", "")}),
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng["id"]})
    assert resp.status_code == 201, resp.text
    first = _wait_for_run(client, resp.json()["id"])
    assert first["status"] == "done", first
    assert first["trigger"] == "manual"

    runs = _wait_for_run_count(client, ws_id, ticket["id"], 2)
    followup = next(r for r in runs if r["id"] != first["id"])
    assert followup["trigger"] == "handoff"
    assert followup["parent_run_id"] == first["id"]
    assert followup["status"] == "done"

    ticket_detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert ticket_detail["handoff_depth"] == 1


# ---------------------------------------------------------------------------
# (b) chain of handoffs increases handoff_depth each time, stopped by max_handoff_depth
# ---------------------------------------------------------------------------


_PINGPONG_SCRIPT = '''
import json, sys
model = sys.argv[sys.argv.index("-m") + 1]
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


def test_handoff_chain_increments_depth_and_stops_at_max_handoff_depth(
    client, tmp_path, monkeypatch
):
    ws_id = _make_workspace(client, tmp_path, guardrails={"max_handoff_depth": 2})
    eng = _make_agent(client, ws_id, "engineer", "eng-1", model="opencode/eng")
    _make_agent(client, ws_id, "lead", "lead-1", model="opencode/lead")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _PINGPONG_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng["id"]})
    assert resp.status_code == 201, resp.text

    # eng->lead is handoff #1 (depth 0->1, run2 created). lead's own report then mentions
    # eng again -- that's handoff #2 (depth 1->2), and *that* scheduling attempt is the
    # one that hits max_handoff_depth=2 and gets blocked before a 3rd Run row ever
    # exists. No infinite/real process runaway: the chain is bounded by the guardrail
    # regardless of how many mentions fire.
    detail = _wait_for_ticket_status(client, ticket["key"], {"blocked"})
    assert detail["status"] == "blocked"
    assert detail["handoff_depth"] == 2

    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("max_handoff_depth" in b for b in bodies)

    # Exactly 2 runs: eng (manual), lead (handoff #1) -- the 2nd handoff attempt (back
    # to eng) never became a Run row at all.
    runs = [
        r
        for r in client.get(f"/api/workspaces/{ws_id}/runs").json()
        if r["ticket_id"] == ticket["id"]
    ]
    assert len(runs) == 2, runs
    assert all(r["status"] == "done" for r in runs)


def test_unblocking_resets_handoff_depth(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path, guardrails={"max_handoff_depth": 2})
    eng = _make_agent(client, ws_id, "engineer", "eng-1", model="opencode/eng")
    _make_agent(client, ws_id, "lead", "lead-1", model="opencode/lead")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _PINGPONG_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng["id"]})
    assert resp.status_code == 201, resp.text
    _wait_for_ticket_status(client, ticket["key"], {"blocked"})

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["handoff_depth"] == 2

    # Owner unblocks -- handoff_depth resets to 0, so this exact agent pair can make
    # forward progress again instead of being permanently stuck (mirrors loop_reset_at
    # for the loop detector, MAP-028's twin guardrail).
    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "in_progress"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["handoff_depth"] == 0

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng["id"]})
    assert resp.status_code == 201, resp.text
    detail = _wait_for_ticket_status(client, ticket["key"], {"blocked"})
    # Blocked again at the same limit -- not immediately re-blocked from residual depth.
    assert detail["handoff_depth"] == 2

    runs = [
        r
        for r in client.get(f"/api/workspaces/{ws_id}/runs").json()
        if r["ticket_id"] == ticket["id"]
    ]
    assert len(runs) == 4, runs  # 2 runs before the reset, 2 more after


# ---------------------------------------------------------------------------
# (c) role-name mention resolves to an idle agent with that role
# ---------------------------------------------------------------------------


def test_role_mention_resolves_to_idle_agent_with_that_role(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws_id, "engineer", "eng-1")
    _make_agent(client, ws_id, "lead", "lead-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    # Mention the ROLE "lead", not the agent name "lead-1".
    script = _write_python_binary(tmp_path / "opencode", _script("review", "lead"))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng["id"]})
    assert resp.status_code == 201, resp.text
    first = _wait_for_run(client, resp.json()["id"])
    assert first["status"] == "done", first

    runs = _wait_for_run_count(client, ws_id, ticket["id"], 2)
    followup = next(r for r in runs if r["id"] != first["id"])
    assert followup["trigger"] == "handoff"
    agents = client.get(f"/api/workspaces/{ws_id}/agents").json()
    followup_agent = next(a for a in agents if a["id"] == followup["agent_id"])
    assert followup_agent["name"] == "lead-1"
    assert followup_agent["role"] == "lead"


# ---------------------------------------------------------------------------
# (d) role mention with multiple candidates prefers the one with fewer runs on ticket
# ---------------------------------------------------------------------------


def test_role_mention_prefers_agent_with_fewer_runs_on_ticket(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pm = _make_agent(client, ws_id, "pm", "pm-1")
    lead_a = _make_agent(client, ws_id, "lead", "lead-a")
    lead_b = _make_agent(client, ws_id, "lead", "lead-b")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    # Give lead-a a prior (finished) run on this ticket so lead-b has fewer.
    script_lead_only = _write_python_binary(
        tmp_path / "opencode",
        '''
import json
text = """ok

```map
status: blocked
mention: []
summary: |
  placeholder
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
''',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script_lead_only)
    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": lead_a["id"]})
    assert resp.status_code == 201, resp.text
    _wait_for_run(client, resp.json()["id"])
    # lead's blocked report leaves the ticket blocked; reopen it (owner-only transitions)
    # and put it back in_progress so PM has a legal target status to declare (PM may
    # only move in_progress -> done per the state machine's transition table).
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    # Now PM mentions role "lead" -- lead-b (0 runs on ticket) should be preferred over
    # lead-a (1 prior run), both idle. Status "review" (not "done"/"release"): a
    # completion-status report's mentions are informational only and don't schedule
    # a follow-up (see test_mention_on_completed_ticket_does_not_schedule_followup_run).
    script = _write_python_binary(tmp_path / "opencode", _script("review", "lead"))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm["id"]})
    assert resp.status_code == 201, resp.text
    first = _wait_for_run(client, resp.json()["id"])
    assert first["status"] == "done", first

    runs = _wait_for_run_count(client, ws_id, ticket["id"], 3)
    followup = next(r for r in runs if r["id"] != first["id"] and r["agent_id"] != lead_a["id"])
    assert followup["agent_id"] == lead_b["id"]


# ---------------------------------------------------------------------------
# (e) unknown mention name/role, resulting status non-final -> ticket blocked
# ---------------------------------------------------------------------------


def test_unknown_mention_with_nonfinal_status_blocks_ticket(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _script("review", "nobody-such"))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng["id"]})
    assert resp.status_code == 201, resp.text
    run = _wait_for_run(client, resp.json()["id"])
    assert run["status"] == "done", run  # the report itself parsed fine

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"
    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("nobody-such" in b for b in bodies)


# ---------------------------------------------------------------------------
# (f) mention to a disabled agent -> blocked with "agent X nonaktif"
# ---------------------------------------------------------------------------


def test_mention_to_disabled_agent_blocks_with_comment(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws_id, "engineer", "eng-1")
    _make_agent(client, ws_id, "lead", "lead-1", enabled=False)
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    script = _write_python_binary(tmp_path / "opencode", _script("review", "lead-1"))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng["id"]})
    assert resp.status_code == 201, resp.text
    _wait_for_run(client, resp.json()["id"])

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"
    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("lead-1" in b and "nonaktif" in b for b in bodies)


# ---------------------------------------------------------------------------
# (g) owner comment @mention triggers trigger="mention" run, no handoff_depth change
# ---------------------------------------------------------------------------


def test_owner_comment_mention_triggers_mention_run_without_handoff_depth(
    client, tmp_path, monkeypatch
):
    ws_id = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(
        tmp_path / "opencode",
        '''
import json
text = """ok

```map
status: blocked
mention: []
summary: |
  noted
```
"""
print(json.dumps({"type": "assistant_text", "text": text}))
''',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(
        f"/api/tickets/{ticket['key']}/comments",
        json={"body": f"hey @{eng['name']} please look", "author_agent_id": None},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["mentions"] == [eng["name"]]

    runs = _wait_for_run_count(client, ws_id, ticket["id"], 1)
    assert runs[0]["trigger"] == "mention"
    assert runs[0]["parent_run_id"] is None

    ticket_detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert ticket_detail["handoff_depth"] == 0


# ---------------------------------------------------------------------------
# (h) self-mention in a report schedules nothing extra
# ---------------------------------------------------------------------------


def test_self_mention_schedules_nothing_extra(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    pentester = _make_agent(client, ws_id, "pentester", "pentester-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")
    _set_status(client, ticket["key"], "review")
    _set_status(client, ticket["key"], "qa")
    _set_status(client, ticket["key"], "security")

    script = _write_python_binary(
        tmp_path / "opencode", _script("done", "pentester-1")
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pentester["id"]})
    assert resp.status_code == 201, resp.text
    run = _wait_for_run(client, resp.json()["id"])
    assert run["status"] == "done", run
    assert run["report"]["mention"] == []  # self-mention filtered by report.py

    # Give any (incorrect) auto-scheduling a moment to show up, then confirm it didn't.
    time.sleep(0.3)
    runs = [
        r
        for r in client.get(f"/api/workspaces/{ws_id}/runs").json()
        if r["ticket_id"] == ticket["id"]
    ]
    assert len(runs) == 1, runs

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "done"  # final status, so no auto-block either


# ---------------------------------------------------------------------------
# (i) mention on an already-completed ticket is informational, not a new handoff
# ---------------------------------------------------------------------------


def test_mention_on_completed_ticket_does_not_schedule_followup_run(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws_id, "engineer", "eng-1")
    _make_agent(client, ws_id, "lead", "lead-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")
    _set_status(client, ticket["key"], "review")
    _set_status(client, ticket["key"], "qa")
    _set_status(client, ticket["key"], "security")

    # eng closes the ticket as done but still (informationally) mentions lead-1 —
    # this must NOT schedule a new run for lead-1 (the TRX-001 incident: agents kept
    # re-confirming an already-done ticket to each other forever).
    script = _write_python_binary(tmp_path / "opencode", _script("done", "lead-1"))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng["id"]})
    assert resp.status_code == 201, resp.text
    run = _wait_for_run(client, resp.json()["id"])
    assert run["status"] == "done", run

    time.sleep(0.3)
    runs = [
        r
        for r in client.get(f"/api/workspaces/{ws_id}/runs").json()
        if r["ticket_id"] == ticket["id"]
    ]
    assert len(runs) == 1, runs

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "done"
    assert detail["handoff_depth"] == 0
    bodies = _system_comment_bodies(client, ticket["key"])
    assert any("lead-1" in b and "info" in b for b in bodies), bodies


def test_blocked_report_with_mention_still_schedules_handoff(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws_id, "engineer", "eng-1", model="opencode/eng")
    _make_agent(client, ws_id, "lead", "lead-1", model="opencode/lead")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")
    _set_status(client, ticket["key"], "in_progress")

    # eng reports itself stuck ("blocked") but mentions lead-1 to ask for a decision —
    # unlike "done"/"release", "blocked" + a mention is real forward momentum and must
    # still schedule the follow-up run.
    script = _write_python_binary(
        tmp_path / "opencode",
        _script_by_model({"opencode/eng": ("blocked", "lead-1"), "opencode/lead": ("blocked", "")}),
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng["id"]})
    assert resp.status_code == 201, resp.text
    first = _wait_for_run(client, resp.json()["id"])
    assert first["status"] == "done", first

    runs = _wait_for_run_count(client, ws_id, ticket["id"], 2)
    followup = next(r for r in runs if r["id"] != first["id"])
    assert followup["trigger"] == "handoff"
    assert followup["parent_run_id"] == first["id"]

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["handoff_depth"] == 1
