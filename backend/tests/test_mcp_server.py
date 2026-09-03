"""Tests for the MCP ticket server (ADR-011): tools call the backend HTTP API
(validated end-to-end through the real FastAPI app via httpx WSGITransport).
"""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import INTERNAL_MCP_SECRET, settings
from app.core.auth import get_current_user, hash_password
from app.db import session as db_session
from app.db.models import Base, User
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
    # Drop conftest.py's blanket superadmin override — these tests must prove
    # the MCP server's *own* auth (MAP_INTERNAL_SECRET, ADR-016) actually works,
    # not ride on the test-only bypass every other file uses.
    app.dependency_overrides.pop(get_current_user, None)

    # Point the MCP server's HTTP client at the real app (ASGI transport), with
    # the same internal-secret header a real spawned MCP subprocess sends
    # (app/agents/mcp_config.py sets MAP_INTERNAL_SECRET in its env).
    async def _point_client():
        transport = httpx.ASGITransport(app=app)
        import app.mcp_server as mcp_mod

        await mcp_mod._http.aclose()
        mcp_mod._http = httpx.AsyncClient(
            transport=transport,
            timeout=30,
            headers={"x-map-internal-secret": INTERNAL_MCP_SECRET},
        )

    asyncio.run(_point_client())

    async def _seed_superadmin():
        async with maker() as session:
            session.add(
                User(
                    email="owner@test.local",
                    password_hash=hash_password("secret123"),
                    is_superadmin=True,
                )
            )
            await session.commit()

    asyncio.run(_seed_superadmin())

    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        # Setup calls below (_make_workspace/_make_agent/_make_ticket) go through
        # this TestClient as "the owner", authenticated for real via cookie — a
        # separate mechanism from the MCP server's own header-based bypass above,
        # so this genuinely exercises both auth paths independently.
        login = c.post("/api/auth/login", json={"email": "owner@test.local", "password": "secret123"})
        assert login.status_code == 200, login.text
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


def test_mcp_write_tools_refuse_roles_without_the_permission(client, tmp_path):
    """`create_ticket`/`update_ticket` used to bypass the `may_declare_tickets` gate
    report.py enforces on `tickets:`/`updates:` — an engineer could create through the
    tool exactly what the parser would have dropped. CLAUDE.md: role permissions are
    enforced in the parser, not trusted to the prompt."""
    ws = _make_workspace(client, tmp_path)
    eng = _make_agent(client, ws["id"], "engineer", "eng-1")
    ticket = _make_ticket(client, ws["id"], "A ticket")

    import app.mcp_server as mcp_mod

    mcp_mod.WORKSPACE_ID = ws["id"]
    mcp_mod.AGENT_ID = eng["id"]
    mcp_mod._ROLE_FLAGS.clear()

    server = create_server()

    async def _call(name, args):
        result = await server.call_tool(name, args)
        return result.content[0].text if result.content else ""

    out = asyncio.run(_call("create_ticket", {"title": "Sneaky", "description": "x"}))
    assert "may_declare_tickets" in out
    assert "Refused" in out
    titles = [t["title"] for t in client.get(f"/api/workspaces/{ws['id']}/tickets").json()]
    assert "Sneaky" not in titles

    out = asyncio.run(_call("update_ticket", {"key": ticket["key"], "status": "done"}))
    assert "may_declare_tickets" in out
    assert client.get(f"/api/tickets/{ticket['key']}").json()["status"] == "backlog"

    # Read tools stay open to every role.
    out = asyncio.run(_call("list_tickets", {}))
    assert ticket["key"] in out


def test_mcp_tools_end_to_end(client, tmp_path, monkeypatch):
    ws = _make_workspace(client, tmp_path)
    pm = _make_agent(client, ws["id"], "pm", "pm-1")
    ticket = _make_ticket(client, ws["id"], "Tiket macet")

    import app.mcp_server as mcp_mod

    mcp_mod.WORKSPACE_ID = ws["id"]
    mcp_mod.AGENT_ID = pm["id"]
    mcp_mod._ROLE_FLAGS.clear()

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

    # list_sprints shows the active flag (MAP owner-chat reported it missing entirely)
    resp = client.post(f"/api/workspaces/{ws['id']}/sprints", json={"name": "Sprint 1"})
    assert resp.status_code == 201, resp.text
    sprint = resp.json()
    resp = client.patch(f"/api/sprints/{sprint['id']}", json={"status": "active"})
    assert resp.status_code == 200, resp.text
    out = asyncio.run(_call("list_sprints", {}))
    assert "Sprint 1 [ACTIVE]" in out

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


def test_internal_secret_resolves_from_cli_args_when_env_missing(monkeypatch):
    """Real dogfooding regression: opencode dropped the whole env block (same
    MAP-048 class of bug as the workspace/agent ids), leaving MAP_INTERNAL_SECRET
    permanently empty and every MCP tool call 401ing with "login required"
    regardless of backend restarts. The secret needs the same CLI-flag fallback
    the ids already had."""
    import sys

    import app.mcp_server as mcp_mod

    monkeypatch.delenv("MAP_INTERNAL_SECRET", raising=False)
    monkeypatch.setattr(sys, "argv", ["mcp_server.py", "--internal-secret", "secret-from-cli"])

    assert mcp_mod._internal_secret_from_env_or_args() == "secret-from-cli"


def test_internal_secret_prefers_env_over_cli(monkeypatch):
    import app.mcp_server as mcp_mod

    monkeypatch.setenv("MAP_INTERNAL_SECRET", "secret-from-env")
    assert mcp_mod._internal_secret_from_env_or_args() == "secret-from-env"
