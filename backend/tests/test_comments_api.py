"""API tests for MAP-010 comment CRUD + @mention parsing."""

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


def _make_agent(client, ws_id, name):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": "engineer", "model": "big-pickle", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_ticket(client, ws_id, title="Do the thing"):
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json={"title": title})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_comment_without_mentions(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "plain comment"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["body"] == "plain comment"
    assert body["author_agent_id"] is None
    assert body["is_system"] is False
    assert body["mentions"] == []


def test_create_comment_with_valid_mention(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)
    eng = _make_agent(client, ws_id, "eng-1")

    resp = client.post(
        f"/api/tickets/{ticket['key']}/comments", json={"body": "hey @eng-1 look at this"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["mentions"] == ["eng-1"]

    listed = client.get(f"/api/tickets/{ticket['key']}/comments").json()
    assert len(listed) == 1
    assert listed[0]["mentions"] == ["eng-1"]
    assert eng["id"]  # sanity


def test_unknown_mention_no_row_no_error(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(
        f"/api/tickets/{ticket['key']}/comments", json={"body": "cc @tidak-ada please"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["mentions"] == []


def test_self_mention_discarded(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)
    eng = _make_agent(client, ws_id, "eng-1")

    resp = client.post(
        f"/api/tickets/{ticket['key']}/comments",
        json={"body": "note to self @eng-1", "author_agent_id": eng["id"]},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["mentions"] == []


def test_duplicate_mention_collapses_to_one_row(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)
    _make_agent(client, ws_id, "eng-1")

    resp = client.post(
        f"/api/tickets/{ticket['key']}/comments",
        json={"body": "@eng-1 and again @eng-1 please"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["mentions"] == ["eng-1"]


def test_list_comments_ordered(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)

    client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "first"})
    client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "second"})

    resp = client.get(f"/api/tickets/{ticket['key']}/comments")
    assert resp.status_code == 200
    bodies = [c["body"] for c in resp.json()]
    assert bodies == ["first", "second"]


def test_comment_on_nonexistent_ticket_404(client):
    resp = client.post("/api/tickets/MAP-999/comments", json={"body": "hi"})
    assert resp.status_code == 404

    resp = client.get("/api/tickets/MAP-999/comments")
    assert resp.status_code == 404


def test_comment_with_unknown_author_agent_404(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(
        f"/api/tickets/{ticket['key']}/comments",
        json={"body": "hi", "author_agent_id": "does-not-exist"},
    )
    assert resp.status_code == 404
