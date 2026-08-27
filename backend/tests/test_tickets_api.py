"""API tests for MAP-009 ticket CRUD + key numbering."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
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
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    # main.py's lifespan calls recover_interrupted_runs(db_session.async_session) directly,
    # bypassing the get_session override above — point it at this test's engine too.
    monkeypatch.setattr(db_session, "async_session", maker)
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


def _ticket_payload(title="Do the thing", **overrides):
    payload = {"title": title, "description": "desc", "is_new_epic": True}
    payload.update(overrides)
    if "parent_id" in overrides:
        payload.pop("is_new_epic", None)
    return payload


def test_create_ticket_without_parent_or_epic_flag_422(client, tmp_path):
    # Every ticket needs an epic: parent_id or an explicit is_new_epic=True opt-in.
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json={"title": "orphan"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "epic_required"


def test_create_ticket_with_both_parent_and_epic_flag_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    epic = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": "epic", "is_new_epic": True}
    ).json()
    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={"title": "confused", "parent_id": epic["id"], "is_new_epic": True},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_epic_flag"


def test_create_ticket_with_parent_id_succeeds_without_epic_flag(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    epic = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": "epic", "is_new_epic": True}
    ).json()
    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": "child", "parent_id": epic["id"]}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["parent_id"] == epic["id"]


def test_create_ticket_success_and_key_format(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["key"] == "MAP-001"
    assert body["status"] == "backlog"
    assert body["priority"] == "medium"
    assert body["workspace_id"] == ws_id


def test_sequential_numbering(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    keys = []
    for i in range(4):
        resp = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload(f"T{i}"))
        assert resp.status_code == 201
        keys.append(resp.json()["key"])
    assert keys == ["MAP-001", "MAP-002", "MAP-003", "MAP-004"]


def test_key_not_reused_after_delete(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    r1 = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("first"))
    key1 = r1.json()["key"]
    assert client.delete(f"/api/tickets/{key1}").status_code == 204

    r2 = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("second"))
    assert r2.json()["key"] == "MAP-002"


def test_parent_nesting_max_one_level_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    parent = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("parent")).json()
    child = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json=_ticket_payload("child", parent_id=parent["id"]),
    ).json()

    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json=_ticket_payload("grandchild", parent_id=child["id"]),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "nesting_too_deep"


def test_get_ticket_with_nested_data(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    parent = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("parent")).json()
    child = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json=_ticket_payload("child", parent_id=parent["id"]),
    ).json()

    resp = client.get(f"/api/tickets/{parent['key']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["comments"] == []
    assert body["attachments"] == []
    assert body["runs"] == []
    assert len(body["children"]) == 1
    assert body["children"][0]["key"] == child["key"]
    assert body["parent"] is None


def test_get_ticket_includes_parent_epic(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    parent = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("parent")).json()
    child = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json=_ticket_payload("child", parent_id=parent["id"]),
    ).json()

    resp = client.get(f"/api/tickets/{child['key']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent"] is not None
    assert body["parent"]["key"] == parent["key"]
    assert body["parent"]["title"] == parent["title"]
    assert body["children"] == []


def test_get_ticket_nested_comments_include_mentions(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    agent = _make_agent(client, ws_id, "engineer", name="ellie")
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("t")).json()

    client.post(f"/api/tickets/{ticket['key']}/comments", json={"body": "hey @ellie look at this"})

    body = client.get(f"/api/tickets/{ticket['key']}").json()
    assert body["comments"][0]["mentions"] == ["ellie"]


def test_list_filters(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    a = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("a")).json()
    b = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("b")).json()
    client.patch(f"/api/tickets/{b['key']}", json={"status": "in_progress"})

    resp = client.get(f"/api/workspaces/{ws_id}/tickets", params={"status": "in_progress"})
    assert resp.status_code == 200
    keys = [t["key"] for t in resp.json()]
    assert keys == [b["key"]]

    resp = client.get(f"/api/workspaces/{ws_id}/tickets", params={"parent_id": a["id"]})
    assert resp.json() == []


def test_patch_happy_path(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(
        f"/api/tickets/{ticket['key']}",
        json={"title": "renamed", "priority": "urgent", "status": "todo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "renamed"
    assert body["priority"] == "urgent"
    assert body["status"] == "todo"


def _make_agent(client, ws_id, role, name="agent"):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": "gpt", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_patch_any_role_may_transition_between_any_statuses(client, tmp_path):
    # Owner request: the old per-role transition matrix (e.g. only Lead could go
    # review -> qa) was removed — any known role may move a ticket between any two
    # distinct known statuses now.
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()
    engineer_id = _make_agent(client, ws_id, "engineer")

    resp = client.patch(
        f"/api/tickets/{ticket['key']}",
        json={"status": "review", "actor_agent_id": engineer_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "review"


def test_patch_unknown_status_value_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "not_a_real_status"})
    assert resp.status_code == 422


def test_patch_owner_bypasses_matrix_including_blocked_to_todo(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "blocked"})
    assert resp.status_code == 200, resp.text

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "todo"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "todo"
    assert resp.json()["loop_reset_at"] is not None


def test_patch_status_change_not_from_blocked_leaves_loop_reset_at_unset(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "todo"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["loop_reset_at"] is None


def test_patch_legal_transition_writes_system_comment(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "todo"})
    assert resp.status_code == 200

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    comments = detail["comments"]
    assert len(comments) == 1
    assert comments[0]["is_system"] is True
    assert comments[0]["author_agent_id"] is None
    assert "backlog" in comments[0]["body"] and "todo" in comments[0]["body"]


def test_patch_unknown_actor_agent_id_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    resp = client.patch(
        f"/api/tickets/{ticket['key']}",
        json={"status": "todo", "actor_agent_id": "nonexistent"},
    )
    assert resp.status_code == 422


def test_delete_ticket(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()

    assert client.delete(f"/api/tickets/{ticket['key']}").status_code == 204
    assert client.get(f"/api/tickets/{ticket['key']}").status_code == 404


def test_get_nonexistent_ticket_404(client):
    resp = client.get("/api/tickets/MAP-999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Assignment auto-schedules a run (owner request: don't require a separate
# mention/manual-Run click after assigning a ticket that's already actionable).
# ---------------------------------------------------------------------------


def test_patch_assignee_on_todo_ticket_schedules_run(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    # Needs an active sprint — otherwise `ticket_not_in_active_sprint` blocks the
    # schedule regardless of status (first sprint created is auto-active).
    sprint_id = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "S1"}).json()["id"]
    ticket = client.post(
        f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload(sprint_id=sprint_id)
    ).json()
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "todo"})

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"assignee_id": eng_id})
    assert resp.status_code == 200, resp.text

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert len(runs) == 1
    assert runs[0]["ticket_id"] == ticket["id"]
    assert runs[0]["agent_id"] == eng_id
    assert runs[0]["trigger"] == "manual"


def test_patch_assignee_on_backlog_ticket_does_not_schedule_run(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    # Fresh ticket stays "backlog" (default) — not queued for work yet, so
    # assigning it must not start a run.
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()
    assert ticket["status"] == "backlog"

    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"assignee_id": eng_id})
    assert resp.status_code == 200, resp.text

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert runs == []


def test_patch_reassigning_same_agent_does_not_reschedule(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    sprint_id = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "S1"}).json()["id"]
    ticket = client.post(
        f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload(sprint_id=sprint_id)
    ).json()
    client.patch(f"/api/tickets/{ticket['key']}", json={"status": "todo"})

    client.patch(f"/api/tickets/{ticket['key']}", json={"assignee_id": eng_id})
    # Same assignee again (e.g. a PATCH that also touches other fields) — no
    # actual reassignment happened, so no second run.
    resp = client.patch(
        f"/api/tickets/{ticket['key']}", json={"assignee_id": eng_id, "priority": "high"}
    )
    assert resp.status_code == 200, resp.text

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert len(runs) == 1


def test_create_ticket_with_assignee_defaults_to_backlog_no_run(client, tmp_path, monkeypatch):
    # New tickets always start "backlog" (no status field on TicketCreate), so
    # assigning at creation time is a documented near no-op today — still worth
    # asserting so a future status-at-creation feature doesn't silently start
    # auto-running brand-new tickets.
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")

    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload(assignee_id=eng_id)
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "backlog"

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert runs == []


def test_create_ticket_with_invalid_parent_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={"title": "child", "parent_id": "does-not-exist"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_parent"


def test_create_ticket_with_parent_from_other_workspace_422(client, tmp_path):
    ws_a = _make_workspace(client, tmp_path, key="AAA")
    ws_b = _make_workspace(client, tmp_path, key="BBB")
    parent = client.post(
        f"/api/workspaces/{ws_a}/tickets", json=_ticket_payload("parent")
    ).json()
    resp = client.post(
        f"/api/workspaces/{ws_b}/tickets",
        json={"title": "child", "parent_id": parent["id"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_parent"


def test_list_filters_assignee_and_offset(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    a = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("a")).json()
    b = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload("b")).json()
    client.patch(f"/api/tickets/{a['key']}", json={"assignee_id": eng_id})
    client.patch(f"/api/tickets/{b['key']}", json={"assignee_id": eng_id})

    resp = client.get(f"/api/workspaces/{ws_id}/tickets", params={"assignee_id": eng_id})
    assert resp.status_code == 200
    assert {t["key"] for t in resp.json()} == {a["key"], b["key"]}

    resp = client.get(f"/api/workspaces/{ws_id}/tickets", params={"offset": 1})
    assert len(resp.json()) == 1

    resp = client.get(f"/api/workspaces/{ws_id}/tickets", params={"limit": 1})
    assert len(resp.json()) == 1


def test_create_ticket_with_invalid_assignee_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json=_ticket_payload(assignee_id="does-not-exist"),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_reference"


def test_patch_with_invalid_assignee_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()
    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"assignee_id": "does-not-exist"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_reference"


def test_delete_ticket_with_unknown_actor_422(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()
    resp = client.delete(f"/api/tickets/{ticket['key']}", params={"actor_agent_id": "nope"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_reference"


def test_delete_ticket_non_pm_actor_403(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()
    eng_id = _make_agent(client, ws_id, "engineer", "eng-1")
    resp = client.delete(f"/api/tickets/{ticket['key']}", params={"actor_agent_id": eng_id})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "pm_only"


def test_delete_ticket_pm_actor_succeeds(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload()).json()
    pm_id = _make_agent(client, ws_id, "pm", "pm-1")
    resp = client.delete(f"/api/tickets/{ticket['key']}", params={"actor_agent_id": pm_id})
    assert resp.status_code == 204


@pytest.mark.parametrize("n", [20, 100])
async def test_concurrent_creates_get_unique_sequential_keys(tmp_path, n):
    """N concurrent POSTs against the same workspace must yield N unique, sequential keys.

    MAP-016 AC requires the 100-way case specifically (stress level beyond MAP-009's
    original 20-way check, kept here as a parametrize case for regression coverage).
    """
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
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/workspaces",
                json={"name": "Map", "key": "MAP", "repo_path": str(tmp_path)},
            )
            ws_id = resp.json()["id"]

            async def _create(i):
                return await ac.post(
                    f"/api/workspaces/{ws_id}/tickets", json=_ticket_payload(f"t{i}")
                )

            responses = await asyncio.gather(*[_create(i) for i in range(n)])
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

    assert all(r.status_code == 201 for r in responses)
    keys = [r.json()["key"] for r in responses]
    assert len(set(keys)) == n
    assert sorted(keys) == [f"MAP-{i:03d}" for i in range(1, n + 1)]
