"""Tests for the MCP ticket server (ADR-011): tools call the backend HTTP API
(validated end-to-end through the real FastAPI app via httpx WSGITransport).
"""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import session as db_session
from app.db.models import Base
from app.db.session import get_session
from app.main import app
from app.mcp_server import AGENT_ID, WORKSPACE_ID, _http, create_server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))

    db_path = tmp_path / f"{uuid.uuid4().hex}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async def _override_get_session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    monkeypatch.setattr(db_session, "async_session", maker)

    # Point the MCP server's HTTP client at the real app (ASGI transport).
    async def _point_client():
        transport = httpx.ASGITransport(app=app)
        import app.mcp_server as mcp_mod

        await mcp_mod._http.aclose()
        mcp_mod._http = httpx.AsyncClient(transport=transport, timeout=30)

    asyncio.run(_point_client())

    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()

    async def _dispose():
        await engine.dispose()
        await _http.aclose()

    asyncio.run(_dispose())


def _make_workspace(client, tmp_path):
    resp = client.post(
        "/api/workspaces", json={"name": "Map", "key": "MAP", "repo_path": str(tmp_path)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()

def _make_agent(client, ws_id, role, name):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": "opencode/big-pickle", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_ticket(client, ws_id, title="Do the thing"):
    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": title, "is_new_epic": True}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_mcp_tools_end_to_end(client, tmp_path, monkeypatch):
    ws = _make_workspace(client, tmp_path)
    pm = _make_agent(client, ws["id"], "pm", "pm-1")
    ticket = _make_ticket(client, ws["id"], "Tiket macet")

    import app.mcp_server as mcp_mod

    mcp_mod.WORKSPACE_ID = ws["id"]
    mcp_mod.AGENT_ID = pm["id"]

    server = create_server()

    async def _call(name, args):
        result = await server.call_tool(name, args)
        return result.content[0].text if result.content else ""

    # list_tickets (desc order + pagination)
    out = asyncio.run(_call("list_tickets", {}))
    assert ticket["key"] in out
    assert "Tiket macet" in out
    out = asyncio.run(_call("list_tickets", {"limit": 1, "offset": 0}))
    assert ticket["key"] in out

    # get_ticket detail includes the key and status
    out = asyncio.run(_call("get_ticket", {"key": ticket["key"]}))
    assert ticket["key"] in out
    assert "backlog" in out

    # post_comment (as the running agent), then list_comments shows it first
    out = asyncio.run(_call("post_comment", {"key": ticket["key"], "body": "Tolong dicek."}))
    assert "Comment posted" in out
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    agent_comments = [c for c in detail["comments"] if not c["is_system"]]
    assert len(agent_comments) == 1
    assert agent_comments[0]["author_agent_id"] == pm["id"]

    out = asyncio.run(_call("list_comments", {"key": ticket["key"]}))
    assert "Tolong dicek" in out

    # create_ticket (backlog, not auto-run)
    out = asyncio.run(_call("create_ticket", {"title": "Dari MCP", "description": "x"}))
    assert "MAP-" in out
    created = client.get("/api/workspaces/%s/tickets" % ws["id"]).json()
    new = next(t for t in created if t["title"] == "Dari MCP")
    assert new["status"] == "backlog"
    assert new["parent_id"] is None

    # list_tickets flags top-level tickets as [EPIC] so the agent can spot reuse
    # candidates before calling create_ticket.
    out = asyncio.run(_call("list_tickets", {}))
    assert f"{new['key']} [EPIC]" in out

    # create_ticket(epic=...) attaches to an existing epic instead of spawning a
    # fresh one (docs/03-agent-design.md §3).
    out = asyncio.run(_call("create_ticket", {"title": "Sub dari MCP", "epic": new["key"]}))
    assert "MAP-" in out
    created = client.get("/api/workspaces/%s/tickets" % ws["id"]).json()
    sub = next(t for t in created if t["title"] == "Sub dari MCP")
    assert sub["parent_id"] == new["id"]

    # create_ticket(epic=<a ticket that itself has a parent>) is rejected, not
    # silently top-leveled.
    out = asyncio.run(_call("create_ticket", {"title": "Bad epic", "epic": sub["key"]}))
    assert "Failed" in out or "not a top-level epic" in out
    created = client.get("/api/workspaces/%s/tickets" % ws["id"]).json()
    assert not any(t["title"] == "Bad epic" for t in created)

    # update_ticket status -> todo (as agent)
    out = asyncio.run(_call("update_ticket", {"key": ticket["key"], "status": "todo"}))
    assert "updated" in out
    assert client.get(f"/api/tickets/{ticket['key']}").json()["status"] == "todo"

    # delete_ticket (PM can delete; ticket gone)
    out = asyncio.run(_call("delete_ticket", {"key": ticket["key"]}))
    assert "deleted" in out
    assert client.get(f"/api/tickets/{ticket['key']}").status_code == 404

    # memory tools
    out = asyncio.run(_call("create_memory", {"note": "ingat ini"}))
    assert "Memory saved" in out
    out = asyncio.run(_call("get_memory", {}))
    assert "ingat ini" in out
    notes = client.get(f"/api/agents/{pm['id']}/memory").json()
    mem_id = notes[0]["id"]
    out = asyncio.run(_call("update_memory", {"memory_id": mem_id, "note": "update"}))
    assert "updated" in out
    notes = client.get(f"/api/agents/{pm['id']}/memory").json()
    assert notes[0]["note"] == "update"

    # error path: unknown ticket
    out = asyncio.run(_call("get_ticket", {"key": "MAP-999"}))
    assert "Failed" in out


def test_ids_resolve_from_cli_args_when_env_missing(monkeypatch):
    """MAP-048: opencode's MCP launcher may drop the env block of a local config,
    so the server must also read workspace/agent ids from CLI flags."""
    import sys

    import app.mcp_server as mcp_mod

    monkeypatch.delenv("MAP_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("MAP_AGENT_ID", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mcp_server.py", "--workspace-id", "ws-1", "--agent-id", "ag-1"],
    )

    ws, agent = mcp_mod._ids_from_env_or_args()
    assert ws == "ws-1"
    assert agent == "ag-1"


def test_ids_prefer_env_over_cli(monkeypatch):
    import app.mcp_server as mcp_mod

    monkeypatch.setenv("MAP_WORKSPACE_ID", "ws-env")
    monkeypatch.setenv("MAP_AGENT_ID", "ag-env")
    ws, agent = mcp_mod._ids_from_env_or_args()
    assert ws == "ws-env"
    assert agent == "ag-env"
