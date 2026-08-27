"""API tests for conversations (chat separated from ticket comments)."""

import stat
import time
import uuid

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
async def client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OPENCODE_BIN", str(tmp_path / "opencode-fake"))
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))

    # File-based DB, not :memory: — background chat runs open their own sessions,
    # and each :memory: connection would be a separate empty database.
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


def _make_agent(client, ws_id, role, name):
    resp = client.post(
        f"/api/workspaces/{ws_id}/agents",
        json={"name": name, "role": role, "model": "opencode/big-pickle", "tool_kind": "opencode"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _write_python_binary(path, code):
    path.write_text(f"#!/usr/bin/env python3\n{code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


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


def _chat_reply_script():
    return """\
import json
text = \"\"\"Siap!

```map
summary: |
  Siap, saya akan kerjakan itu.
```
\"\"\"
print(json.dumps({"type": "assistant_text", "text": text}))
"""


def _chat_reply_script_with_comment(ticket_key):
    return f"""\
import json
text = \"\"\"Follow-up!

```map
summary: |
  Sudah saya follow-up lewat komentar tiket.
comments:
  - ticket: {ticket_key}
    body: |
      Ini tindak lanjut dari chat.
```
\"\"\"
print(json.dumps({{"type": "assistant_text", "text": text}}))
"""


def test_conversation_crud(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)

    resp = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Rencana fitur"}
    )
    assert resp.status_code == 201, resp.text
    conv = resp.json()
    assert conv["title"] == "Rencana fitur"
    assert conv["linked_ticket_key"] is None

    listed = client.get(f"/api/workspaces/{ws_id}/conversations").json()
    assert len(listed) == 1
    assert listed[0]["id"] == conv["id"]

    detail = client.get(f"/api/conversations/{conv['id']}").json()
    assert detail["id"] == conv["id"]

    # Missing workspace -> 404.
    assert client.get("/api/workspaces/nope/conversations").status_code == 404
    assert client.get("/api/conversations/nope").status_code == 404


def test_conversation_linked_ticket_validated(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    resp = client.post(
        f"/api/workspaces/{ws_id}/conversations",
        json={"title": "t", "linked_ticket_key": "MAP-999"},
    )
    assert resp.status_code == 404


def test_message_triggers_chat_run_and_pm_replies(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Diskusi"}
    ).json()

    script = _write_python_binary(tmp_path / "opencode", _chat_reply_script())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    resp = client.post(
        f"/api/conversations/{conv['id']}/messages", json={"body": "Bikin fitur X ya"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["author_agent_id"] is None
    assert resp.json()["is_system"] is False

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    chat_run = next(r for r in runs if r["trigger"] == "chat")
    assert chat_run["conversation_id"] == conv["id"]
    assert chat_run["ticket_id"] is None

    final = _wait_for_run(client, chat_run["id"])
    assert final["status"] == "done", final

    messages = client.get(f"/api/conversations/{conv['id']}/messages").json()
    # owner message + PM reply
    assert len(messages) == 2
    reply = messages[-1]
    assert reply["author_agent_id"] is not None
    assert reply["is_system"] is False
    assert "kerjakan" in reply["body"]

    # No ticket was touched (chat stays separate from comments).
    tickets = client.get(f"/api/workspaces/{ws_id}/tickets").json()
    assert tickets == []


def test_chat_run_comments_land_on_ticket(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    ticket = client.post(
        f"/api/workspaces/{ws_id}/tickets", json={"title": "Tiket A", "is_new_epic": True}
    ).json()
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Tindak lanjut"}
    ).json()

    script = _write_python_binary(
        tmp_path / "opencode", _chat_reply_script_with_comment(ticket["key"])
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "Follow up"})
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    chat_run = next(r for r in runs if r["trigger"] == "chat")
    final = _wait_for_run(client, chat_run["id"])
    assert final["status"] == "done", final

    detail = client.get(f"/api/tickets/{ticket['key']}").json()
    agent_comments = [c for c in detail["comments"] if not c["is_system"]]
    assert len(agent_comments) == 1
    assert "tindak lanjut" in agent_comments[0]["body"]


def test_message_without_pm_rejected(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "No PM"}
    ).json()
    resp = client.post(
        f"/api/conversations/{conv['id']}/messages", json={"body": "halo"}
    )
    assert resp.status_code == 422
    assert "PM" in resp.json()["error"]["message"]


def test_conversation_attachment_upload_delete(client, tmp_path):
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Dengan file"}
    ).json()

    resp = client.post(
        f"/api/conversations/{conv['id']}/attachments",
        files={"file": ("context.txt", b"ini konteks", "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    att = resp.json()
    assert att["filename"] == "context.txt"
    assert att["size_bytes"] == len(b"ini konteks")

    listed = client.get(f"/api/conversations/{conv['id']}/attachments").json()
    assert len(listed) == 1

    dl = client.get(f"/api/conversations/attachments/{att['id']}/download")
    assert dl.status_code == 200
    assert dl.content == b"ini konteks"

    assert client.delete(f"/api/conversations/attachments/{att['id']}").status_code == 204
    assert client.get(f"/api/conversations/{conv['id']}/attachments").json() == []


def _chat_reply_script_with_proposal():
    return """\
import json
text = \"\"\"Sprint sebelumnya udah kelar.

```map
summary: |
  Sprint sebelumnya udah kelar semua. Aku usul bikin Sprint 2 buat lanjutin fitur Y, boleh gas?
sprints:
  - name: Sprint 2
    goal: Lanjutin fitur Y
tickets:
  - title: Fitur Y bagian 1
    description: |
      Detail fitur Y.
    priority: medium
    sprint: Sprint 2
```
\"\"\"
print(json.dumps({"type": "assistant_text", "text": text}))
"""


def test_chat_proposes_sprint_when_no_active_sprint(client, tmp_path, monkeypatch):
    """No active sprint in the workspace -> the PM's sprints[]/tickets[] are held as
    a proposal, nothing is created, and the owner sees an approval prompt."""
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Sprint baru"}
    ).json()

    script = _write_python_binary(tmp_path / "opencode", _chat_reply_script_with_proposal())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "Ada kerjaan baru nih"})
    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    chat_run = next(r for r in runs if r["trigger"] == "chat")
    final = _wait_for_run(client, chat_run["id"])
    assert final["status"] == "done", final

    assert client.get(f"/api/workspaces/{ws_id}/sprints").json() == []
    assert client.get(f"/api/workspaces/{ws_id}/tickets").json() == []

    messages = client.get(f"/api/conversations/{conv['id']}/messages").json()
    proposal_messages = [
        m for m in messages if not m["is_system"] and m["author_agent_id"] is not None and "oke" in m["body"].lower()
    ]
    assert len(proposal_messages) == 1
    assert "Sprint 2" in proposal_messages[0]["body"]


def test_chat_sprint_proposal_approved_creates_and_activates(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Sprint baru"}
    ).json()

    script = _write_python_binary(tmp_path / "opencode", _chat_reply_script_with_proposal())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "Ada kerjaan baru nih"})
    chat_run = next(
        r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "chat"
    )
    _wait_for_run(client, chat_run["id"])

    resp = client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "oke lanjut"})
    assert resp.status_code == 201, resp.text

    sprints = client.get(f"/api/workspaces/{ws_id}/sprints").json()
    assert len(sprints) == 1
    assert sprints[0]["name"] == "Sprint 2"
    assert sprints[0]["status"] == "active"

    tickets = client.get(f"/api/workspaces/{ws_id}/tickets").json()
    assert len(tickets) == 1
    assert tickets[0]["title"] == "Fitur Y bagian 1"
    assert tickets[0]["sprint_id"] == sprints[0]["id"]

    messages = client.get(f"/api/conversations/{conv['id']}/messages").json()
    pm_messages = [m for m in messages if not m["is_system"] and m["author_agent_id"] is not None]
    assert any("disetujui" in m["body"] for m in pm_messages)

    # The approval message itself must not have scheduled a fresh PM chat run.
    chat_runs = [
        r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "chat"
    ]
    assert len(chat_runs) == 1


def test_chat_sprint_proposal_not_approved_stays_pending(client, tmp_path, monkeypatch):
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Sprint baru"}
    ).json()

    script = _write_python_binary(tmp_path / "opencode", _chat_reply_script_with_proposal())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "Ada kerjaan baru nih"})
    chat_run = next(
        r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "chat"
    )
    _wait_for_run(client, chat_run["id"])

    resp = client.post(
        f"/api/conversations/{conv['id']}/messages", json={"body": "kurangi dulu scope-nya"}
    )
    assert resp.status_code == 201, resp.text
    second_run = next(
        r
        for r in client.get(f"/api/workspaces/{ws_id}/runs").json()
        if r["trigger"] == "chat" and r["id"] != chat_run["id"]
    )
    _wait_for_run(client, second_run["id"])

    assert client.get(f"/api/workspaces/{ws_id}/sprints").json() == []
    assert client.get(f"/api/workspaces/{ws_id}/tickets").json() == []


def _chat_reply_script_with_two_sprints():
    return """\
import json
text = \"\"\"Usul dua sprint.

```map
summary: |
  Usul: Sprint 2 buat sekarang, Sprint 3 buat nanti.
sprints:
  - name: Sprint 2
    goal: Goal A
  - name: Sprint 3
    goal: Goal B
tickets:
  - title: Fitur A
    sprint: Sprint 2
```
\"\"\"
print(json.dumps({"type": "assistant_text", "text": text}))
"""


def test_chat_sprint_proposal_multiple_sprints_only_first_activated(client, tmp_path, monkeypatch):
    """A proposal declaring two new sprints under sprints[] must not have them fight
    over the single-active-sprint slot: only the one that actually bootstraps
    active stays active, the other is created but left planned."""
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Dua sprint"}
    ).json()

    script = _write_python_binary(tmp_path / "opencode", _chat_reply_script_with_two_sprints())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "Ada 2 sprint nih"})
    chat_run = next(
        r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "chat"
    )
    _wait_for_run(client, chat_run["id"])

    resp = client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "oke"})
    assert resp.status_code == 201, resp.text

    sprints = {s["name"]: s for s in client.get(f"/api/workspaces/{ws_id}/sprints").json()}
    assert set(sprints) == {"Sprint 2", "Sprint 3"}
    assert sprints["Sprint 2"]["status"] == "active"
    assert sprints["Sprint 3"]["status"] == "planned"

    messages = client.get(f"/api/conversations/{conv['id']}/messages").json()
    confirmation = next(m for m in messages if not m["is_system"] and "disetujui" in m["body"])
    assert "TIDAK diaktifkan" in confirmation["body"]
    assert "Sprint 3" in confirmation["body"]


def test_chat_sprint_proposal_does_not_reactivate_completed_sprint(client, tmp_path, monkeypatch):
    """A ticket-level `sprint:` reference to an existing (completed) sprint by name
    must not silently reactivate it — only sprints[] the PM actually proposed as
    new get activated."""
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")

    old_sprint = client.post(
        f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"}
    ).json()
    assert old_sprint["status"] == "active"  # first sprint ever -> bootstrapped active
    client.patch(f"/api/sprints/{old_sprint['id']}", json={"status": "completed"})

    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Referensi sprint lama"}
    ).json()
    script = _write_python_binary(
        tmp_path / "opencode",
        """\
import json
text = \"\"\"Lanjut di sprint lama.

```map
summary: |
  Aku pakai nama sprint lama buat tiket ini, tapi tidak minta diaktifkan lagi.
tickets:
  - title: Fitur B
    sprint: Sprint 1
```
\"\"\"
print(json.dumps({"type": "assistant_text", "text": text}))
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "Ada kerjaan baru nih"})
    chat_run = next(
        r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "chat"
    )
    _wait_for_run(client, chat_run["id"])

    resp = client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "oke"})
    assert resp.status_code == 201, resp.text

    sprints = client.get(f"/api/workspaces/{ws_id}/sprints").json()
    assert len(sprints) == 1
    assert sprints[0]["id"] == old_sprint["id"]
    assert sprints[0]["status"] == "completed"  # NOT reactivated

    tickets = client.get(f"/api/workspaces/{ws_id}/tickets").json()
    assert len(tickets) == 1
    assert tickets[0]["sprint_id"] == old_sprint["id"]


def test_chat_sprint_proposal_double_approval_does_not_duplicate(client, tmp_path, monkeypatch):
    """A proposal already claimed (executed) by one approval message must not be
    re-executed by a second approval message replying to the same conversation."""
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Approve dua kali"}
    ).json()

    script = _write_python_binary(tmp_path / "opencode", _chat_reply_script_with_proposal())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "Ada kerjaan baru nih"})
    chat_run = next(
        r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "chat"
    )
    _wait_for_run(client, chat_run["id"])

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "oke"})
    # Second "oke": pending_proposal is already cleared, so this must NOT re-create
    # the same sprint/ticket — it just falls through to a normal chat message. Swap
    # in a script with no sprints:/tickets: so a plain reply doesn't itself create
    # anything (isolating the assertion to the double-approval guard, not a fresh
    # PM proposal from the fallback chat run).
    monkeypatch.setattr(
        settings, "OPENCODE_BIN", _write_python_binary(tmp_path / "opencode2", _chat_reply_script())
    )
    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "oke"})
    second_chat_run = next(
        (
            r
            for r in client.get(f"/api/workspaces/{ws_id}/runs").json()
            if r["trigger"] == "chat" and r["id"] != chat_run["id"]
        ),
        None,
    )
    if second_chat_run is not None:
        _wait_for_run(client, second_chat_run["id"])

    assert len(client.get(f"/api/workspaces/{ws_id}/sprints").json()) == 1
    assert len(client.get(f"/api/workspaces/{ws_id}/tickets").json()) == 1


def _chat_reply_script_with_malformed_sprints():
    # Regression for the real failure mode: the agent writes `sprints: |` (YAML
    # literal block) instead of `sprints:` followed by a plain list — this turns
    # the value into one string, not a list of mappings, so report.py drops it.
    # The `summary` still claims success, as it did in the wild.
    return """\
import json
text = \"\"\"Siap.

```map
summary: |
  Baik Mas, Sprint 6 sudah saya buat (26 Agt - 1 Sep).
sprints: |
  - name: Sprint 6
    goal: Perbaikan keamanan fallback secret MCP
```
\"\"\"
print(json.dumps({"type": "assistant_text", "text": text}))
"""


def test_chat_malformed_sprints_yaml_not_created_and_owner_notified(client, tmp_path, monkeypatch):
    """PM writes `sprints: |` (literal block) by mistake instead of a plain list —
    report.py drops it and PM's own summary still claims success. The owner must
    see a system correction in the same chat, not just a silently missing sprint."""
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Sprint 6"}
    ).json()

    script = _write_python_binary(
        tmp_path / "opencode", _chat_reply_script_with_malformed_sprints()
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "Bikin Sprint 6"})
    chat_run = next(
        r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "chat"
    )
    final = _wait_for_run(client, chat_run["id"])
    assert final["status"] == "done", final

    # Nothing was actually created, despite the PM's claim in its summary.
    assert client.get(f"/api/workspaces/{ws_id}/sprints").json() == []

    messages = client.get(f"/api/conversations/{conv['id']}/messages").json()
    system_messages = [m for m in messages if m["is_system"]]
    assert len(system_messages) == 1
    body = system_messages[0]["body"]
    assert "sprints" in body
    assert "sprints: |" in body  # names the actual mistake, not just "dropped"


def _chat_reply_script_activate_sprint():
    return """\
import json
text = \"\"\"Lanjut ke sprint berikutnya.

```map
summary: |
  Sprint 2 dibuat dan diaktifkan.
sprints:
  - name: Sprint 2
    status: active
```
\"\"\"
print(json.dumps({"type": "assistant_text", "text": text}))
"""


def test_chat_sprints_only_report_still_creates_and_activates_sprint(
    client, tmp_path, monkeypatch
):
    """Regression: sprint creation/activation used to be nested inside
    `if parsed.tickets:`, so a chat report that ONLY declares `sprints:` (no new
    tickets in the same message — the common "activate the next sprint" case)
    silently did nothing at all."""
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    # An active sprint already exists, so this goes through the immediate-creation
    # path, not the no-active-sprint proposal-hold path.
    client.post(f"/api/workspaces/{ws_id}/sprints", json={"name": "Sprint 1"})
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Sprint berikutnya"}
    ).json()

    script = _write_python_binary(tmp_path / "opencode", _chat_reply_script_activate_sprint())
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "Sprint 1 sudah kelar"})
    chat_run = next(
        r for r in client.get(f"/api/workspaces/{ws_id}/runs").json() if r["trigger"] == "chat"
    )
    final = _wait_for_run(client, chat_run["id"])
    assert final["status"] == "done", final

    sprints = {s["name"]: s for s in client.get(f"/api/workspaces/{ws_id}/sprints").json()}
    assert sprints["Sprint 2"]["status"] == "active"
    assert sprints["Sprint 1"]["status"] == "planned"


def test_second_message_does_not_duplicate_active_run(client, tmp_path, monkeypatch):
    """A message sent while a chat run is queued/running must not schedule a second
    run — the running run's transcript already includes it."""
    ws_id = _make_workspace(client, tmp_path)
    _make_agent(client, ws_id, "pm", "pm-1")
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations", json={"title": "Lambat"}
    ).json()

    script = _write_python_binary(
        tmp_path / "opencode",
        """\
import json, time
text = \"\"\"Bentar...

```map
summary: |
  Oke siap.
```
\"\"\"
time.sleep(0.3)
print(json.dumps({"type": "assistant_text", "text": text}))
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "pesan 1"})
    # Immediately send a second message while the first run is still in flight.
    client.post(f"/api/conversations/{conv['id']}/messages", json={"body": "pesan 2"})

    runs = client.get(f"/api/workspaces/{ws_id}/runs").json()
    chat_runs = [r for r in runs if r["trigger"] == "chat"]
    assert len(chat_runs) == 1

    final = _wait_for_run(client, chat_runs[0]["id"])
    assert final["status"] == "done", final

    messages = client.get(f"/api/conversations/{conv['id']}/messages").json()
    assert len(messages) == 3  # 2 owner + 1 reply
