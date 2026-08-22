"""Minimal proof that SQLite FK cascades actually fire (PRAGMA foreign_keys=ON wiring)."""

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Agent, Base, Ticket, Workspace


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s

    await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_delete_cascades_to_agent_and_ticket(session):
    ws = Workspace(name="Acme", key="ACM", repo_path="/tmp/acme", guardrails={})
    session.add(ws)
    await session.flush()

    agent = Agent(workspace_id=ws.id, name="lead", role="lead", model="x/y", tool_kind="opencode")
    session.add(agent)
    await session.flush()

    ticket = Ticket(workspace_id=ws.id, key="ACM-1", title="Do the thing", status="backlog")
    session.add(ticket)
    await session.commit()

    await session.delete(ws)
    await session.commit()

    assert (await session.scalars(select(Agent))).all() == []
    assert (await session.scalars(select(Ticket))).all() == []
