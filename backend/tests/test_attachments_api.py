"""API tests for MAP-011 attachment upload/download/delete."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base
from app.db.session import get_session
from app.main import app


@pytest.fixture
async def client(tmp_path, monkeypatch):
    # Hermetic storage dir per test — never touch the real repo storage/attachments/.
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


def _make_workspace(client, tmp_path, key="MAP"):
    resp = client.post(
        "/api/workspaces", json={"name": "Map", "key": key, "repo_path": str(tmp_path)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_ticket(client, ws_id, title="Do the thing"):
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json={"title": title})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_upload_success_and_file_on_disk(client, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    ws_id = _make_workspace(client, repo_dir)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(
        f"/api/tickets/{ticket['key']}/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "notes.txt"
    assert body["content_type"] == "text/plain"
    assert body["size_bytes"] == len(b"hello world")

    storage_dir = (tmp_path / "storage").resolve()
    on_disk = storage_dir / body["path"]
    assert on_disk.is_file()
    assert on_disk.read_bytes() == b"hello world"
    # sanitized-on-disk name: <uuid>-<basename>, not the original alone
    assert on_disk.name != "notes.txt"
    assert on_disk.name.endswith("-notes.txt")
    assert on_disk.parent.name == ticket["id"]


def test_path_traversal_filename_sanitized(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(
        f"/api/tickets/{ticket['key']}/attachments",
        files={"file": ("../../etc/passwd", b"pwned", "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    storage_dir = (tmp_path / "storage").resolve()
    attachments_dir = storage_dir / "attachments"
    on_disk = storage_dir / body["path"]

    # flattened: no separators, no ".." components in the on-disk name
    assert "/" not in on_disk.name
    assert ".." not in on_disk.name.split("-", 1)[-1].split("/")
    assert on_disk.name.endswith("-passwd")
    # and it never escapes the attachments dir
    assert on_disk.resolve().is_relative_to(attachments_dir.resolve())
    # original filename preserved in DB for display/download purposes
    assert body["filename"] == "../../etc/passwd"


def test_file_stored_outside_repo_path(client, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    ws_id = _make_workspace(client, repo_dir)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(
        f"/api/tickets/{ticket['key']}/attachments",
        files={"file": ("a.txt", b"data", "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    storage_attachments_dir = (tmp_path / "storage" / "attachments").resolve()
    on_disk = (tmp_path / "storage" / body["path"]).resolve()

    assert str(on_disk).startswith(str(storage_attachments_dir))
    assert not str(on_disk).startswith(str(repo_dir.resolve()))


def test_oversized_upload_413(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)

    big = b"x" * (25 * 1024 * 1024 + 1)
    resp = client.post(
        f"/api/tickets/{ticket['key']}/attachments",
        files={"file": ("big.bin", big, "application/octet-stream")},
    )
    assert resp.status_code == 413, resp.text

    # no partial file left behind under the ticket's attachment dir
    ticket_dir = tmp_path / "storage" / "attachments" / ticket["id"]
    if ticket_dir.exists():
        assert list(ticket_dir.iterdir()) == []


def test_download_original_filename_and_bytes(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(
        f"/api/tickets/{ticket['key']}/attachments",
        files={"file": ("report.csv", b"a,b,c\n1,2,3", "text/csv")},
    )
    attachment_id = resp.json()["id"]

    resp = client.get(f"/api/attachments/{attachment_id}")
    assert resp.status_code == 200
    assert resp.content == b"a,b,c\n1,2,3"
    assert "report.csv" in resp.headers["content-disposition"]
    assert resp.headers["content-type"].startswith("text/csv")


def test_delete_removes_db_row_and_file(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = _make_ticket(client, ws_id)

    resp = client.post(
        f"/api/tickets/{ticket['key']}/attachments",
        files={"file": ("bye.txt", b"gone soon", "text/plain")},
    )
    attachment_id = resp.json()["id"]
    on_disk = (tmp_path / "storage" / resp.json()["path"]).resolve()
    assert on_disk.is_file()

    resp = client.delete(f"/api/attachments/{attachment_id}")
    assert resp.status_code == 204

    assert not on_disk.exists()
    assert client.get(f"/api/attachments/{attachment_id}").status_code == 404


def test_attachment_on_nonexistent_ticket_404(client):
    resp = client.post(
        "/api/tickets/MAP-999/attachments",
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 404


def test_get_delete_nonexistent_attachment_404(client):
    assert client.get("/api/attachments/does-not-exist").status_code == 404
    assert client.delete("/api/attachments/does-not-exist").status_code == 404
