"""Tests for MAP-030: full autonomous flow.

Builds on MAP-029's handoff engine (app/core/orchestrator.py's `_handoff`) to cover what
it didn't: `tickets[]` from a report auto-scheduling a run for each assignee
(`trigger="auto"`), the workspace's PM getting re-invoked once every child of an epic has
reached a terminal status so it can close the epic, and session continuation
(`-s <session_id>`) actually holding up end to end when an agent returns to a ticket it
touched before via handoff.

Same fake-binary + real-HTTP-API technique as test_handoff.py / test_orchestrator.py, but
the capstone test needs one binary that behaves differently per *role* across many
chained runs, so `_ROLE_SCRIPT` below branches on the `-m <model>` argument (one model
per agent) and, for the PM specifically, on whether `-s` was passed (first kickoff run
vs. the later "close the epic" run on the very same ticket+agent).

Every wait helper here deadline-polls (no fixed sleeps) and raises on timeout, per this
project's prior flakiness/hangs with real subprocess-spawning tests.
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


# ---------------------------------------------------------------------------
# shared helpers (deliberately duplicated from test_handoff.py / test_orchestrator.py,
# same convention those files already use)
# ---------------------------------------------------------------------------


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


def _make_agent(client, ws_id, role, name, model=None):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": model or f"opencode/{name}", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_ticket(client, ws_id, title="Epic"):
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json={"title": title, "description": "d"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_status(client, key, status):
    resp = client.patch(f"/api/tickets/{key}", json={"status": status})
    assert resp.status_code == 200, resp.text


def _wait_for_ticket_status(client, key, statuses, timeout=30.0):
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        body = client.get(f"/api/tickets/{key}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.03)
    raise TimeoutError(f"ticket {key} did not reach {statuses} within {timeout}s: {body}")


def _wait_for_workspace_runs_settled(client, ws_id, min_count, timeout=30.0):
    """Poll until at least `min_count` runs exist for the workspace and none are
    queued/running. Returns the final run list. Raises on timeout instead of hanging
    forever if a chain stalls or deadlocks."""
    deadline = time.time() + timeout
    runs = []
    while time.time() < deadline:
        runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
        if len(runs) >= min_count and all(r["status"] not in ("queued", "running") for r in runs):
            return runs
        time.sleep(0.05)
    raise TimeoutError(
        f"runs for workspace {ws_id} did not settle at >= {min_count} within {timeout}s: {runs}"
    )


def _run_detail(client, run_id):
    resp = client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# capstone: one epic, kicked off once, reaches `done` with zero further manual
# /run calls -- PM's tickets[] auto-schedules two children, each flows engineer ->
# lead -> qa -> pentester -> done, and PM gets auto re-invoked to close the epic.
# ---------------------------------------------------------------------------

_CAPSTONE_SCRIPT = '''
import json, sys

argv = sys.argv
model = argv[argv.index("-m") + 1]
has_session = "-s" in argv


def emit(status, mention, summary, session_id, tickets=None):
    tickets_yaml = ""
    if tickets:
        rows = ["tickets:"]
        for t in tickets:
            rows.append(f"  - title: \\"{t[0]}\\"")
            rows.append(f"    assignee: \\"{t[1]}\\"")
        tickets_yaml = "\\n".join(rows) + "\\n"
    text = f"""ok

```map
status: {status}
mention: [{mention}]
{tickets_yaml}summary: |
  {summary}
```
"""
    print(json.dumps({"type": "assistant_text", "text": text, "session_id": session_id}))


if model == "opencode/pm":
    if has_session:
        emit("done", "", "all children done, closing epic", "sess-pm-close")
    else:
        emit(
            "in_progress",
            "",
            "broken into 2 sub-tickets",
            "sess-pm-kickoff",
            tickets=[("child A", "eng-1"), ("child B", "eng-1")],
        )
elif model == "opencode/eng":
    emit("review", "lead-1", "implemented", "sess-eng")
elif model == "opencode/lead":
    emit("qa", "qa-1", "approved", "sess-lead")
elif model == "opencode/qa":
    emit("security", "pentester-1", "tests pass", "sess-qa")
elif model == "opencode/pentester":
    emit("done", "", "audit clean", "sess-pentester")
else:
    raise SystemExit(f"unexpected model {model!r}")
'''


def test_epic_runs_to_done_with_no_manual_intervention_after_kickoff(
    client, tmp_path, monkeypatch
):
    ws_id = _make_workspace(client, tmp_path)
    pm = _make_agent(client, ws_id, "pm", "pm-1", model="opencode/pm")
    _make_agent(client, ws_id, "engineer", "eng-1", model="opencode/eng")
    _make_agent(client, ws_id, "lead", "lead-1", model="opencode/lead")
    _make_agent(client, ws_id, "qa", "qa-1", model="opencode/qa")
    _make_agent(client, ws_id, "pentester", "pentester-1", model="opencode/pentester")

    epic = _make_ticket(client, ws_id, "Bikin halaman login")
    _set_status(client, epic["key"], "todo")

    script = _write_python_binary(tmp_path / "opencode", _CAPSTONE_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    # The ONE manual action: kick off PM on the epic. Everything else must be autonomous.
    resp = client.post(f"/api/tickets/{epic['key']}/run", json={"agent_id": pm["id"]})
    assert resp.status_code == 201, resp.text

    # 1 PM kickoff + 2 children x (eng, lead, qa, pentester) + 1 PM close = 10 runs.
    runs = _wait_for_workspace_runs_settled(client, ws_id, 10, timeout=40.0)
    assert len(runs) == 10, runs
    assert all(r["status"] == "done" for r in runs), [r for r in runs if r["status"] != "done"]

    detail = _wait_for_ticket_status(client, epic["key"], {"done", "blocked"}, timeout=10.0)
    assert detail["status"] == "done", detail

    children = client.get(f"/api/workspaces/{ws_id}/tickets", params={"parent_id": epic["id"]}).json()
    assert len(children) == 2, children
    assert all(c["status"] == "done" for c in children), children

    # trigger bookkeeping: exactly one PM run is "manual" (the kickoff); the PM's
    # closing run and every child run must be "auto" or "handoff", never another
    # manual call.
    triggers = [r["trigger"] for r in runs]
    assert triggers.count("manual") == 1, triggers
    assert set(triggers) <= {"manual", "auto", "handoff"}

    pm_runs = [r for r in runs if r["agent_id"] == pm["id"]]
    assert len(pm_runs) == 2
    assert {r["trigger"] for r in pm_runs} == {"manual", "auto"}

    # No ticket left non-final with nothing scheduled/running for it (epic + both
    # children are all "done" already, and we've already asserted every Run settled).
    all_tickets = [epic] + children
    for t in all_tickets:
        refreshed = client.get(f"/api/tickets/{t['key']}").json()
        assert refreshed["status"] == "done", refreshed


def test_capstone_flow_is_stable_across_repeated_runs(client, tmp_path, monkeypatch):
    """Same scenario as above, run twice more in fresh workspaces within one test to
    catch timing-dependent flakiness (real subprocess spawns, chained async handoffs)
    without needing pytest-repeat as a new dependency."""
    for i in range(2):
        ws_id = _make_workspace(client, tmp_path, key=f"MAP{chr(65 + i)}")
        pm = _make_agent(client, ws_id, "pm", "pm-1", model="opencode/pm")
        _make_agent(client, ws_id, "engineer", "eng-1", model="opencode/eng")
        _make_agent(client, ws_id, "lead", "lead-1", model="opencode/lead")
        _make_agent(client, ws_id, "qa", "qa-1", model="opencode/qa")
        _make_agent(client, ws_id, "pentester", "pentester-1", model="opencode/pentester")

        epic = _make_ticket(client, ws_id, "Bikin halaman login")
        _set_status(client, epic["key"], "todo")

        script = _write_python_binary(tmp_path / f"opencode{i}", _CAPSTONE_SCRIPT)
        monkeypatch.setattr(settings, "OPENCODE_BIN", script)

        resp = client.post(f"/api/tickets/{epic['key']}/run", json={"agent_id": pm["id"]})
        assert resp.status_code == 201, resp.text

        runs = _wait_for_workspace_runs_settled(client, ws_id, 10, timeout=40.0)
        assert len(runs) == 10, runs
        assert all(r["status"] == "done" for r in runs)

        detail = _wait_for_ticket_status(client, epic["key"], {"done", "blocked"}, timeout=10.0)
        assert detail["status"] == "done", detail


# ---------------------------------------------------------------------------
# session continuation: an engineer sent back to the SAME ticket via handoff (after
# being reached the first time through MAP-030's tickets[] auto-schedule) resumes its
# prior opencode session (`-s <session_id>`), verified via `run.session_id`.
# ---------------------------------------------------------------------------

_SESSION_SCRIPT = '''
import json, sys

argv = sys.argv
model = argv[argv.index("-m") + 1]
s_idx = argv.index("-s") if "-s" in argv else None
s_val = argv[s_idx + 1] if s_idx is not None else None


def emit(status, mention, summary, session_id, tickets=None):
    tickets_yaml = ""
    if tickets:
        rows = ["tickets:"]
        for t in tickets:
            rows.append(f"  - title: \\"{t[0]}\\"")
            rows.append(f"    assignee: \\"{t[1]}\\"")
        tickets_yaml = "\\n".join(rows) + "\\n"
    text = f"""ok

```map
status: {status}
mention: [{mention}]
{tickets_yaml}summary: |
  {summary}
```
"""
    print(json.dumps({"type": "assistant_text", "text": text, "session_id": session_id}))


if model == "opencode/pm":
    if s_val:
        # Second invocation: woken up because the child ended up blocked. A
        # well-behaved PM (docs/03-agent-design.md §4: "Kalau ada sub-tiket yang
        # blocked: status: blocked") closes the epic as blocked instead of filing
        # more tickets[] -- doesn't loop.
        emit("blocked", "", "child ticket ended up blocked", "sess-pm-2")
    else:
        emit("in_progress", "", "one sub-ticket", "sess-pm", tickets=[("child", "eng-1")])
elif model == "opencode/lead":
    # Always rejects, sending the child back to the engineer -- second hop.
    emit("in_progress", "eng-1", "fix the validation bug", "sess-lead")
elif model == "opencode/eng":
    if s_val:
        emit("review", "", f"resumed:{s_val}", "sess-eng-second")
    else:
        emit("review", "lead-1", "first pass", "sess-eng-first")
else:
    raise SystemExit(f"unexpected model {model!r}")
'''


def test_engineer_resumes_opencode_session_on_return_via_auto_and_handoff(
    client, tmp_path, monkeypatch
):
    ws_id = _make_workspace(client, tmp_path)
    pm = _make_agent(client, ws_id, "pm", "pm-1", model="opencode/pm")
    _make_agent(client, ws_id, "engineer", "eng-1", model="opencode/eng")
    _make_agent(client, ws_id, "lead", "lead-1", model="opencode/lead")

    epic = _make_ticket(client, ws_id, "Epic with one child")
    _set_status(client, epic["key"], "todo")

    script = _write_python_binary(tmp_path / "opencode", _SESSION_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{epic['key']}/run", json={"agent_id": pm["id"]})
    assert resp.status_code == 201, resp.text

    # PM kickoff (1) + child: eng first pass (2) + lead reject (3) + eng resumed (4)
    # + PM woken up again once the child ends up blocked, closing the epic (5).
    runs = _wait_for_workspace_runs_settled(client, ws_id, 5, timeout=30.0)
    assert len(runs) == 5, runs
    assert all(r["status"] == "done" for r in runs), runs

    detail = _wait_for_ticket_status(client, epic["key"], {"done", "blocked"}, timeout=10.0)
    assert detail["status"] == "blocked", detail

    children = client.get(f"/api/workspaces/{ws_id}/tickets", params={"parent_id": epic["id"]}).json()
    assert len(children) == 1, children
    child = children[0]
    assert child["status"] == "blocked", child

    # Runs against the child ticket only (eng first pass, lead reject, eng resumed).
    eng_runs = sorted((r for r in runs if r["ticket_id"] == child["id"]), key=lambda r: r["started_at"])
    assert len(eng_runs) == 3, eng_runs

    # First run for the child ticket is eng-1's "auto" run (from tickets[]); the second
    # is lead-1's handoff reject; the third is eng-1 again via handoff, and it must
    # carry forward the first run's session_id.
    first_eng = next(r for r in eng_runs if r["trigger"] == "auto")
    second_eng = next(r for r in eng_runs if r["trigger"] == "handoff" and r["agent_id"] == first_eng["agent_id"])

    assert first_eng["session_id"] == "sess-eng-first"

    second_detail = _run_detail(client, second_eng["id"])
    assert second_detail["report"]["summary"].strip() == f"resumed:{first_eng['session_id']}"
