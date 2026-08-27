"""Tests for GET /api/fs/browse — the onboarding wizard's repo-path folder picker."""

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


def test_browse_lists_subdirectories(client, tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "not-a-dir.txt").write_text("x")
    (tmp_path / ".hidden").mkdir()

    resp = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == str(tmp_path)
    names = [d["name"] for d in body["dirs"]]
    assert names == ["alpha", "beta"]
    assert body["dirs"][0]["path"] == str(tmp_path / "alpha")


def test_browse_reports_parent(client, tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    resp = client.get("/api/fs/browse", params={"path": str(child)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent"] == str(tmp_path)


def test_browse_root_has_no_parent(client):
    resp = client.get("/api/fs/browse", params={"path": "/"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent"] is None


def test_browse_defaults_to_home(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    resp = client.get("/api/fs/browse")
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == str(tmp_path)


def test_browse_nonexistent_path_404(client, tmp_path):
    resp = client.get("/api/fs/browse", params={"path": str(tmp_path / "nope")})
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


def test_browse_file_not_directory_404(client, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    resp = client.get("/api/fs/browse", params={"path": str(f)})
    assert resp.status_code == 404, resp.text
