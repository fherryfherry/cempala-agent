"""API tests for MAP-008 agent CRUD."""

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
    # main.py's lifespan calls recover_interrupted_runs(db_session.async_session) directly,
    # bypassing the get_session override above — point it at this test's engine too.
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


def _agent_payload(name="Alice", role="engineer", tool_kind="opencode"):
    return {
        "name": name,
        "role": role,
        "model": "claude-sonnet",
        "tool_kind": tool_kind,
        "system_prompt": "be helpful",
    }


def test_create_agent_success(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Alice"
    assert body["role"] == "engineer"
    assert body["tool_kind"] == "opencode"
    assert body["enabled"] is True
    assert body["status"] == "idle"
    assert body["workspace_id"] == ws_id


def test_create_agent_without_model(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    payload = _agent_payload()
    del payload["model"]
    resp = client.post(f"/api/workspaces/{ws_id}/agents", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["model"] is None


def test_create_duplicate_name_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    payload = _agent_payload()
    resp1 = client.post(f"/api/workspaces/{ws_id}/agents", json=payload)
    assert resp1.status_code == 201

    resp2 = client.post(f"/api/workspaces/{ws_id}/agents", json=payload)
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "duplicate_name"


def test_create_invalid_role_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents", json=_agent_payload(role="astronaut")
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_role"


def test_create_invalid_tool_kind_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents", json=_agent_payload(tool_kind="gpt")
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_create_under_nonexistent_workspace_404(client):
    resp = client.post("/api/workspaces/does-not-exist/agents", json=_agent_payload())
    assert resp.status_code == 404


def test_delete_agent_with_active_run_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_resp = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload())
    agent_id = agent_resp.json()["id"]

    # Insert a running Run directly via the DB session dependency override.
    import asyncio

    from app.db.models import Run, Ticket

    async def _insert_running_run():
        async for session in app.dependency_overrides[get_session]():
            ticket = Ticket(workspace_id=ws_id, key="ACM-1", title="t")
            session.add(ticket)
            await session.flush()
            run = Run(
                ticket_id=ticket.id,
                agent_id=agent_id,
                status="running",
                trigger="manual",
                tool_kind="opencode",
                model="claude-sonnet",
            )
            session.add(run)
            await session.commit()
            break

    asyncio.run(_insert_running_run())

    resp = client.delete(f"/api/agents/{agent_id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "agent_has_active_run"


def test_delete_agent_without_active_run_succeeds(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_resp = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload())
    agent_id = agent_resp.json()["id"]

    resp = client.delete(f"/api/agents/{agent_id}")
    assert resp.status_code == 204


def test_list_and_patch_happy_path(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_resp = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload())
    agent_id = agent_resp.json()["id"]

    list_resp = client.get(f"/api/workspaces/{ws_id}/agents")
    assert list_resp.status_code == 200
    assert any(a["id"] == agent_id for a in list_resp.json())

    patch_resp = client.patch(f"/api/agents/{agent_id}", json={"enabled": False, "model": "gpt-5"})
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["enabled"] is False
    assert body["model"] == "gpt-5"


def test_create_agent_with_avatar(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    payload = _agent_payload()
    payload["avatar_template"] = "person-3"
    payload["avatar_color"] = "#10b981"
    resp = client.post(f"/api/workspaces/{ws_id}/agents", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["avatar_template"] == "person-3"
    assert body["avatar_color"] == "#10b981"


def test_patch_agent_avatar(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload()).json()["id"]

    resp = client.patch(
        f"/api/agents/{agent_id}",
        json={"avatar_template": "person-1", "avatar_color": "#6366f1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["avatar_template"] == "person-1"
    assert body["avatar_color"] == "#6366f1"

    # Explicit null clears back to plain initials.
    resp = client.patch(f"/api/agents/{agent_id}", json={"avatar_template": None, "avatar_color": None})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["avatar_template"] is None
    assert body["avatar_color"] is None


def test_create_agent_invalid_avatar_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)

    bad_template = client.post(
        f"/api/workspaces/{ws_id}/agents", json={**_agent_payload(), "avatar_template": "robot-99"}
    )
    assert bad_template.status_code == 422

    bad_color = client.post(
        f"/api/workspaces/{ws_id}/agents", json={**_agent_payload(), "avatar_color": "red"}
    )
    assert bad_color.status_code == 422
    assert bad_color.json()["error"]["code"] == "validation_error"


def test_list_agents_memory_count_zero_for_fresh_agent(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload()).json()["id"]

    list_resp = client.get(f"/api/workspaces/{ws_id}/agents")
    assert list_resp.status_code == 200
    listed = next(a for a in list_resp.json() if a["id"] == agent_id)
    assert listed["memory_count"] == 0


def test_list_agents_memory_count_reflects_notes(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_a = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload("Alice")).json()["id"]
    agent_b = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload("Bob")).json()["id"]

    for note in ("first", "second", "third"):
        resp = client.post(f"/api/agents/{agent_a}/memory", json={"note": note})
        assert resp.status_code == 201, resp.text
    resp = client.post(f"/api/agents/{agent_b}/memory", json={"note": "only one"})
    assert resp.status_code == 201, resp.text

    list_resp = client.get(f"/api/workspaces/{ws_id}/agents")
    assert list_resp.status_code == 200
    by_id = {a["id"]: a for a in list_resp.json()}
    assert by_id[agent_a]["memory_count"] == 3
    assert by_id[agent_b]["memory_count"] == 1


def test_patch_agent_unknown_role_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent_id = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload()).json()["id"]

    resp = client.patch(f"/api/agents/{agent_id}", json={"role": "astronaut"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_role"


def test_patch_agent_duplicate_name_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload("Alice"))
    agent_b = client.post(f"/api/workspaces/{ws_id}/agents", json=_agent_payload("Bob")).json()["id"]

    resp = client.patch(f"/api/agents/{agent_b}", json={"name": "Alice"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_name"
