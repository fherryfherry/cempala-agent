"""Tests for the Artifacts menu: agent-declared ```map `artifacts:` -> published

Attachment (origin="agent") grouped by ArtifactGroup, and GET /workspaces/{id}/artifacts.
Same fake-opencode-binary technique as test_orchestrator.py.
"""

import stat
import time
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import session as db_session
from app.db.models import Base
from app.db.session import get_session
from app.main import app


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))

    db_path = tmp_path / f"{uuid.uuid4().hex}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

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
    monkeypatch.setattr(db_session, "async_session", maker)

    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


def _write_python_binary(path, code):
    path.write_text(f"#!/usr/bin/env python3\n{code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _make_workspace(client, repo_dir, key="MAP"):
    resp = client.post(
        "/api/workspaces", json={"name": "Map", "key": key, "repo_path": str(repo_dir)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_agent(client, ws_id, role, name):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": "opencode/big-pickle", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_ticket(client, ws_id, title="Do the thing"):
    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": title, "is_new_epic": True}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_status(client, key, status):
    resp = client.patch(f"/api/tickets/{key}", json={"status": status})
    assert resp.status_code == 200, resp.text


def _wait_for_run(client, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.03)
    raise TimeoutError(f"run {run_id} did not reach a terminal state within {timeout}s")


def _map_script(artifacts_yaml: str) -> str:
    return f'''
import json
text = """Done.

```map
status: review
mention: []
summary: |
  published artifacts
{artifacts_yaml}
```
"""
print(json.dumps({{
    "type": "assistant_text",
    "text": text,
    "session_id": "sess-artifacts",
}}))
'''


def test_artifact_escaping_repo_path_is_skipped(client, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")

    ws_id = _make_workspace(client, repo_dir)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(
        tmp_path / "opencode",
        _map_script(
            "artifacts:\n"
            "  - path: ../secret.txt\n"
            "    group: Dokumen Teknis\n"
        ),
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final

    artifacts = client.get(f"/api/workspaces/{ws_id}/artifacts").json()
    assert artifacts == []

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    bodies = [c["body"] for c in detail["comments"]]
    assert any("diabaikan" in b and "secret.txt" in b for b in bodies)


def test_missing_artifact_file_is_skipped(client, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    ws_id = _make_workspace(client, repo_dir)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(
        tmp_path / "opencode",
        _map_script(
            "artifacts:\n"
            "  - path: docs/does-not-exist.md\n"
            "    group: Dokumen Teknis\n"
        ),
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final

    artifacts = client.get(f"/api/workspaces/{ws_id}/artifacts").json()
    assert artifacts == []


def test_valid_artifact_published_end_to_end(client, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "docs").mkdir()
    (repo_dir / "docs" / "PRD.md").write_text("# PRD\n\ngoals...")

    ws_id = _make_workspace(client, repo_dir)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(
        tmp_path / "opencode",
        _map_script(
            "artifacts:\n"
            "  - path: docs/PRD.md\n"
            "    group: Dokumen Teknis\n"
            "    description: initial PRD\n"
        ),
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    final = _wait_for_run(client, run["id"])
    assert final["status"] == "done", final
    assert final["report"]["artifacts"] == [{"path": "docs/PRD.md", "group": "Dokumen Teknis"}]

    groups = client.get(f"/api/workspaces/{ws_id}/artifacts").json()
    assert len(groups) == 1
    assert groups[0]["name"] == "Dokumen Teknis"
    assert len(groups[0]["attachments"]) == 1
    attachment = groups[0]["attachments"][0]
    assert attachment["filename"] == "PRD.md"
    assert attachment["origin"] == "agent"
    assert attachment["description"] == "initial PRD"
    assert attachment["ticket_key"] == ticket["key"]

    download = client.get(f"/api/attachments/{attachment['id']}")
    assert download.status_code == 200
    assert download.content == b"# PRD\n\ngoals..."

    # ticket detail also lists it as a regular attachment
    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    assert any(a["id"] == attachment["id"] for a in detail["attachments"])


def test_manual_upload_excluded_from_artifacts_listing(client, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    ws_id = _make_workspace(client, repo_dir)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(
        f"/api/tickets/{ticket['key']}/attachments",
        files={"file": ("context.txt", b"human context", "text/plain")},
    )
    assert resp.status_code == 201, resp.text

    groups = client.get(f"/api/workspaces/{ws_id}/artifacts").json()
    assert groups == []


def test_reset_workspace_clears_artifact_groups(client, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "docs").mkdir()
    (repo_dir / "docs" / "PRD.md").write_text("# PRD")

    ws_id = _make_workspace(client, repo_dir)
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    ticket = _make_ticket(client, ws_id)
    _set_status(client, ticket["key"], "todo")

    script = _write_python_binary(
        tmp_path / "opencode",
        _map_script("artifacts:\n  - path: docs/PRD.md\n    group: Dokumen Teknis\n"),
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(f"/api/tickets/{ticket['key']}/run", json={"agent_id": pm_id})
    run = resp.json()
    _wait_for_run(client, run["id"])

    assert len(client.get(f"/api/workspaces/{ws_id}/artifacts").json()) == 1

    assert client.post(f"/api/workspaces/{ws_id}/pause").status_code == 200
    assert client.post(f"/api/workspaces/{ws_id}/reset").status_code == 200

    assert client.get(f"/api/workspaces/{ws_id}/artifacts").json() == []
