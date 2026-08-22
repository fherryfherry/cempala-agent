"""EventBus: publish persists+increments seq, subscriber fan-out, bounded queue overflow."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.events import OVERFLOW_MARKER, EventBus
from app.db.models import Agent, Base, Event, Run, Ticket, Workspace


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s

    await engine.dispose()


async def _make_run(session) -> str:
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


@pytest.mark.asyncio
async def test_publish_persists_with_incrementing_seq(session):
    bus = EventBus()
    run_id, ws_id = await _make_run(session)

    e1 = await bus.publish(session, run_id=run_id, workspace_id=ws_id, type="run_started", payload={})
    e2 = await bus.publish(session, run_id=run_id, workspace_id=ws_id, type="assistant_text", payload={"t": "hi"})

    assert e1.seq == 1
    assert e2.seq == 2

    rows = (await session.scalars(select(Event).where(Event.run_id == run_id))).all()
    assert len(rows) == 2
    assert {r.seq for r in rows} == {1, 2}


@pytest.mark.asyncio
async def test_publish_delivers_to_active_subscriber(session):
    bus = EventBus()
    run_id, ws_id = await _make_run(session)
    queue = bus.subscribe(ws_id)

    ev = await bus.publish(session, run_id=run_id, workspace_id=ws_id, type="run_started", payload={})

    received = queue.get_nowait()
    assert received.id == ev.id


@pytest.mark.asyncio
async def test_subscribers_isolated_per_workspace(session):
    bus = EventBus()
    run_id, ws_id = await _make_run(session)
    q_same = bus.subscribe(ws_id)
    q_other = bus.subscribe("other-workspace")

    await bus.publish(session, run_id=run_id, workspace_id=ws_id, type="run_started", payload={})

    assert q_same.qsize() == 1
    assert q_other.qsize() == 0


@pytest.mark.asyncio
async def test_slow_subscriber_drops_oldest_and_does_not_block_publisher(session):
    bus = EventBus()
    run_id, ws_id = await _make_run(session)
    queue = bus.subscribe(ws_id, maxsize=3)  # never drained -> forces overflow

    async def burst():
        for i in range(20):
            await bus.publish(
                session, run_id=run_id, workspace_id=ws_id, type="assistant_text", payload={"i": i}
            )

    # publish() must never block on a full subscriber queue
    await asyncio.wait_for(burst(), timeout=2.0)

    # queue stayed bounded, and the overflow marker made it in somewhere
    assert queue.qsize() <= 3
    drained = []
    while not queue.empty():
        drained.append(queue.get_nowait())
    assert any(item == OVERFLOW_MARKER for item in drained)

    # the DB has the complete, un-dropped history regardless of subscriber overflow
    rows = (await session.scalars(select(Event).where(Event.run_id == run_id))).all()
    assert len(rows) == 20
    assert sorted(r.seq for r in rows) == list(range(1, 21))


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery(session):
    bus = EventBus()
    run_id, ws_id = await _make_run(session)
    queue = bus.subscribe(ws_id)
    bus.unsubscribe(ws_id, queue)

    await bus.publish(session, run_id=run_id, workspace_id=ws_id, type="run_started", payload={})

    assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_stream_iterator_yields_published_events(session):
    bus = EventBus()
    run_id, ws_id = await _make_run(session)

    agen = bus.stream(ws_id)
    # prime the generator so subscribe() runs before publish
    task = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0)  # let subscribe() register

    ev = await bus.publish(session, run_id=run_id, workspace_id=ws_id, type="run_started", payload={})
    received = await asyncio.wait_for(task, timeout=1.0)

    assert received.id == ev.id
    await agen.aclose()
