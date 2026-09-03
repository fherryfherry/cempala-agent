"""API tests for login/logout/me, global user management, and per-workspace RBAC
(ADR-016, supersedes ADR-005). conftest.py's autouse `_auth_as_superadmin` fixture
overrides `get_current_user` for every test by default (so the rest of the suite
doesn't need touching) — tests here that exercise the *real* auth flow explicitly
pop that override first.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import INTERNAL_MCP_SECRET
from app.core.auth import INTERNAL_SECRET_HEADER, bootstrap_admin, get_current_user, hash_password
from app.db import session as db_session
from app.db.models import Base, User
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
        c._test_maker = maker  # stashed for tests that need direct DB access
        yield c
    app.dependency_overrides.clear()


def _real_auth(client):
    """Drop the blanket superadmin override so requests go through the real
    get_current_user dependency (cookie-based)."""
    app.dependency_overrides.pop(get_current_user, None)


async def _insert_user(client, *, email, password, is_superadmin=False, is_active=True):
    async with client._test_maker() as session:
        user = User(
            email=email,
            password_hash=hash_password(password),
            is_superadmin=is_superadmin,
            is_active=is_active,
        )
        session.add(user)
        await session.commit()
        return user.id


def _make_workspace(client, tmp_path, key="MAP"):
    resp = client.post("/api/workspaces", json={"name": "Map", "key": key, "repo_path": str(tmp_path)})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- login / logout / me ---


def test_login_wrong_password_401(client, tmp_path):
    _real_auth(client)
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="owner@test.local", password="correct-password")
    )
    resp = client.post("/api/auth/login", json={"email": "owner@test.local", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


def test_login_unknown_email_401(client):
    _real_auth(client)
    resp = client.post("/api/auth/login", json={"email": "nobody@test.local", "password": "x"})
    assert resp.status_code == 401


def test_login_inactive_user_401(client):
    _real_auth(client)
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="gone@test.local", password="secret123", is_active=False)
    )
    resp = client.post("/api/auth/login", json={"email": "gone@test.local", "password": "secret123"})
    assert resp.status_code == 401


def test_login_success_sets_cookie_and_me_works(client):
    _real_auth(client)
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="owner@test.local", password="secret123")
    )
    resp = client.post("/api/auth/login", json={"email": "owner@test.local", "password": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "owner@test.local"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "owner@test.local"


def test_me_without_cookie_401(client):
    _real_auth(client)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_logout_clears_session(client):
    _real_auth(client)
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="owner@test.local", password="secret123")
    )
    client.post("/api/auth/login", json={"email": "owner@test.local", "password": "secret123"})
    assert client.get("/api/auth/me").status_code == 200

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


# --- bootstrap ---


def test_bootstrap_admin_creates_first_superadmin(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "ADMIN_EMAIL", "boss@test.local")
    monkeypatch.setattr(config.settings, "ADMIN_PASSWORD", "bootstrap-pw")

    import asyncio

    asyncio.get_event_loop().run_until_complete(bootstrap_admin(client._test_maker))

    _real_auth(client)
    resp = client.post("/api/auth/login", json={"email": "boss@test.local", "password": "bootstrap-pw"})
    assert resp.status_code == 200
    assert resp.json()["is_superadmin"] is True


def test_bootstrap_admin_noop_when_users_exist(client):
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="existing@test.local", password="x", is_superadmin=True)
    )
    asyncio.get_event_loop().run_until_complete(bootstrap_admin(client._test_maker))
    # No crash, and the pre-existing user is untouched — just assert it still works.
    _real_auth(client)
    resp = client.post("/api/auth/login", json={"email": "existing@test.local", "password": "x"})
    assert resp.status_code == 200


# --- global user management (superadmin only) ---


def test_create_user_requires_superadmin(client, tmp_path):
    _real_auth(client)
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="plain@test.local", password="secret123", is_superadmin=False)
    )
    client.post("/api/auth/login", json={"email": "plain@test.local", "password": "secret123"})

    resp = client.post("/api/users", json={"email": "new@test.local", "password": "secret123"})
    assert resp.status_code == 403


def test_create_user_as_superadmin_succeeds(client):
    resp = client.post("/api/users", json={"email": "new@test.local", "password": "secret123"})
    assert resp.status_code == 201
    assert resp.json()["email"] == "new@test.local"


def test_cannot_demote_last_superadmin(client):
    import asyncio

    # Log in as this user for real (instead of the blanket test-superadmin
    # override) so it's genuinely the only active superadmin in the DB.
    self_id = asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="only-admin@test.local", password="secret123", is_superadmin=True)
    )
    _real_auth(client)
    client.post("/api/auth/login", json={"email": "only-admin@test.local", "password": "secret123"})

    resp = client.patch(f"/api/users/{self_id}", json={"is_superadmin": False})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "last_superadmin"


# --- per-workspace RBAC ---


def test_workspace_creation_requires_superadmin_and_grants_admin_membership(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    # The blanket test superadmin created the workspace — should already be an
    # admin member of it (used by MembersCard's isWorkspaceAdmin check).
    resp = client.get(f"/api/workspaces/{ws_id}/members")
    assert resp.status_code == 200
    roles = {m["role"] for m in resp.json()}
    assert "admin" in roles


def test_viewer_can_read_but_not_write(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    import asyncio

    viewer_id = asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="viewer@test.local", password="secret123")
    )
    add = client.post(f"/api/workspaces/{ws_id}/members", json={"user_id": viewer_id, "role": "viewer"})
    assert add.status_code == 201

    _real_auth(client)
    client.post("/api/auth/login", json={"email": "viewer@test.local", "password": "secret123"})

    read = client.get(f"/api/workspaces/{ws_id}/tickets")
    assert read.status_code == 200

    write = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={"title": "t", "is_new_epic": True},
    )
    assert write.status_code == 403


def test_editor_can_write_but_not_manage_members(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    import asyncio

    editor_id = asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="editor@test.local", password="secret123")
    )
    client.post(f"/api/workspaces/{ws_id}/members", json={"user_id": editor_id, "role": "editor"})

    _real_auth(client)
    client.post("/api/auth/login", json={"email": "editor@test.local", "password": "secret123"})

    write = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={"title": "t", "is_new_epic": True},
    )
    assert write.status_code == 201

    members = client.post(
        f"/api/workspaces/{ws_id}/members", json={"user_id": editor_id, "role": "admin"}
    )
    assert members.status_code == 403


def test_non_member_gets_403(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="stranger@test.local", password="secret123")
    )

    _real_auth(client)
    client.post("/api/auth/login", json={"email": "stranger@test.local", "password": "secret123"})

    resp = client.get(f"/api/workspaces/{ws_id}/tickets")
    assert resp.status_code == 403


def test_superadmin_bypasses_membership(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    # The blanket override user is a superadmin with no explicit membership row
    # required — this exercises the same bypass a real superadmin login gets.
    resp = client.get(f"/api/workspaces/{ws_id}/tickets")
    assert resp.status_code == 200


def test_deactivation_takes_effect_on_next_request(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    import asyncio

    member_id = asyncio.get_event_loop().run_until_complete(
        _insert_user(client, email="member@test.local", password="secret123")
    )
    client.post(f"/api/workspaces/{ws_id}/members", json={"user_id": member_id, "role": "viewer"})

    _real_auth(client)
    client.post("/api/auth/login", json={"email": "member@test.local", "password": "secret123"})
    assert client.get("/api/auth/me").status_code == 200

    # Deactivate via a second, superadmin-authenticated request path.
    asyncio.get_event_loop().run_until_complete(
        _deactivate(client, member_id)
    )

    # Same cookie, still "valid" by signature, but the user is now inactive.
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


async def _deactivate(client, user_id):
    async with client._test_maker() as session:
        user = await session.get(User, user_id)
        user.is_active = False
        await session.commit()


# --- MCP server's internal bypass (real regression: MCP tool calls hit these
# same gated routes with no user session, per app/config.py's INTERNAL_MCP_SECRET) ---


def test_internal_secret_header_bypasses_login(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    _real_auth(client)

    resp = client.get(
        f"/api/workspaces/{ws_id}/tickets", headers={INTERNAL_SECRET_HEADER: INTERNAL_MCP_SECRET}
    )
    assert resp.status_code == 200


def test_wrong_internal_secret_still_401(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    _real_auth(client)

    resp = client.get(
        f"/api/workspaces/{ws_id}/tickets", headers={INTERNAL_SECRET_HEADER: "wrong-secret"}
    )
    assert resp.status_code == 401


def test_cannot_remove_last_workspace_admin(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    members = client.get(f"/api/workspaces/{ws_id}/members").json()
    admin_member = next(m for m in members if m["role"] == "admin")

    resp = client.delete(f"/api/workspaces/{ws_id}/members/{admin_member['id']}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "last_admin"
