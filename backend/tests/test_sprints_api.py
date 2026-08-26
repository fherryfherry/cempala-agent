"""API tests for the Sprint CRUD endpoints (Board/Timeline sprint filter)."""

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


def _make_workspace(client, tmp_path, key="MAP"):
    resp = client.post(
        "/api/workspaces", json={"name": "Map", "key": key, "repo_path": str(tmp_path)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_create_sprint_first_one_is_active(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(
        f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1", "goal": "MVP"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Sprint 1"
    assert body["status"] == "active"
    assert body["index"] == 0


def test_second_sprint_created_planned_not_active(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"})
    resp = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 2"})
    body = resp.json()
    assert body["status"] == "planned"
    assert body["index"] == 1


def test_list_sprints_ordered_by_index(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"})
    client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 2"})
    resp = client.get(f"/api/workspaces/{ws_id}/sprints")
    names = [s["name"] for s in resp.json()]
    assert names == ["Sprint 1", "Sprint 2"]


def test_setting_sprint_active_demotes_other_active_sprint(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    s1 = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"}).json()
    s2 = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 2"}).json()
    assert s1["status"] == "active"
    assert s2["status"] == "planned"

    resp = client.patch(f"/api/sprints/{s2['id']}", json={"status": "active"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"

    s1_after = client.get(f"/api/workspaces/{ws_id}/sprints").json()[0]
    assert s1_after["status"] == "planned"


def test_update_sprint_goal_and_duration(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    s1 = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"}).json()
    resp = client.patch(
        f"/api/sprints/{s1['id']}", json={"goal": "ship login", "duration_estimate": 3.5}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["goal"] == "ship login"
    assert body["duration_estimate"] == 3.5


def test_update_unknown_sprint_404(client):
    resp = client.patch("/api/sprints/does-not-exist", json={"goal": "x"})
    assert resp.status_code == 404


def test_activating_sprint_triggers_runs_for_unfinished_tickets(client, tmp_path, monkeypatch):
    """Owner request: activating a sprint kicks off runs for all its tickets that
    still need work and have an assignee. Done tickets are skipped — if everything
    is done, nothing is triggered."""
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")

    ws_id = _make_workspace(client, tmp_path)
    eng = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": "eng-1", "role": "engineer", "model": "m", "tool_kind": "opencode"},
    ).json()
    qa = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": "qa-1", "role": "qa", "model": "m", "tool_kind": "opencode"},
    ).json()

    s1 = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"}).json()
    s2 = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 2"}).json()

    # Ticket A: todo, assigned -> should be triggered.
    ta = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={"title": "A", "is_new_epic": True, "assignee_id": eng["id"], "sprint_id": s2["id"]},
    ).json()
    # Ticket B: done, assigned -> skipped.
    tb = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={"title": "B", "is_new_epic": True, "assignee_id": qa["id"], "sprint_id": s2["id"]},
    ).json()
    client.patch(f"/api/tickets/{tb['key']}", json={"status": "done"})
    # Ticket C: todo, no assignee -> skipped (no agent to run it).
    client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={"title": "C", "is_new_epic": True, "sprint_id": s2["id"]},
    )

    resp = client.patch(f"/api/sprints/{s2['id']}", json={"status": "active"})
    assert resp.status_code == 200, resp.text

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert len(runs) == 1
    assert runs[0]["ticket_id"] == ta["id"]
    assert runs[0]["agent_id"] == eng["id"]
    assert runs[0]["trigger"] == "manual"


def test_activating_sprint_with_all_done_triggers_nothing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_BIN", "/nonexistent/opencode-for-tests")

    ws_id = _make_workspace(client, tmp_path)
    eng = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": "eng-1", "role": "engineer", "model": "m", "tool_kind": "opencode"},
    ).json()
    s1 = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"}).json()
    s2 = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 2"}).json()

    t = client.post(
        f"/api/workspaces/{ws_id}/tickets",
        json={"title": "A", "is_new_epic": True, "assignee_id": eng["id"], "sprint_id": s2["id"]},
    ).json()
    client.patch(f"/api/tickets/{t['key']}", json={"status": "done"})

    resp = client.patch(f"/api/sprints/{s2['id']}", json={"status": "active"})
    assert resp.status_code == 200, resp.text

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    assert runs == []


def test_create_sprint_with_dates_round_trips(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(
        f"/api/workspaces/{ws_id}/sprints",
        json={"name": "Sprint 1", "start_date": "2026-09-01", "end_date": "2026-09-14"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["start_date"] == "2026-09-01"
    assert body["end_date"] == "2026-09-14"


def test_create_sprint_end_before_start_rejected(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(
        f"/api/workspaces/{ws_id}/sprints",
        json={"name": "Sprint 1", "start_date": "2026-09-14", "end_date": "2026-09-01"},
    )
    assert resp.status_code == 422


def test_update_sprint_end_before_start_rejected_against_existing(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    s1 = client.post(
        f"/api/workspaces/{ws_id}/sprints",
        json={"name": "Sprint 1", "start_date": "2026-09-10"},
    ).json()
    resp = client.patch(f"/api/sprints/{s1['id']}", json={"end_date": "2026-09-01"})
    assert resp.status_code == 422


def _make_ticket(client, ws_id, sprint_id=None, status=None):
    body = {"title": "Do the thing", "is_new_epic": True}
    if sprint_id is not None:
        body["sprint_id"] = sprint_id
    ticket = client.post(f"/api/workspaces/{ws_id}/tickets", json=body).json()
    if status is not None:
        resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": status})
        assert resp.status_code == 200, resp.text
        ticket = resp.json()
    return ticket


def test_complete_sprint_moves_unfinished_tickets_to_active_sprint(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    sprint_a = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint A"}).json()
    sprint_b = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint B"}).json()
    assert sprint_a["status"] == "active"
    assert sprint_b["status"] == "planned"

    todo_ticket = _make_ticket(client, ws_id, sprint_id=sprint_a["id"], status="todo")
    done_ticket = _make_ticket(client, ws_id, sprint_id=sprint_a["id"], status="done")

    resp = client.patch(f"/api/sprints/{sprint_a['id']}", json={"status": "completed"})
    assert resp.status_code == 200, resp.text

    todo_after = client.get(f"/api/tickets/{todo_ticket['key']}").json()
    done_after = client.get(f"/api/tickets/{done_ticket['key']}").json()
    assert todo_after["sprint_id"] == sprint_b["id"]
    assert done_after["sprint_id"] == sprint_a["id"]

    comments = client.get(f"/api/tickets/{todo_ticket['key']}/comments").json()
    system_comments = [c for c in comments if c["is_system"]]
    assert any("ditutup" in c["body"] and sprint_b["name"] in c["body"] for c in system_comments)


def test_complete_sprint_moves_unfinished_tickets_to_next_planned_by_index(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    sprint_a = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint A"}).json()
    sprint_b = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint B"}).json()
    sprint_c = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint C"}).json()
    assert sprint_b["index"] < sprint_c["index"]

    # Complete sprint A (the active one) first, with no other sprint promoted to
    # active, so B and C are both "planned" when sprint X below gets completed.
    client.patch(f"/api/sprints/{sprint_a['id']}", json={"status": "completed"})

    sprint_x = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint X"}).json()
    ticket = _make_ticket(client, ws_id, sprint_id=sprint_x["id"], status="todo")

    resp = client.patch(f"/api/sprints/{sprint_x['id']}", json={"status": "completed"})
    assert resp.status_code == 200, resp.text

    ticket_after = client.get(f"/api/tickets/{ticket['key']}").json()
    assert ticket_after["sprint_id"] == sprint_b["id"]


def test_complete_sprint_no_eligible_next_sprint_falls_back_to_backlog(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    sprint = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Only sprint"}).json()
    ticket = _make_ticket(client, ws_id, sprint_id=sprint["id"], status="in_progress")

    resp = client.patch(f"/api/sprints/{sprint['id']}", json={"status": "completed"})
    assert resp.status_code == 200, resp.text

    ticket_after = client.get(f"/api/tickets/{ticket['key']}").json()
    assert ticket_after["sprint_id"] is None


def test_complete_sprint_terminal_tickets_untouched(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    sprint = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint A"}).json()
    ticket = _make_ticket(client, ws_id, sprint_id=sprint["id"], status="done")
    resp = client.patch(f"/api/tickets/{ticket['key']}", json={"status": "release"})
    assert resp.status_code == 200
    ticket = resp.json()

    client.patch(f"/api/sprints/{sprint['id']}", json={"status": "completed"})

    ticket_after = client.get(f"/api/tickets/{ticket['key']}").json()
    assert ticket_after["sprint_id"] == sprint["id"]


def test_completing_already_completed_sprint_is_noop_for_carry_over(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    sprint = client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint A"}).json()
    ticket = _make_ticket(client, ws_id, sprint_id=sprint["id"], status="todo")

    client.patch(f"/api/sprints/{sprint['id']}", json={"status": "completed"})
    ticket_after_first = client.get(f"/api/tickets/{ticket['key']}").json()

    # Re-completing (a no-op status-wise) must not run carry-over again, so a
    # ticket that was already moved to the backlog must not sprout a second
    # system comment or bounce anywhere else.
    resp = client.patch(f"/api/sprints/{sprint['id']}", json={"status": "completed", "goal": "x"})
    assert resp.status_code == 200, resp.text

    comments = client.get(f"/api/tickets/{ticket['key']}/comments").json()
    carry_over_comments = [c for c in comments if c["is_system"] and "ditutup" in c["body"]]
    assert len(carry_over_comments) == 1
    ticket_after_second = client.get(f"/api/tickets/{ticket['key']}").json()
    assert ticket_after_second["sprint_id"] == ticket_after_first["sprint_id"]
