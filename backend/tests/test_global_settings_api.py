"""API tests for the global orchestrator default model setting (GET/PUT /api/settings/orchestrator-model)."""

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


def test_default_is_none(client):
    resp = client.get("/api/settings/orchestrator-model")
    assert resp.status_code == 200
    assert resp.json() == {"model": None}


def test_put_then_get(client):
    put = client.put("/api/settings/orchestrator-model", json={"model": "opencode/big-pickle"})
    assert put.status_code == 200
    assert put.json() == {"model": "opencode/big-pickle"}

    get = client.get("/api/settings/orchestrator-model")
    assert get.status_code == 200
    assert get.json() == {"model": "opencode/big-pickle"}


def test_put_overwrites(client):
    client.put("/api/settings/orchestrator-model", json={"model": "opencode/a"})
    put = client.put("/api/settings/orchestrator-model", json={"model": "ollama/qwen3-coder"})
    assert put.json() == {"model": "ollama/qwen3-coder"}


def test_put_null_clears(client):
    client.put("/api/settings/orchestrator-model", json={"model": "opencode/a"})
    put = client.put("/api/settings/orchestrator-model", json={"model": None})
    assert put.json() == {"model": None}

    get = client.get("/api/settings/orchestrator-model")
    assert get.json() == {"model": None}


def test_put_empty_string_clears(client):
    client.put("/api/settings/orchestrator-model", json={"model": "opencode/a"})
    put = client.put("/api/settings/orchestrator-model", json={"model": "  "})
    assert put.json() == {"model": None}
