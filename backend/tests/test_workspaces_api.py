"""API tests for MAP-006 workspace CRUD."""

import pathlib
import tempfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import session as db_session
from app.db.models import Agent, Base, Run, Ticket
from app.db.session import get_session
from app.main import app
from app.schemas.workspace import DEFAULT_WORKFLOW_PROMPT


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
    # main.py's lifespan calls recover_interrupted_runs(db_session.async_session) directly
    # (not through the get_session dependency override above) — without this, TestClient's
    # startup hits whatever DATABASE_URL resolves to by default, which fails if that DB
    # doesn't exist or isn't migrated yet.
    monkeypatch.setattr(db_session, "async_session", maker)
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
    assert body["timezone"] == "Asia/Jakarta"
    assert body["guardrails"] == {
        "run_timeout_sec": 1800,
        "max_cost_per_run": 2.0,
        "max_cost_per_ticket": 20.0,
        "max_handoff_depth": 1000,
        "loop_threshold": 3,
        "max_concurrent_runs": 3,
        "max_auto_retries": 3,
        "auto_check_interval_minutes": 3,
        "auto_check_stale_minutes": 3,
    }
    assert body["workflow_prompt"] == DEFAULT_WORKFLOW_PROMPT
    assert body["description"] is None


def test_created_at_is_utc_aware(client, tmp_path):
    # Regression: SQLite silently drops tzinfo on read for DateTime(timezone=True)
    # columns, so a naive value serializes without a UTC offset and the frontend's
    # `new Date(iso)` misreads it as local browser time instead of UTC.
    resp = client.post(
        "/api/workspaces",
        json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)},
    )
    assert resp.status_code == 201, resp.text
    created_at = datetime.fromisoformat(resp.json()["created_at"])
    assert created_at.tzinfo is not None


def test_create_workspace_with_description(client, tmp_path):
    resp = client.post(
        "/api/workspaces",
        json={
            "name": "Acme",
            "key": "ACM",
            "repo_path": str(tmp_path),
            "description": "Internal billing platform for Acme Corp.",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["description"] == "Internal billing platform for Acme Corp."


def test_update_workspace_description(client, tmp_path):
    resp = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = resp.json()["id"]

    resp = client.patch(f"/api/workspaces/{ws_id}", json={"description": "Updated context."})
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "Updated context."


def test_create_duplicate_key_409(client, tmp_path):
    payload = {"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    resp1 = client.post("/api/workspaces", json=payload)
    assert resp1.status_code == 201

    resp2 = client.post("/api/workspaces", json=payload)
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "duplicate_key"


def test_create_missing_absolute_repo_path_is_auto_created(client, tmp_path):
    target = tmp_path / "does" / "not" / "exist" / "yet"
    assert not target.exists()

    resp = client.post(
        "/api/workspaces",
        json={"name": "Acme", "key": "ACM", "repo_path": str(target)},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["repo_path"] == str(target)
    assert target.is_dir()


def test_create_workspace_with_clone_url(client, tmp_path, monkeypatch):
    target = tmp_path / "cloned-repo"
    calls = []

    def fake_clone_repo(url, target_dir):
        calls.append((url, target_dir))
        pathlib.Path(target_dir).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(target_dir) / "README.md").write_text("cloned")

    monkeypatch.setattr("app.api.workspaces.clone_repo", fake_clone_repo)

    resp = client.post(
        "/api/workspaces",
        json={
            "name": "Acme",
            "key": "ACM",
            "repo_path": str(target),
            "clone_url": "https://example.com/acme/repo.git",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["repo_path"] == str(target)
    assert calls == [("https://example.com/acme/repo.git", str(target))]
    assert (target / "README.md").exists()


def test_create_workspace_clone_failure_400(client, tmp_path, monkeypatch):
    from app.core.git import GitError

    def fake_clone_repo(url, target_dir):
        raise GitError("clone_failed", "fatal: repository not found")

    monkeypatch.setattr("app.api.workspaces.clone_repo", fake_clone_repo)

    resp = client.post(
        "/api/workspaces",
        json={
            "name": "Acme",
            "key": "ACM",
            "repo_path": str(tmp_path / "cloned-repo"),
            "clone_url": "https://example.com/nope/repo.git",
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "clone_failed"


def test_create_workspace_clone_into_nonempty_dir_422(client, tmp_path, monkeypatch):
    target = tmp_path / "cloned-repo"
    target.mkdir()
    (target / "existing.txt").write_text("already here")

    def fake_clone_repo(url, target_dir):
        raise AssertionError("clone_repo should not be called for a non-empty target")

    monkeypatch.setattr("app.api.workspaces.clone_repo", fake_clone_repo)

    resp = client.post(
        "/api/workspaces",
        json={
            "name": "Acme",
            "key": "ACM",
            "repo_path": str(target),
            "clone_url": "https://example.com/acme/repo.git",
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "invalid_repo_path"


def test_create_repo_path_that_is_a_file_422(client, tmp_path):
    bad = tmp_path / "not-a-dir"
    bad.write_text("x")

    resp = client.post(
        "/api/workspaces",
        json={"name": "Acme", "key": "ACM", "repo_path": str(bad)},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_repo_path"


def test_create_empty_repo_path_422(client):
    resp = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": "   "}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_repo_path"


def test_create_repo_path_dot_422(client):
    resp = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": ".."}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_repo_path"


def test_create_relative_repo_path_collides_with_file_422(client, tmp_path):
    import shutil
    from pathlib import Path

    name = "collide-workspace"
    target = Path("workspaces") / name
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("i am a file")
    try:
        resp = client.post(
            "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": name}
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "invalid_repo_path"
    finally:
        target.unlink(missing_ok=True)


def test_workflow_prompt_default(client):
    resp = client.get("/api/workspaces/workflow-prompt-default")
    assert resp.status_code == 200
    assert resp.json()["workflow_prompt"] == DEFAULT_WORKFLOW_PROMPT


def test_update_workspace_repo_path_and_time_unit(client, tmp_path):
    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    new_dir = tmp_path / "new-repo"
    resp = client.patch(f"/api/workspaces/{ws_id}", json={"repo_path": str(new_dir)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["repo_path"] == str(new_dir)
    assert new_dir.is_dir()

    resp = client.patch(f"/api/workspaces/{ws_id}", json={"time_unit": "hour"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["time_unit"] == "hour"

    resp = client.patch(
        f"/api/workspaces/{ws_id}",
        json={"workflow_prompt": "custom prompt", "sprint_creator_roles": ["pm", "lead"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["workflow_prompt"] == "custom prompt"
    assert resp.json()["sprint_creator_roles"] == ["pm", "lead"]


def test_create_relative_repo_path_created_under_workspaces_dir(client):
    import shutil
    from pathlib import Path

    name = "test-bare-name-workspace"
    target = Path("workspaces") / name
    shutil.rmtree(target, ignore_errors=True)
    try:
        resp = client.post(
            "/api/workspaces",
            json={"name": "Acme", "key": "ACM", "repo_path": name},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["repo_path"] == str(target.resolve())
        assert target.is_dir()
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_create_repo_path_traversal_flattened(client):
    import shutil
    from pathlib import Path

    resp = client.post(
        "/api/workspaces",
        json={"name": "Acme", "key": "ACM", "repo_path": "../../etc/passwd-lookalike"},
    )
    assert resp.status_code == 201, resp.text
    target = Path("workspaces") / "passwd-lookalike"
    try:
        assert resp.json()["repo_path"] == str(target.resolve())
        assert target.is_dir()
        # never escaped workspaces/
        assert Path("workspaces").resolve() in Path(resp.json()["repo_path"]).parents
    finally:
        shutil.rmtree(target, ignore_errors=True)


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

    patch_timezone = client.patch(
        f"/api/workspaces/{ws_id}", json={"timezone": "Asia/Makassar"}
    )
    assert patch_timezone.status_code == 200
    assert patch_timezone.json()["timezone"] == "Asia/Makassar"

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


def test_reset_requires_paused_409(client, tmp_path):
    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]
    client.post(f"/api/workspaces/{ws_id}/tickets", json={"title": "t1", "is_new_epic": True})

    resp = client.post(f"/api/workspaces/{ws_id}/reset")
    assert resp.status_code == 409


def test_reset_wipes_tickets_comments_sprints_and_counter(client, tmp_path):
    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    ticket = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": "t1", "is_new_epic": True}
    ).json()
    client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "hi"})
    client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"})

    pause_resp = client.post(f"/api/workspaces/{ws_id}/pause")
    assert pause_resp.status_code == 200

    reset_resp = client.post(f"/api/workspaces/{ws_id}/reset")
    assert reset_resp.status_code == 200
    body = reset_resp.json()
    assert body["ticket_counter"] == 0
    assert body["paused"] is True

    assert client.get(f"/api/workspaces/{ws_id}/tickets").json() == []
    assert client.get(f"/api/workspaces/{ws_id}/sprints").json() == []
    assert client.get(f"/api/tickets/{ticket['key']}").status_code == 404

    # counter restarted: the next ticket gets -001 again, not -002
    new_ticket = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": "fresh", "is_new_epic": True}
    ).json()
    assert new_ticket["key"] == "ACM-001"


def test_terminate_deletes_workspace_and_all_data(client, tmp_path):
    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    ticket = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": "t1", "is_new_epic": True}
    ).json()
    client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "hi"})
    client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"})

    resp = client.post(f"/api/workspaces/{ws_id}/terminate")
    assert resp.status_code == 204

    assert client.get(f"/api/workspaces/{ws_id}").status_code == 404
    assert client.get(f"/api/workspaces/{ws_id}/tickets").status_code == 404
    assert client.get(f"/api/workspaces/{ws_id}/agents").status_code == 404
    assert client.get(f"/api/tickets/{ticket['key']}").status_code == 404


def test_terminate_does_not_touch_disk(client):
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        marker = os.path.join(tmp_dir, "keep-me.txt")
        with open(marker, "w") as f:
            f.write("hello")

        create = client.post(
            "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": tmp_dir}
        )
        ws_id = create.json()["id"]

        resp = client.post(f"/api/workspaces/{ws_id}/terminate")
        assert resp.status_code == 204

        assert os.path.isdir(tmp_dir)
        assert os.path.isfile(marker)


def test_terminate_missing_workspace_404(client):
    resp = client.post("/api/workspaces/nope/terminate")
    assert resp.status_code == 404


def test_pause_with_running_run_cancels_and_resume(client, tmp_path, monkeypatch):
    import asyncio

    from app.core import orchestrator

    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    async def _seed_running_run():
        async with db_session.async_session() as s:
            agent = Agent(
                workspace_id=ws_id, name="eng", role="engineer",
                model="opencode/big-pickle", tool_kind="opencode", status="working",
            )
            s.add(agent)
            await s.flush()
            ticket = Ticket(
                workspace_id=ws_id, key="ACM-1", title="t", status="in_progress",
            )
            s.add(ticket)
            await s.flush()
            run = Run(
                ticket_id=ticket.id, agent_id=agent.id, status="running",
                trigger="manual", tool_kind="opencode", model="opencode/big-pickle",
            )
            s.add(run)
            await s.commit()
            return run.id

    run_id = asyncio.run(_seed_running_run())
    stopped = []
    async def _fake_stop(run_id):
        stopped.append(run_id)
        return True
    monkeypatch.setattr(orchestrator, "stop", _fake_stop)

    resp = client.post(f"/api/workspaces/{ws_id}/pause")
    assert resp.status_code == 200, resp.text
    assert resp.json()["paused"] is True
    assert stopped == [run_id]

    resp = client.post(f"/api/workspaces/{ws_id}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["paused"] is False


def test_reset_with_running_run_409(client, tmp_path, monkeypatch):
    import asyncio

    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    async def _seed_running_run():
        async with db_session.async_session() as s:
            agent = Agent(
                workspace_id=ws_id, name="eng", role="engineer",
                model="opencode/big-pickle", tool_kind="opencode", status="working",
            )
            s.add(agent)
            await s.flush()
            ticket = Ticket(
                workspace_id=ws_id, key="ACM-1", title="t", status="in_progress",
            )
            s.add(ticket)
            await s.flush()
            run = Run(
                ticket_id=ticket.id, agent_id=agent.id, status="running",
                trigger="manual", tool_kind="opencode", model="opencode/big-pickle",
            )
            s.add(run)
            await s.commit()

    asyncio.run(_seed_running_run())
    client.post(f"/api/workspaces/{ws_id}/pause")

    resp = client.post(f"/api/workspaces/{ws_id}/reset")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "runs_in_progress"


def test_pause_with_queued_run_cancels(client, tmp_path, monkeypatch):
    import asyncio

    from app.core import orchestrator

    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    async def _seed_queued_run():
        async with db_session.async_session() as s:
            agent = Agent(
                workspace_id=ws_id, name="eng", role="engineer",
                model="opencode/big-pickle", tool_kind="opencode", status="idle",
            )
            s.add(agent)
            await s.flush()
            ticket = Ticket(
                workspace_id=ws_id, key="ACM-1", title="t", status="in_progress",
            )
            s.add(ticket)
            await s.flush()
            run = Run(
                ticket_id=ticket.id, agent_id=agent.id, status="queued",
                trigger="manual", tool_kind="opencode", model="opencode/big-pickle",
            )
            s.add(run)
            await s.commit()
            return run.id

    run_id = asyncio.run(_seed_queued_run())
    cancelled = []
    async def _fake_cancel(agent_id, run_id):
        cancelled.append(run_id)
        return True
    monkeypatch.setattr(orchestrator, "cancel_queued", _fake_cancel)

    resp = client.post(f"/api/workspaces/{ws_id}/pause")
    assert resp.status_code == 200, resp.text
    assert cancelled == [run_id]


def test_terminate_with_queued_run_cancels(client, tmp_path, monkeypatch):
    import asyncio

    from app.core import orchestrator

    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    async def _seed_queued_run():
        async with db_session.async_session() as s:
            agent = Agent(
                workspace_id=ws_id, name="eng", role="engineer",
                model="opencode/big-pickle", tool_kind="opencode", status="idle",
            )
            s.add(agent)
            await s.flush()
            ticket = Ticket(
                workspace_id=ws_id, key="ACM-1", title="t", status="in_progress",
            )
            s.add(ticket)
            await s.flush()
            run = Run(
                ticket_id=ticket.id, agent_id=agent.id, status="queued",
                trigger="manual", tool_kind="opencode", model="opencode/big-pickle",
            )
            s.add(run)
            await s.commit()

    asyncio.run(_seed_queued_run())
    cancelled = []
    async def _fake_cancel(agent_id, run_id):
        cancelled.append(run_id)
        return True
    monkeypatch.setattr(orchestrator, "cancel_queued", _fake_cancel)

    resp = client.post(f"/api/workspaces/{ws_id}/terminate")
    assert resp.status_code == 204, resp.text
    assert len(cancelled) == 1


def test_terminate_waits_for_running_run_then_succeeds(client, tmp_path, monkeypatch):
    import asyncio

    from app.core import orchestrator

    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    async def _seed_running_run():
        async with db_session.async_session() as s:
            agent = Agent(
                workspace_id=ws_id, name="eng", role="engineer",
                model="opencode/big-pickle", tool_kind="opencode", status="working",
            )
            s.add(agent)
            await s.flush()
            ticket = Ticket(
                workspace_id=ws_id, key="ACM-1", title="t", status="in_progress",
            )
            s.add(ticket)
            await s.flush()
            run = Run(
                ticket_id=ticket.id, agent_id=agent.id, status="running",
                trigger="manual", tool_kind="opencode", model="opencode/big-pickle",
            )
            s.add(run)
            await s.commit()
            return run.id

    run_id = asyncio.run(_seed_running_run())

    # Simulate the run finishing shortly after terminate starts polling.
    async def _fake_stop(run_id):
        async def _finish():
            await asyncio.sleep(0.2)
            async with db_session.async_session() as s:
                r = await s.get(Run, run_id)
                r.status = "cancelled"
                await s.commit()
        asyncio.create_task(_finish())
        return True

    monkeypatch.setattr(orchestrator, "stop", _fake_stop)

    resp = client.post(f"/api/workspaces/{ws_id}/terminate")
    assert resp.status_code == 204
    assert client.get(f"/api/workspaces/{ws_id}").status_code == 404


def test_terminate_timeout_409_keeps_workspace(client, tmp_path, monkeypatch):
    import asyncio

    from app.core import orchestrator

    create = client.post(
        "/api/workspaces", json={"name": "Acme", "key": "ACM", "repo_path": str(tmp_path)}
    )
    ws_id = create.json()["id"]

    async def _seed_running_run():
        async with db_session.async_session() as s:
            agent = Agent(
                workspace_id=ws_id, name="eng", role="engineer",
                model="opencode/big-pickle", tool_kind="opencode", status="working",
            )
            s.add(agent)
            await s.flush()
            ticket = Ticket(
                workspace_id=ws_id, key="ACM-1", title="t", status="in_progress",
            )
            s.add(ticket)
            await s.flush()
            run = Run(
                ticket_id=ticket.id, agent_id=agent.id, status="running",
                trigger="manual", tool_kind="opencode", model="opencode/big-pickle",
            )
            s.add(run)
            await s.commit()

    asyncio.run(_seed_running_run())

    # The run never actually stops — terminate must time out and leave the
    # workspace paused and intact. Shrink the timeout so the test is fast.
    import app.api.workspaces as ws_api

    monkeypatch.setattr(ws_api, "_TERMINATE_TIMEOUT_SEC", 0.5)

    async def _never_stop(run_id):
        return True

    monkeypatch.setattr(orchestrator, "stop", _never_stop)

    resp = client.post(f"/api/workspaces/{ws_id}/terminate")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "runs_in_progress"

    ws = client.get(f"/api/workspaces/{ws_id}").json()
    assert ws["paused"] is True
    assert client.get(f"/api/workspaces/{ws_id}/tickets").json() != []
