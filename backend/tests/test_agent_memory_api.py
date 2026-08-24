"""API tests for agent memory (MAP-035): GET/POST/DELETE /agents/{id}/memory."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import session as db_session
from app.db.models import Base
from app.db.session import get_session
from app.main import app


@pytest.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    monkeypatch.setattr(db_session, "async_session", maker)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


def _make_workspace(client, tmp_path):
    resp = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_agent(client, ws_id, name="Alice"):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": "engineer", "model": "claude-sonnet", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_list_memory_empty(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id)

    resp = client.get(f"/api/agents/{agent_id}/memory")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_memory_manual_owner_note(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id)

    resp = client.post(f"/api/agents/{agent_id}/memory", json={"note": "Jangan lupa run test"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["note"] == "Jangan lupa run test"
    assert body["origin"] == "owner"
    assert body["source_ticket_key"] is None
    assert body["agent_id"] == agent_id

    list_resp = client.get(f"/api/agents/{agent_id}/memory")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


def test_create_memory_truncated_to_max_length(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id)

    long_note = "x" * 1000
    resp = client.post(f"/api/agents/{agent_id}/memory", json={"note": long_note})
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["note"]) == 500


def test_create_memory_empty_note_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id)

    resp = client.post(f"/api/agents/{agent_id}/memory", json={"note": "   "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "empty_note"


def test_create_memory_under_nonexistent_agent_404(client):
    resp = client.post("/api/agents/does-not-exist/memory", json={"note": "hi"})
    assert resp.status_code == 404


def test_list_memory_most_recent_first(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id)

    client.post(f"/api/agents/{agent_id}/memory", json={"note": "first"})
    client.post(f"/api/agents/{agent_id}/memory", json={"note": "second"})

    resp = client.get(f"/api/agents/{agent_id}/memory")
    notes = [m["note"] for m in resp.json()]
    assert notes == ["second", "first"]


def test_delete_memory(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id)

    created = client.post(f"/api/agents/{agent_id}/memory", json={"note": "to delete"}).json()

    resp = client.delete(f"/api/agent-memory/{created['id']}")
    assert resp.status_code == 204

    assert client.get(f"/api/agents/{agent_id}/memory").json() == []


def test_update_memory(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id)

    created = client.post(f"/api/agents/{agent_id}/memory", json={"note": "old note"}).json()

    resp = client.patch(f"/api/agent-memory/{created['id']}", json={"note": "new note"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] == "new note"

    listed = client.get(f"/api/agents/{agent_id}/memory").json()
    assert listed[0]["note"] == "new note"


def test_update_memory_truncates_and_rejects_empty(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id)
    created = client.post(f"/api/agents/{agent_id}/memory", json={"note": "x"}).json()

    long_note = "y" * 1000
    resp = client.patch(f"/api/agent-memory/{created['id']}", json={"note": long_note})
    assert resp.status_code == 200
    assert len(resp.json()["note"]) == 500

    resp = client.patch(f"/api/agent-memory/{created['id']}", json={"note": "   "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "empty_note"


def test_update_nonexistent_memory_404(client):
    resp = client.patch("/api/agent-memory/does-not-exist", json={"note": "hi"})
    assert resp.status_code == 404


def test_delete_nonexistent_memory_404(client):
    resp = client.delete("/api/agent-memory/does-not-exist")
    assert resp.status_code == 404


def test_memory_scoped_per_agent(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_a = _make_agent(client, ws_id, name="Alice")
    agent_b = _make_agent(client, ws_id, name="Bob")

    client.post(f"/api/agents/{agent_a}/memory", json={"note": "only for alice"})

    assert len(client.get(f"/api/agents/{agent_a}/memory").json()) == 1
    assert client.get(f"/api/agents/{agent_b}/memory").json() == []


def test_memory_cascades_on_agent_delete(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = _make_agent(client, ws_id)
    client.post(f"/api/agents/{agent_id}/memory", json={"note": "note"})

    resp = client.delete(f"/api/agents/{agent_id}")
    assert resp.status_code == 204

    # Agent gone -> its memory sub-resource 404s (agent lookup fails first).
    assert client.get(f"/api/agents/{agent_id}/memory").status_code == 404
