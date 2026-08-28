"""Tests for MAP-026: recover orphaned runs on backend startup.

`recover_interrupted_runs` is unit-tested directly against a bare engine (simulating a
crash by seeding `running`/`queued` Run rows with no orchestrator process behind them),
plus one end-to-end test that boots the real app via `TestClient` to prove `main.py`'s
`lifespan` actually calls it.
"""

import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.orchestrator import recover_interrupted_runs
from app.db import session as db_session
from app.db.models import (
    Agent,
    Base,
    Comment,
    Conversation,
    ConversationMessage,
    Routine,
    Run,
    Ticket,
    Workspace,
)


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(maker, *, run_status: str, agent_status: str = "working", key="MAP"):
    """Create a workspace/agent/ticket/run tuple and return their ids."""
    async with maker() as session:
        ws = Workspace(name="W", key=key + uuid.uuid4().hex[:6], repo_path="/tmp")
        session.add(ws)
        await session.flush()

        agent = Agent(
            workspace_id=ws.id,
            name="eng",
            role="engineer",
            model="opencode/big-pickle",
            tool_kind="opencode",
            status=agent_status,
        )
        session.add(agent)
        await session.flush()

        ticket = Ticket(workspace_id=ws.id, key=ws.key + "-1", title="t", status="in_progress")
        session.add(ticket)
        await session.flush()

        run = Run(
            ticket_id=ticket.id,
            agent_id=agent.id,
            status=run_status,
            trigger="manual",
            tool_kind="opencode",
            model="opencode/big-pickle",
        )
        session.add(run)
        await session.commit()
        return ws.id, agent.id, ticket.id, run.id


async def test_running_run_and_working_agent_recovered(maker):
    _, agent_id, ticket_id, run_id = await _seed(maker, run_status="running")

    count = await recover_interrupted_runs(maker)
    assert count == 1

    async with maker() as session:
        run = await session.get(Run, run_id)
        agent = await session.get(Agent, agent_id)
        assert run.status == "interrupted"
        assert run.ended_at is not None
        assert agent.status == "idle"

        comments = (
            await session.execute(select(Comment).where(Comment.ticket_id == ticket_id))
        ).scalars().all()
        assert len(comments) == 1
        assert comments[0].is_system is True
        assert run_id in comments[0].body


async def test_queued_run_recovered(maker):
    _, agent_id, ticket_id, run_id = await _seed(maker, run_status="queued", agent_status="idle")

    count = await recover_interrupted_runs(maker)
    assert count == 1

    async with maker() as session:
        run = await session.get(Run, run_id)
        assert run.status == "interrupted"


@pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
async def test_terminal_runs_untouched(maker, status):
    _, agent_id, ticket_id, run_id = await _seed(maker, run_status=status, agent_status="idle")

    count = await recover_interrupted_runs(maker)
    assert count == 0

    async with maker() as session:
        run = await session.get(Run, run_id)
        assert run.status == status
        comments = (
            await session.execute(select(Comment).where(Comment.ticket_id == ticket_id))
        ).scalars().all()
        assert comments == []


async def test_agent_without_interrupted_runs_untouched(maker):
    # An agent sitting idle with no runs at all — must not be touched.
    async with maker() as session:
        ws = Workspace(name="W", key="OTH", repo_path="/tmp")
        session.add(ws)
        await session.flush()
        agent = Agent(
            workspace_id=ws.id,
            name="eng",
            role="engineer",
            model="opencode/big-pickle",
            tool_kind="opencode",
            status="error",
        )
        session.add(agent)
        await session.commit()
        agent_id = agent.id

    count = await recover_interrupted_runs(maker)
    assert count == 0

    async with maker() as session:
        agent = await session.get(Agent, agent_id)
        assert agent.status == "error"


async def test_no_interrupted_runs_is_noop(maker):
    count = await recover_interrupted_runs(maker)
    assert count == 0
    async with maker() as session:
        comments = (await session.execute(select(Comment))).scalars().all()
        assert comments == []


async def test_multiple_interrupted_runs_different_tickets_each_get_comment(maker):
    _, _, ticket_a, run_a = await _seed(maker, run_status="running", key="AAA")
    _, _, ticket_b, run_b = await _seed(maker, run_status="queued", key="BBB", agent_status="idle")

    count = await recover_interrupted_runs(maker)
    assert count == 2

    async with maker() as session:
        for ticket_id in (ticket_a, ticket_b):
            comments = (
                await session.execute(select(Comment).where(Comment.ticket_id == ticket_id))
            ).scalars().all()
            assert len(comments) == 1


async def test_real_app_startup_recovers(monkeypatch, maker):
    """Boots the real app (real lifespan) and confirms main.py actually wires this in."""
    _, agent_id, ticket_id, run_id = await _seed(maker, run_status="running")

    monkeypatch.setattr(db_session, "async_session", maker)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app):
        pass

    async with maker() as session:
        run = await session.get(Run, run_id)
        agent = await session.get(Agent, agent_id)
        assert run.status == "interrupted"
        assert agent.status == "idle"


async def test_interrupted_chat_run_writes_conversation_system_message(maker):
    async with maker() as session:
        ws = Workspace(name="W", key="MAPCHAT", repo_path="/tmp")
        session.add(ws)
        await session.flush()

        agent = Agent(
            workspace_id=ws.id, name="pm", role="pm", model="m", tool_kind="opencode", status="working"
        )
        session.add(agent)
        await session.flush()

        conversation = Conversation(workspace_id=ws.id, title="Diskusi")
        session.add(conversation)
        await session.flush()

        run = Run(
            conversation_id=conversation.id,
            agent_id=agent.id,
            status="running",
            trigger="chat",
            tool_kind="opencode",
            model="m",
        )
        session.add(run)
        await session.commit()
        conversation_id, agent_id, run_id = conversation.id, agent.id, run.id

    count = await recover_interrupted_runs(maker)
    assert count == 1

    async with maker() as session:
        run = await session.get(Run, run_id)
        agent = await session.get(Agent, agent_id)
        assert run.status == "interrupted"
        assert agent.status == "idle"

        messages = (
            await session.execute(
                select(ConversationMessage).where(ConversationMessage.conversation_id == conversation_id)
            )
        ).scalars().all()
        assert len(messages) == 1
        assert messages[0].is_system is True
        assert "interrupted" in messages[0].body


async def test_interrupted_routine_run_marked_idle(maker):
    async with maker() as session:
        ws = Workspace(name="W", key="MAPROUT", repo_path="/tmp")
        session.add(ws)
        await session.flush()

        agent = Agent(
            workspace_id=ws.id, name="pm", role="pm", model="m", tool_kind="opencode", status="working"
        )
        session.add(agent)
        await session.flush()

        routine = Routine(
            workspace_id=ws.id,
            name="R",
            prompt="p",
            interval_minutes=5,
            mode="idle_only",
            agent_id=agent.id,
            status="running",
        )
        session.add(routine)
        await session.flush()

        run = Run(
            routine_id=routine.id,
            agent_id=agent.id,
            status="running",
            trigger="routine",
            tool_kind="opencode",
            model="m",
        )
        session.add(run)
        await session.commit()
        routine_id, agent_id, run_id = routine.id, agent.id, run.id

    count = await recover_interrupted_runs(maker)
    assert count == 1

    async with maker() as session:
        run = await session.get(Run, run_id)
        agent = await session.get(Agent, agent_id)
        routine = await session.get(Routine, routine_id)
        assert run.status == "interrupted"
        assert agent.status == "idle"
        assert routine.status == "idle"
