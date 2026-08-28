"""Tests for auto-retry with adaptive prompt (MAP-044).

When a ticket run fails in a *retryable* way (missing/malformed ```map block,
opencode subprocess failure), the orchestrator schedules a child run with
`parent_run_id` chained to the failed one, up to `max_auto_retries` per
(ticket, agent). The child's prompt carries a "WARNING: YOUR PREVIOUS RUN
FAILED" notice with the parent's error + output tail, and starts a fresh
opencode session (`-s` is NOT passed). Only after the budget is exhausted
does the ticket get blocked. Non-retryable failures (state-machine rejection)
and routine runs never retry.
"""

import json
import stat
import time
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import session as db_session
from app.db.models import Base, Ticket
from app.db.session import get_session
from app.main import app
from app.schemas.workspace import DEFAULT_GUARDRAILS

_VALID_MAP = """Done.

```map
status: done
mention: []
summary: |
  finished on retry
```
"""

_GARBAGE = """
import json
print(json.dumps({"type": "assistant_text", "text": "I forgot to close with a map block."}))
"""


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
    resp = client.post(
        "/api/workspaces",
        json={"name": "Map", "key": key, "repo_path": str(tmp_path), **overrides},
    )
    assert resp.status_code == 201, resp.text
    ws_id = resp.json()["id"]
    if guardrails is not None:
        patch = client.patch(f"/api/workspaces/{ws_id}", json={"guardrails": guardrails})
        assert patch.status_code == 200, patch.text
    return ws_id


def _make_agent(client, ws_id, role, name, fallback_tool_kind=None):
    payload = {"name": name, "role": role, "model": "opencode/big-pickle", "tool_kind": "opencode"}
    if fallback_tool_kind is not None:
        payload["fallback_tool_kind"] = fallback_tool_kind
    resp = client.post(f"/api/workspaces/{ws_id}/agents", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _active_sprint_id(client, ws_id):
    sprints = client.get(f"/api/workspaces/{ws_id}/sprints").json()
    active = next((s for s in sprints if s["status"] == "active"), None)
    if active:
        return active["id"]
    resp = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 0"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_ticket(client, ws_id, title="Do the thing", parent_id=None):
    payload = {
        "title": title,
        "is_new_epic": True,
        "sprint_id": _active_sprint_id(client, ws_id),
    }
    if parent_id:
        payload["parent_id"] = parent_id
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_status(client, key, status):
    resp = client.patch(f"/api/tickets/{key}", json={"status": status})
    assert resp.status_code == 200, resp.text


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


def _wait_for_ticket_runs(client, ws_id, ticket_id, expected_count, timeout=20.0):
    """Wait until the ticket has `expected_count` runs AND all of them are terminal
    (the auto-retry chain executes child runs asynchronously, one after another)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
        ticket_runs = [r for r in runs if r["ticket_id"] == ticket_id]
        if len(ticket_runs) >= expected_count and all(
            r["status"] not in ("queued", "running") for r in ticket_runs
        ):
            return ticket_runs
        time.sleep(0.05)
    raise AssertionError(
        f"expected {expected_count} terminal runs on ticket {ticket_id}, "
        f"got {len(ticket_runs)} (statuses: {[r['status'] for r in ticket_runs]})"
    )


def _run_prompt(client, run_id) -> str:
    events = client.get(f"/api/runs/{run_id}").json()["events"]
    started = next(e for e in events if e["type"] == "run_started")
    return started["payload"]["prompt"]


def _garbage_script():
    return _GARBAGE


def _fail_then_ok(counter_path):
    return f"""
import json
import pathlib
counter = pathlib.Path({json.dumps(str(counter_path))})
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
if n == 0:
    print(json.dumps({{"type": "assistant_text", "text": "I did work but forgot the map block."}}))
else:
    print(json.dumps({{"type": "assistant_text", "text": {json.dumps(_VALID_MAP)}}}))
"""


def _fail_then_ok_with_argv(counter_path):
    return f'''
import json
import pathlib
import sys
counter = pathlib.Path({json.dumps(str(counter_path))})
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
if n == 0:
    print(json.dumps({{"type": "assistant_text", "text": "forgot", "session_id": "sess-old"}}))
else:
    print(json.dumps({{"type": "assistant_text", "text": "ARGV:" + json.dumps(sys.argv[1:])}}))
    print(json.dumps({{"type": "assistant_text", "text": {json.dumps(_VALID_MAP)}}}))
'''


def _valid_map_script(status="done"):
    return (
        "import json\n"
        'text = "done\\n\\n```map\\n'
        f"status: {status}\\n"
        'mention: []\\nsummary: |\\n  done\\n```\\n"\\n'
        'print(json.dumps({"type": "assistant_text", "text": text}))\n'
    )


def _valid_map_script_with_cost():
    return (
        "import json\n"
        'print(json.dumps({"type": "assistant_text", "text": "working", "cost": 0.1}))\n'
        'text = "done\\n\\n```map\\nstatus: done\\nmention: []\\nsummary: |\\n  done\\n```\\n"\n'
        'print(json.dumps({"type": "assistant_text", "text": text}))\n'
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_retry_succeeds_and_notice_in_prompt(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    counter = tmp_path / "counter"
    script = _write_python_binary(tmp_path / "opencode", _fail_then_ok(counter))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run1 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    final1 = _wait_for_run(client, run1["id"])
    assert final1["status"] == "failed"

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert len(runs) == 2, runs
    retry_run = next(r for r in runs if r["id"] != run1["id"])
    retry_final = _wait_for_run(client, retry_run["id"])
    assert retry_final["status"] == "done"
    assert retry_final["trigger"] == "auto"
    assert retry_final["parent_run_id"] == run1["id"]

    final_ticket = client.get(f"/api/tickets/{ticket['key']}").json()
    assert final_ticket["status"] == "done"
    assert final_ticket["blocked_reason"] is None

    prompt = _run_prompt(client, retry_run["id"])
    assert "WARNING: YOUR PREVIOUS RUN FAILED" in prompt
    assert "forgot the map block" in prompt

    # ticket never got blocked: no status_change -> blocked event on the failed run
    evs = client.get(f"/api/runs/{run1['id']}").json()["events"]
    assert not any(
        e["type"] == "status_change" and e["payload"].get("to") == "blocked" for e in evs
    )


def test_retry_starts_fresh_session_no_resume(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    counter = tmp_path / "counter"
    script = _write_python_binary(tmp_path / "opencode", _fail_then_ok_with_argv(counter))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run1 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    final1 = _wait_for_run(client, run1["id"])
    assert final1["status"] == "failed"
    assert final1["session_id"] == "sess-old"

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    retry_run = next(r for r in runs if r["id"] != run1["id"])
    _wait_for_run(client, retry_run["id"])

    events = client.get(f"/api/runs/{retry_run['id']}").json()["events"]
    argv_texts = [
        e["payload"].get("text", "")
        for e in events
        if e["type"] == "assistant_text" and e["payload"].get("text", "").startswith("ARGV:")
    ]
    assert argv_texts, "no argv echo event on retry run"
    argv = json.loads(argv_texts[0][len("ARGV:"):])
    assert "-s" not in argv, argv


def test_exhausted_retries_block_ticket(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 2}
    )
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(tmp_path / "opencode", _garbage_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run1 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    runs = _wait_for_ticket_runs(client, ws_id, ticket["id"], 3)
    assert len(runs) == 3, runs  # 1 original + 2 retries
    assert all(r["status"] == "failed" for r in runs)

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"
    assert "max_auto_retries=2" in detail["blocked_reason"]
    assert "Auto-retry habis" in detail["blocked_reason"]

    comments = [c for c in detail["comments"] if c["is_system"]]
    assert any("Auto-retry 1/2 dijalankan" in c["body"] for c in comments)
    assert any("Auto-retry 2/2 dijalankan" in c["body"] for c in comments)


def _claude_valid_map_script():
    return (
        "import json\n"
        f"text = {_VALID_MAP!r}\n"
        'print(json.dumps({"type": "assistant", "message": {"content": '
        '[{"type": "text", "text": text}]}}))\n'
        'print(json.dumps({"type": "result", "subtype": "success"}))\n'
    )


def _claude_garbage_script():
    return (
        'import json\n'
        'print(json.dumps({"type": "assistant", "message": {"content": '
        '[{"type": "text", "text": "still no map block"}]}}))\n'
        'print(json.dumps({"type": "result", "subtype": "success"}))\n'
    )


def test_fallback_tool_used_after_retries_exhausted(client, tmp_path, monkeypatch):
    """Once max_auto_retries is exhausted on the primary tool, one extra attempt
    runs on the agent's configured fallback tool before the ticket blocks."""
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 1}
    )
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1", fallback_tool_kind="claude")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    opencode_script = _write_python_binary(tmp_path / "opencode", _garbage_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", opencode_script)
    claude_script = _write_python_binary(tmp_path / "claude", _claude_valid_map_script())
    monkeypatch.setattr(settings, "CLAUDE_BIN", claude_script)

    client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    # 1 original + 1 primary-tool retry (max_auto_retries=1) + 1 fallback attempt
    runs = _wait_for_ticket_runs(client, ws_id, ticket["id"], 3)
    assert len(runs) == 3, runs
    fallback_run = next(r for r in runs if r["tool_kind"] == "claude")
    assert fallback_run["status"] == "done"

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "done"

    comments = [c for c in detail["comments"] if c["is_system"]]
    assert any("fallback tool: claude" in c["body"] for c in comments)


def test_fallback_tool_also_fails_blocks_ticket(client, tmp_path, monkeypatch):
    """If the fallback attempt fails too, the ticket blocks — no further chaining."""
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 1}
    )
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1", fallback_tool_kind="claude")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    opencode_script = _write_python_binary(tmp_path / "opencode", _garbage_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", opencode_script)
    claude_script = _write_python_binary(tmp_path / "claude", _claude_garbage_script())
    monkeypatch.setattr(settings, "CLAUDE_BIN", claude_script)

    client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    # 1 original + 1 primary-tool retry (max_auto_retries=1) + 1 fallback attempt, no more
    runs = _wait_for_ticket_runs(client, ws_id, ticket["id"], 3)
    assert len(runs) == 3, runs
    assert all(r["status"] == "failed" for r in runs)

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"


def test_fallback_tool_used_immediately_when_max_retries_zero(client, tmp_path, monkeypatch):
    """max_auto_retries=0 means the primary-tool budget is exhausted on the very
    first failure — the fallback tool must still get its one shot, not be skipped
    just because there was no primary-tool retry."""
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 0}
    )
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1", fallback_tool_kind="claude")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    opencode_script = _write_python_binary(tmp_path / "opencode", _garbage_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", opencode_script)
    claude_script = _write_python_binary(tmp_path / "claude", _claude_valid_map_script())
    monkeypatch.setattr(settings, "CLAUDE_BIN", claude_script)

    client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    # 1 original failure + 1 fallback attempt, no primary-tool retry in between
    runs = _wait_for_ticket_runs(client, ws_id, ticket["id"], 2)
    assert len(runs) == 2, runs
    fallback_run = next(r for r in runs if r["tool_kind"] == "claude")
    assert fallback_run["status"] == "done"

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "done"


def test_zero_max_retries_disables_retry(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 0}
    )
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(tmp_path / "opencode", _garbage_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run1 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    _wait_for_run(client, run1["id"])

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert len(runs) == 1, runs
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"


def test_manual_retry_resets_quota(client, tmp_path, monkeypatch):
    """A manual retry (owner click) breaks the auto-retry chain, so the quota resets:
    the next failure starts a fresh 1+max window instead of counting on the old chain."""
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 1}
    )
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(tmp_path / "opencode", _garbage_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run1 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    runs = _wait_for_ticket_runs(client, ws_id, ticket["id"], 2)
    assert len(runs) == 2  # original + 1 retry, both failed -> blocked

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"

    # owner retries manually: unblocks and starts a fresh window
    counter = tmp_path / "counter2"
    script2 = _write_python_binary(tmp_path / "opencode2", _fail_then_ok(counter))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script2)

    resp = client.post(f"/api/runs/{run1['id']}/retry")
    assert resp.status_code == 201, resp.text
    new_run = resp.json()

    # fresh window: the manual retry itself fails (first script invocation), then its
    # auto-retry child (second invocation) succeeds.
    runs = _wait_for_ticket_runs(client, ws_id, ticket["id"], 4)
    assert any(r["id"] == new_run["id"] and r["status"] == "failed" for r in runs), runs
    assert any(r["status"] == "done" for r in runs), runs

    final_ticket = client.get(f"/api/tickets/{ticket['key']}").json()
    assert final_ticket["status"] == "done"


def test_quota_per_agent_pair(client, tmp_path, monkeypatch):
    """Failures by agent A must not consume agent B's retry budget on the same ticket."""
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_auto_retries": 1}
    )
    eng_a = _make_agent(client, ws_id, "engineer", "eng-a")
    eng_b = _make_agent(client, ws_id, "engineer", "eng-b")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(tmp_path / "opencode", _garbage_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run_a = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_a}).json()
    runs = _wait_for_ticket_runs(client, ws_id, ticket["id"], 2)
    assert len(runs) == 2  # A: original + 1 retry
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert detail["status"] == "blocked"

    # owner unblocks, then B works with its own fresh window
    _set_status(client, ticket["key"], "todo")
    counter = tmp_path / "counter"
    script2 = _write_python_binary(tmp_path / "opencode2", _fail_then_ok(counter))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script2)

    run_b = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_b}).json()
    runs_b = _wait_for_ticket_runs(client, ws_id, ticket["id"], 4)
    assert any(r["agent_id"] == eng_b and r["status"] == "done" for r in runs_b), runs_b


def test_routine_run_not_retried(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id, "engineer", "eng-1")

    resp = client.post(
        f"/api/workspaces/{ws_id}/routines",
        json={"name": "nightly", "interval_minutes": 60, "prompt": "do it", "agent_id": agent_id},
    )
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]

    script = _write_python_binary(tmp_path / "opencode", _garbage_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/routines/{rid}/run")
    assert resp.status_code == 200, resp.text

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    routine_run = next(r for r in runs if r["trigger"] == "routine")
    final = _wait_for_run(client, routine_run["id"])
    assert final["status"] == "failed", final

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert len(runs) == 1, runs


def test_guardrail_cancelled_run_not_retried(client, tmp_path, monkeypatch):
    """A run killed by a runtime guardrail (status `cancelled`, e.g. max_cost_per_run)
    is a deliberate brake activation — it must not be auto-retried."""
    ws_id = _make_workspace(
        client, tmp_path, guardrails={**DEFAULT_GUARDRAILS, "max_cost_per_run": 0.0}
    )
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(
        tmp_path / "opencode",
        _valid_map_script_with_cost(),  # emits cost -> trips max_cost_per_run=0
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run1 = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    final1 = _wait_for_run(client, run1["id"])
    assert final1["status"] == "cancelled"

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert len(runs) == 1, runs
