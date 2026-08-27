"""API tests for MAP-007 GET /api/models."""

import os
import stat
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.models as models_mod
from app.config import settings
from app.db import session as db_session
from app.db.models import Base
from app.main import app


def _write_script(path, body):
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture(autouse=True)
def _reset_cache():
    models_mod._cache = None
    yield
    models_mod._cache = None


@pytest.fixture
async def client(monkeypatch):
    # This module's own tests don't touch the DB, but main.py's lifespan always calls
    # recover_interrupted_runs(db_session.async_session) on TestClient startup — point it
    # at a throwaway migrated engine instead of whatever DATABASE_URL resolves to by default.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(db_session, "async_session", async_sessionmaker(engine, expire_on_commit=False))
    with TestClient(app) as c:
        yield c
    await engine.dispose()


def test_successful_parse(tmp_path, monkeypatch, client):
    script = _write_script(
        tmp_path / "opencode",
        'printf "opencode/big-pickle\\nollama/qwen3-coder:480b-cloud\\n\\n"',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.get("/api/models")

    assert resp.status_code == 200
    assert resp.json() == ["opencode/big-pickle", "ollama/qwen3-coder:480b-cloud"]


def test_nonzero_exit_returns_503(tmp_path, monkeypatch, client):
    script = _write_script(tmp_path / "opencode", "exit 1")
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.get("/api/models")

    assert resp.status_code == 503
    assert "opencode auth login" in resp.json()["error"]["message"]


def test_empty_output_returns_503(tmp_path, monkeypatch, client):
    script = _write_script(tmp_path / "opencode", "printf ''")
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.get("/api/models")

    assert resp.status_code == 503
    assert "opencode auth login" in resp.json()["error"]["message"]


def test_binary_not_found_returns_503(monkeypatch, client):
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-binary")

    resp = client.get("/api/models")

    assert resp.status_code == 503
    assert "opencode auth login" in resp.json()["error"]["message"]


def test_timeout_returns_503(tmp_path, monkeypatch, client):
    monkeypatch.setattr(subprocess, "run", _raise_timeout)

    resp = client.get("/api/models")

    assert resp.status_code == 503
    assert "opencode auth login" in resp.json()["error"]["message"]


def _raise_timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(cmd="opencode models", timeout=30)


def test_default_model_reads_opencode_config(tmp_path, monkeypatch, client):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_dir = tmp_path / "opencode"
    config_dir.mkdir()
    (config_dir / "opencode.json").write_text('{"model": "ollama-cloud/deepseek-v4-flash:0731"}')

    resp = client.get("/api/models/default")

    assert resp.status_code == 200
    assert resp.json() == {"model": "ollama-cloud/deepseek-v4-flash:0731"}


def test_default_model_missing_config_returns_null(tmp_path, monkeypatch, client):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    resp = client.get("/api/models/default")

    assert resp.status_code == 200
    assert resp.json() == {"model": None}


def test_default_model_malformed_json_returns_null(tmp_path, monkeypatch, client):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_dir = tmp_path / "opencode"
    config_dir.mkdir()
    (config_dir / "opencode.json").write_text("{not valid json")

    resp = client.get("/api/models/default")

    assert resp.status_code == 200
    assert resp.json() == {"model": None}


def test_default_model_blank_model_returns_null(tmp_path, monkeypatch, client):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_dir = tmp_path / "opencode"
    config_dir.mkdir()
    (config_dir / "opencode.json").write_text('{"model": "   "}')

    resp = client.get("/api/models/default")

    assert resp.status_code == 200
    assert resp.json() == {"model": None}


def test_cache_avoids_second_invocation(tmp_path, monkeypatch, client):
    counter_file = tmp_path / "count"
    script = _write_script(
        tmp_path / "opencode",
        f'echo x >> "{counter_file}"\nprintf "opencode/big-pickle\\n"',
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    first = client.get("/api/models")
    second = client.get("/api/models")

    assert first.status_code == 200
    assert second.status_code == 200
    assert os.path.exists(counter_file)
    assert counter_file.read_text().count("x") == 1
