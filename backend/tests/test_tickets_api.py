"""API tests for MAP-009 ticket CRUD + key numbering."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base
from app.db.session import get_session
from app.main import app


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


def _make_workspace(client, tmp_path, key="MAP"):
    resp = client.post(
        "/api/workspaces", json={"name": "Map", "key": key, "repo_path": str(tmp_path)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _ticket_payload(title="Do the thing", **overrides):
    payload = {"title": title, "description": "desc"}
    payload.update(overrides)
    return payload


def test_create_ticket_success_and_key_format(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["key"] == "MAP-001"
    assert body["status"] == "backlog"
    assert body["priority"] == "medium"
    assert body["workspace_id"] == ws_id


def test_sequential_numbering(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    keys = []
    for i in range(4):
        resp = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload(f"T{i}"))
        assert resp.status_code == 201
        keys.append(resp.json()["key"])
    assert keys == ["MAP-001", "MAP-002", "MAP-003", "MAP-004"]


def test_key_not_reused_after_delete(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    r1 = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("first"))
    key1 = r1.json()["key"]
    assert client.delete(f"/api/tickets/{key1}").status_code == 204

    r2 = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("second"))
    assert r2.json()["key"] == "MAP-002"


def test_parent_nesting_max_one_level_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    parent = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("parent")).json()
    child = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json=_ticket_payload("child", parent_id=parent["id"]),
    ).json()

    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json=_ticket_payload("grandchild", parent_id=child["id"]),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "nesting_too_deep"


def test_get_ticket_with_nested_data(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    parent = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("parent")).json()
    child = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json=_ticket_payload("child", parent_id=parent["id"]),
    ).json()

    resp = client.get(f"/api/tickets/{parent['key']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["comments"] == []
    assert body["attachments"] == []
    assert body["runs"] == []
    assert len(body["children"]) == 1
    assert body["children"][0]["key"] == child["key"]


def test_list_filters(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    a = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("a")).json()
    b = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("b")).json()
    client.patch(f"/api/tickets/{b['key']}", json={"status": "in_progress"})

    resp = client.get(f"/api/workspaces/{ws_id}/tickets", params={"status": "in_progress"})
    assert resp.status_code == 200
    keys = [t["key"] for t in resp.json()]
    assert keys == [b["key"]]

    resp = client.get(f"/api/workspaces/{ws_id}/tickets", params={"parent_id": a["id"]})
    assert resp.json() == []


def test_patch_happy_path(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(
        f"/api/tickets/{ticket['key']}",
        json={"title": "renamed", "priority": "urgent", "status": "todo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "renamed"
    assert body["priority"] == "urgent"
    assert body["status"] == "todo"


def _make_agent(client, ws_id, role, name="agent"):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": "gpt", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_patch_illegal_transition_422_names_transition(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()
    engineer_id = _make_agent(client, ws_id, "engineer")

    resp = client.patch(
        f"/api/tickets/{ticket['key']}",
        json={"status": "review", "actor_agent_id": engineer_id},
    )
    assert resp.status_code == 422
    msg = resp.json()["error"]["message"]
    assert "backlog" in msg and "review" in msg and "engineer" in msg


def test_patch_owner_bypasses_matrix_including_blocked_to_todo(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "blocked"})
    assert resp.status_code == 200, resp.text

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "todo"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "todo"


def test_patch_legal_transition_writes_system_comment(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "todo"})
    assert resp.status_code == 200

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    comments = detail["comments"]
    assert len(comments) == 1
    assert comments[0]["is_system"] is True
    assert comments[0]["author_agent_id"] is None
    assert "backlog" in comments[0]["body"] and "todo" in comments[0]["body"]


def test_patch_unknown_actor_agent_id_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(
        f"/api/tickets/{ticket['key']}",
        json={"status": "todo", "actor_agent_id": "nonexistent"},
    )
    assert resp.status_code == 422


def test_delete_ticket(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    assert client.delete(f"/api/tickets/{ticket['key']}").status_code == 204
    assert client.get(f"/api/tickets/{ticket['key']}").status_code == 404


def test_get_nonexistent_ticket_404(client):
    resp = client.get("/api/tickets/MAP-999")
    assert resp.status_code == 404


@pytest.mark.parametrize("n", [20, 100])
async def test_concurrent_creates_get_unique_sequential_keys(tmp_path, n):
    """N concurrent POSTs against the same workspace must yield N unique, sequential keys.

    MAP-016 AC requires the 100-way case specifically (stress level beyond MAP-009's
    original 20-way check, kept here as a parametrize case for regression coverage).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

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
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/workspaces",
                json={"name": "Map", "key": "MAP", "repo_path": str(tmp_path)},
            )
            ws_id = resp.json()["id"]

            async def _create(i):
                return await ac.post(
                    f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload(f"t{i}")
                )

            responses = await asyncio.gather(*[_create(i) for i in range(n)])
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

    assert all(r.status_code == 201 for r in responses)
    keys = [r.json()["key"] for r in responses]
    assert len(set(keys)) == n
    assert sorted(keys) == [f"MAP-{i:03d}" for i in range(1, n + 1)]
