"""WebSocket-layer tests for the Terminal menu route (app/api/terminal.py).

app/core/terminal.py's spawn/cleanup are already tested directly against a real
PTY in test_terminal.py; these tests drive the same real PTY through the actual
WebSocket route via TestClient.websocket_connect (no mocking).
"""

import json
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import hash_password
from app.db import session as db_session
from app.db.models import Base, User
from app.db.session import get_session
from app.main import app

_TEST_EMAIL = "terminal-test@example.com"
_TEST_PASSWORD = "test-password"


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
    # terminal.py did `from app.db.session import async_session`, binding its own
    # module-local reference at import time — patching db_session.async_session
    # above does not affect it, so it must be patched directly too.
    import app.api.terminal as terminal_mod

    monkeypatch.setattr(terminal_mod, "async_session", maker)

    async with maker() as session:
        session.add(
            User(email=_TEST_EMAIL, password_hash=hash_password(_TEST_PASSWORD), is_superadmin=True)
        )
        await session.commit()

    with TestClient(app) as c:
        # The WS route reads the session cookie directly off the socket (not via
        # a FastAPI Depends), so it isn't covered by conftest.py's blanket
        # get_current_user override — log in for real so the cookie jar (shared
        # between HTTP calls and websocket_connect) carries a valid session.
        resp = c.post("/api/auth/login", json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD})
        assert resp.status_code == 200, resp.text
        yield c
    app.dependency_overrides.clear()


def _make_workspace(client, tmp_path, key="MAP"):
    resp = client.post(
        "/api/workspaces", json={"name": "Map", "key": key, "repo_path": str(tmp_path)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_terminal_ws_404_unknown_workspace(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/workspaces/does-not-exist/terminal/ws") as ws:
            ws.receive_bytes()


def test_terminal_ws_404_missing_repo_path(client, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    ws_id = _make_workspace(client, repo_dir)
    shutil.rmtree(repo_dir)

    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/workspaces/{ws_id}/terminal/ws") as ws:
            ws.receive_bytes()


def test_terminal_ws_echo_roundtrip(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)

    with client.websocket_connect(f"/api/workspaces/{ws_id}/terminal/ws") as ws:
        ws.send_bytes(b"echo hi\n")
        deadline = time.time() + 5
        seen = b""
        while time.time() < deadline and b"hi" not in seen:
            seen += ws.receive_bytes()
        assert b"hi" in seen


def test_terminal_ws_resize_control_message(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)

    with client.websocket_connect(f"/api/workspaces/{ws_id}/terminal/ws") as ws:
        ws.send_text(json.dumps({"type": "resize", "cols": 100, "rows": 40}))
        # Connection must survive the control message and still echo bytes.
        ws.send_bytes(b"echo ok\n")
        deadline = time.time() + 5
        seen = b""
        while time.time() < deadline and b"ok" not in seen:
            seen += ws.receive_bytes()
        assert b"ok" in seen


def test_terminal_ws_ignores_malformed_json(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)

    with client.websocket_connect(f"/api/workspaces/{ws_id}/terminal/ws") as ws:
        ws.send_text("not json{{{")
        # Session must still be usable afterwards.
        ws.send_bytes(b"echo alive\n")
        deadline = time.time() + 5
        seen = b""
        while time.time() < deadline and b"alive" not in seen:
            seen += ws.receive_bytes()
        assert b"alive" in seen


def test_terminal_ws_disconnect_cleanup(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)

    with client.websocket_connect(f"/api/workspaces/{ws_id}/terminal/ws") as ws:
        ws.send_bytes(b"echo hi\n")
        time.sleep(0.2)
    # Exiting the context manager closes the client side; the server's finally
    # block (reader_task.cancel() + cleanup()) must run without hanging the
    # next request on this same TestClient/event loop.
    resp = client.get("/api/health")
    assert resp.status_code == 200
