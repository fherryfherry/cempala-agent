"""SSE endpoint tests (MAP-022): live delivery, replay-on-reconnect, unsubscribe-on-disconnect.

A real socket is used (uvicorn in a background thread + a plain `requests`-style
streaming read) rather than httpx.ASGITransport or Starlette's TestClient: both of
those buffer the *entire* ASGI response before returning, which never completes for
an infinite SSE stream (verified by hand — both hang forever). A real TCP connection
gives us genuine incremental reads and a genuine disconnect-on-close signal.
"""

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.events import event_bus
from app.db.models import Agent, Base, Run, Ticket, Workspace
from app.db.session import get_session
from app.main import app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def env():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async def _create_schema():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_schema())

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn test server did not start"

    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url, maker
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


async def _make_run_async(maker) -> tuple[str, str]:
    async with maker() as session:
        ws = Workspace(name="Acme", key="ACM", repo_path="/tmp/acme", guardrails={})
        session.add(ws)
        await session.flush()
        agent = Agent(workspace_id=ws.id, name="eng-1", role="engineer", model="x/y", tool_kind="opencode")
        session.add(agent)
        await session.flush()
        ticket = Ticket(workspace_id=ws.id, key="ACM-1", title="Do it", status="backlog")
        session.add(ticket)
        await session.flush()
        run = Run(ticket_id=ticket.id, agent_id=agent.id, trigger="manual", tool_kind="opencode", model="x/y")
        session.add(run)
        await session.commit()
        return run.id, ws.id


async def _publish_async(maker, run_id, ws_id, payload=None):
    async with maker() as session:
        return await event_bus.publish(
            session, run_id=run_id, workspace_id=ws_id, type="assistant_text", payload=payload or {}
        )


def _make_run(maker):
    return asyncio.run(_make_run_async(maker))


def _publish(maker, run_id, ws_id, payload=None):
    return asyncio.run(_publish_async(maker, run_id, ws_id, payload))


def _read_sse_events(response, count, timeout=10.0):
    events = []
    current_id = None
    deadline = time.monotonic() + timeout
    for line in response.iter_lines():
        if time.monotonic() > deadline:
            break
        if line.startswith("id: "):
            current_id = line[len("id: "):]
        elif line.startswith("data: "):
            events.append((current_id, json.loads(line[len("data: "):])))
            if len(events) >= count:
                break
    return events


def test_live_events_delivered_after_connecting(env):
    base_url, maker = env
    run_id, ws_id = _make_run(maker)

    published = {}

    def publish_soon():
        time.sleep(0.3)
        published["ev"] = _publish(maker, run_id, ws_id, payload={"t": "hi"})

    t = threading.Thread(target=publish_soon)
    t.start()
    try:
        with httpx.Client(timeout=15.0) as client:
            with client.stream("GET", f"{base_url}/api/workspaces/{ws_id}/events/stream") as response:
                assert response.status_code == 200
                events = _read_sse_events(response, 1)
    finally:
        t.join()

    assert len(events) == 1
    assert events[0][0] == published["ev"].id
    assert events[0][1]["type"] == "assistant_text"


def test_reconnect_with_since_event_id_replays_only_later_no_dup(env):
    base_url, maker = env
    run_id, ws_id = _make_run(maker)

    e1 = _publish(maker, run_id, ws_id, payload={"i": 1})
    e2 = _publish(maker, run_id, ws_id, payload={"i": 2})
    e3 = _publish(maker, run_id, ws_id, payload={"i": 3})

    with httpx.Client(timeout=15.0) as client:
        with client.stream(
            "GET", f"{base_url}/api/workspaces/{ws_id}/events/stream?since_event_id={e1.id}"
        ) as response:
            events = _read_sse_events(response, 2)

    ids = [eid for eid, _ in events]
    assert ids == [e2.id, e3.id]  # e1 not replayed, no dups, correct order
    assert len(set(ids)) == len(ids)


def test_reconnect_via_last_event_id_header_replays_and_then_goes_live(env):
    base_url, maker = env
    run_id, ws_id = _make_run(maker)

    e1 = _publish(maker, run_id, ws_id, payload={"i": 1})
    e2 = _publish(maker, run_id, ws_id, payload={"i": 2})

    published = {}

    def publish_soon():
        time.sleep(0.3)
        published["ev"] = _publish(maker, run_id, ws_id, payload={"i": 3})

    t = threading.Thread(target=publish_soon)
    t.start()
    try:
        with httpx.Client(timeout=15.0) as client:
            with client.stream(
                "GET",
                f"{base_url}/api/workspaces/{ws_id}/events/stream",
                headers={"Last-Event-ID": e1.id},
            ) as response:
                events = _read_sse_events(response, 2)
    finally:
        t.join()

    ids = [eid for eid, _ in events]
    assert ids == [e2.id, published["ev"].id]
    assert len(set(ids)) == len(ids)


def test_unknown_since_event_id_replays_everything(env):
    base_url, maker = env
    run_id, ws_id = _make_run(maker)
    e1 = _publish(maker, run_id, ws_id, payload={"i": 1})

    with httpx.Client(timeout=15.0) as client:
        with client.stream(
            "GET", f"{base_url}/api/workspaces/{ws_id}/events/stream?since_event_id=does-not-exist"
        ) as response:
            events = _read_sse_events(response, 1)

    assert events[0][0] == e1.id


def test_closing_stream_unsubscribes(env):
    base_url, maker = env
    run_id, ws_id = _make_run(maker)

    with httpx.Client(timeout=15.0) as client:
        with client.stream("GET", f"{base_url}/api/workspaces/{ws_id}/events/stream") as response:
            assert response.status_code == 200
            time.sleep(0.3)  # let the server-side generator reach subscribe()
            assert len(event_bus._subscribers.get(ws_id, ())) == 1

    # closing the client's connection is a real socket close -> real ASGI disconnect;
    # give the server loop's poll interval a moment to notice and unsubscribe.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if len(event_bus._subscribers.get(ws_id, ())) == 0:
            break
        time.sleep(0.1)

    assert len(event_bus._subscribers.get(ws_id, ())) == 0
