"""API tests for MAP-006 workspace CRUD."""

import tempfile

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


def test_create_workspace_success(client, tmp_path):
    resp = client.post(
        "/api/workspaces",
        json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Acme"
    assert body["key"] == "ACM"
    assert body["ticket_counter"] == 0
    assert body["paused"] is False
    assert body["guardrails"] == {
        "run_timeout_sec": 1800,
        "max_cost_per_run": 2.0,
        "max_cost_per_ticket": 20.0,
        "max_handoff_depth": 12,
        "loop_threshold": 3,
        "max_concurrent_runs": 3,
    }


def test_create_duplicate_key_409(client, tmp_path):
    payload = {"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    resp1 = client.post("/api/workspaces", json=payload)
    assert resp1.status_code == 201

    resp2 = client.post("/api/workspaces", json=payload)
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "duplicate_key"


def test_create_bad_repo_path_422(client):
    resp = client.post(
        "/api/workspaces",
        json={"name": "Acme", "key": "ACM", "repo_path": "/definitely/not/a/real/path/xyz"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_repo_path"


def test_create_relative_repo_path_422(client):
    resp = client.post(
        "/api/workspaces",
        json={"name": "Acme", "key": "ACM", "repo_path": "relative/path"},
    )
    assert resp.status_code == 422


def test_create_invalid_key_format_422(client, tmp_path):
    resp = client.post(
        "/api/workspaces",
        json={"name": "Acme", "key": "acm", "repo_path": str(tmp_path)},
    )
    assert resp.status_code == 422
    # Pydantic validation errors must use the same uniform {"error": {...}} shape as AppError.
    body = resp.json()
    assert "error" in body
    assert "code" in body["error"] and "message" in body["error"]


def test_get_list_patch_delete_happy_path(client, tmp_path):
    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    get_resp = client.get(f"/api/workspaces/{ws_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == ws_id

    list_resp = client.get("/api/workspaces")
    assert list_resp.status_code == 200
    assert any(w["id"] == ws_id for w in list_resp.json())

    patch_resp = client.patch(f"/api/workspaces/{ws_id}", json={"name": "Acme Corp"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Acme Corp"

    patch_guardrails = client.patch(
        f"/api/workspaces/{ws_id}", json={"guardrails": {"max_cost_per_run": 5.0}}
    )
    assert patch_guardrails.status_code == 200
    assert patch_guardrails.json()["guardrails"] == {"max_cost_per_run": 5.0}

    delete_resp = client.delete(f"/api/workspaces/{ws_id}")
    assert delete_resp.status_code == 204

    get_after_delete = client.get(f"/api/workspaces/{ws_id}")
    assert get_after_delete.status_code == 404


def test_delete_does_not_touch_disk(client):
    with tempfile.TemporaryDirectory() as tmp_dir:
        import os

        marker = os.path.join(tmp_dir, "keep-me.txt")
        with open(marker, "w") as f:
            f.write("hello")

        create = client.post(
            "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": tmp_dir}
        )
        ws_id = create.json()["id"]

        delete_resp = client.delete(f"/api/workspaces/{ws_id}")
        assert delete_resp.status_code == 204

        assert os.path.isdir(tmp_dir)
        assert os.path.isfile(marker)
