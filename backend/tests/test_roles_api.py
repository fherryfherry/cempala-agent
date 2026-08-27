"""API tests for the global role CRUD (docs/superpowers/specs/
2026-08-27-dynamic-roles-design.md): list/create/update/delete, the builtin
guards (undeletable, pm flags immutable), key immutability, and the
agent-in-use deletion block."""

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


def _role_payload(**overrides):
    payload = {
        "key": "scrum_master",
        "name": "Scrum Master",
        "description": "Owns sprint ceremonies.",
        "system_prompt": "Kamu Scrum Master.",
        "may_declare_tickets": False,
        "may_manage_artifacts": False,
        "is_reviewer": False,
    }
    payload.update(overrides)
    return payload


def test_list_roles_returns_8_builtins(client):
    resp = client.get("/api/roles")
    assert resp.status_code == 200
    roles = resp.json()
    assert len(roles) == 8
    by_key = {r["key"]: r for r in roles}
    assert set(by_key) == {
        "pm", "lead", "engineer", "designer", "qa", "pentester",
        "business_analyst", "system_architect",
    }
    pm = by_key["pm"]
    assert pm["is_builtin"] is True
    assert pm["may_declare_tickets"] is True
    assert pm["may_manage_artifacts"] is True
    assert pm["is_reviewer"] is False
    assert pm["name"] == "Project Manager"
    assert pm["system_prompt"] is not None
    assert pm["agent_count"] == 0
    assert by_key["engineer"]["may_declare_tickets"] is False


def test_create_role_success(client):
    resp = client.post("/api/roles", json=_role_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["key"] == "scrum_master"
    assert body["name"] == "Scrum Master"
    assert body["is_builtin"] is False
    assert body["agent_count"] == 0

    listed = client.get("/api/roles").json()
    assert len(listed) == 9


def test_create_role_with_flags(client):
    payload = _role_payload(
        may_declare_tickets=True,
        may_manage_artifacts=True,
        is_reviewer=True,
        description=None,
        system_prompt=None,
    )
    resp = client.post("/api/roles", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["may_declare_tickets"] is True
    assert body["may_manage_artifacts"] is True
    assert body["is_reviewer"] is True
    assert body["description"] is None
    assert body["system_prompt"] is None


def test_create_duplicate_key_409(client):
    client.post("/api/roles", json=_role_payload())
    resp = client.post("/api/roles", json=_role_payload())
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_key"


def test_create_key_with_spaces_422(client):
    resp = client.post("/api/roles", json=_role_payload(key="scrum master"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_create_key_uppercase_422(client):
    resp = client.post("/api/roles", json=_role_payload(key="ScrumMaster"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_create_key_starting_with_digit_422(client):
    resp = client.post("/api/roles", json=_role_payload(key="2scrum"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_update_edits_fields_but_not_key(client):
    created = client.post("/api/roles", json=_role_payload()).json()
    resp = client.patch(
        f"/api/roles/{created['key']}",
        json={
            "name": "Scrum Master v2",
            "description": "Updated.",
            "system_prompt": "Kamu Scrum Master v2.",
            "may_declare_tickets": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == "scrum_master"
    assert body["name"] == "Scrum Master v2"
    assert body["description"] == "Updated."
    assert body["system_prompt"] == "Kamu Scrum Master v2."
    assert body["may_declare_tickets"] is True


def test_update_key_in_body_ignored(client):
    created = client.post("/api/roles", json=_role_payload()).json()
    resp = client.patch(
        f"/api/roles/{created['key']}", json={"key": "renamed_key", "name": "X"}
    )
    assert resp.status_code == 200
    assert resp.json()["key"] == "scrum_master"
    assert resp.json()["name"] == "X"


def test_update_clears_prompt_with_explicit_null(client):
    created = client.post("/api/roles", json=_role_payload()).json()
    resp = client.patch(f"/api/roles/{created['key']}", json={"system_prompt": None})
    assert resp.status_code == 200
    assert resp.json()["system_prompt"] is None


def test_delete_custom_role(client):
    created = client.post("/api/roles", json=_role_payload()).json()
    resp = client.delete(f"/api/roles/{created['key']}")
    assert resp.status_code == 204
    assert len(client.get("/api/roles").json()) == 8


def test_delete_builtin_role_403(client):
    resp = client.delete("/api/roles/pm")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "builtin_role"


def test_delete_role_in_use_409(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    created = client.post("/api/roles", json=_role_payload()).json()
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={
            "name": "Alice",
            "role": created["key"],
            "model": "claude-sonnet",
            "tool_kind": "opencode",
        },
    )
    assert resp.status_code == 201, resp.text

    delete = client.delete(f"/api/roles/{created['key']}")
    assert delete.status_code == 409
    assert delete.json()["error"]["code"] == "role_in_use"

    # agent_count shows up in the list
    listed = {r["key"]: r for r in client.get("/api/roles").json()}
    assert listed[created["key"]]["agent_count"] == 1


def test_delete_role_404(client):
    resp = client.delete("/api/roles/does_not_exist")
    assert resp.status_code == 404


def test_pm_flags_immutable_403(client):
    resp = client.patch("/api/roles/pm", json={"may_declare_tickets": False})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "pm_flags_locked"


def test_pm_edit_round_trip_current_flag_values_allowed(client):
    """The settings UI's edit form always submits the PM's current flag values
    (the checkboxes are disabled but still mounted). Saving the same values must
    not trip the immutability guard — only an actual change is a 403."""
    resp = client.patch(
        "/api/roles/pm",
        json={
            "name": "Project Manager v2",
            "may_declare_tickets": True,
            "may_manage_artifacts": True,
            "is_reviewer": False,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Project Manager v2"
    assert body["may_declare_tickets"] is True
    assert body["may_manage_artifacts"] is True
    assert body["is_reviewer"] is False


def test_pm_prompt_editable_but_flags_locked(client):
    resp = client.patch(
        "/api/roles/pm", json={"name": "Project Manager v2", "system_prompt": "new pm prompt"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Project Manager v2"
    assert body["system_prompt"] == "new pm prompt"
    assert body["may_manage_artifacts"] is True
    assert body["may_declare_tickets"] is True


def test_other_builtin_roles_editable_including_flags(client):
    resp = client.patch(
        "/api/roles/lead",
        json={"name": "Tech Lead", "may_declare_tickets": True, "is_reviewer": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Tech Lead"
    assert body["may_declare_tickets"] is True
    assert body["is_reviewer"] is False
