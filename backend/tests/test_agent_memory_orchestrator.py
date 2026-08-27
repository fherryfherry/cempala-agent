"""End-to-end tests for agent memory (MAP-035): ```map `memory:` -> AgentMemory rows

persisted for the reporting agent, surfaced via GET /agents/{id}/memory, and injected
back into that agent's next prompt. Same fake-opencode-binary technique as
test_orchestrator.py/test_artifacts_api.py.
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


def _map_script(session_id, memory_yaml="", status="review", mention="[]"):
    return f'''
import json
text = """Done.

```map
status: {status}
mention: {mention}
summary: |
  did the work
{memory_yaml}```
"""
print(json.dumps({{
    "type": "assistant_text",
    "text": text,
    "session_id": "{session_id}",
}}))
'''


def test_memory_persisted_and_reported_on_valid_report(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(
        tmp_path / "opencode",
        _map_script(
            "sess-1",
            memory_yaml=(
                "memory:\n"
                "  - jangan lupa jalankan migrasi sebelum test\n"
                "  - repo ini pakai uv, bukan pip langsung\n"
            ),
        ),
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["report"]["memory"] == [
        "jangan lupa jalankan migrasi sebelum test",
        "repo ini pakai uv, bukan pip langsung",
    ]

    memories = client.get(f"/api/agents/{eng_id}/memory").json()
    assert len(memories) == 2
    notes = {m["note"] for m in memories}
    assert notes == {
        "jangan lupa jalankan migrasi sebelum test",
        "repo ini pakai uv, bukan pip langsung",
    }
    assert all(m["origin"] == "agent" for m in memories)
    assert all(m["source_ticket_key"] == ticket["key"] for m in memories)

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    bodies = [c["body"] for c in detail["comments"]]
    assert any("Memory disimpan" in b for b in bodies)


def test_no_memory_field_leaves_report_empty(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(tmp_path / "opencode", _map_script("sess-1"))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["report"]["memory"] == []
    assert client.get(f"/api/agents/{eng_id}/memory").json() == []


def test_memory_injected_into_agents_next_prompt(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")

    ticket1 = _make_ticket(client, ws_id, title="First ticket")
    _set_status(client, ticket1["key"], "todo")

    script1 = _write_python_binary(
        tmp_path / "opencode1",
        _map_script("sess-1", memory_yaml="memory:\n  - hindari hardcode api key\n"),
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script1)

    run1 = client.post(f"/api/tickets/{ticket1['key']}/run", json={"agent_id": eng_id}).json()
    final1 = _wait_for_run(client, run1["id"])
    assert final1["status"] == "done", final1

    # Second ticket, same agent, plain script (no memory: this time) — the prompt for
    # THIS run should already carry the note saved from the first run.
    ticket2 = _make_ticket(client, ws_id, title="Second ticket")
    _set_status(client, ticket2["key"], "todo")

    script2 = _write_python_binary(tmp_path / "opencode2", _map_script("sess-2"))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script2)

    run2 = client.post(f"/api/tickets/{ticket2['key']}/run", json={"agent_id": eng_id}).json()
    final2 = _wait_for_run(client, run2["id"])
    assert final2["status"] == "done", final2

    detail2 = client.get(f"/api/runs/{run2['id']}").json()
    prompt2 = detail2["events"][0]["payload"]["prompt"]
    assert "Notes from your previous work" in prompt2
    assert "hindari hardcode api key" in prompt2

    # The first run's own prompt had no memory yet (nothing saved before it started).
    detail1 = client.get(f"/api/runs/{run1['id']}").json()
    prompt1 = detail1["events"][0]["payload"]["prompt"]
    assert "Notes from your previous work" not in prompt1


def test_owner_added_memory_also_injected_into_prompt(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")

    resp = client.post(
        f"/api/agents/{eng_id}/memory", json={"note": "selalu tulis test untuk perubahan schema"}
    )
    assert resp.status_code == 201, resp.text

    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(tmp_path / "opencode", _map_script("sess-1"))
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    run = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": eng_id}).json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final

    detail = client.get(f"/api/runs/{run['id']}").json()
    prompt = detail["events"][0]["payload"]["prompt"]
    assert "selalu tulis test untuk perubahan schema" in prompt
