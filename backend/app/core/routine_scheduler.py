"""Routine scheduler — periodic trigger for scheduled agent tasks (no ticket).

One global asyncio task (started in main.py's lifespan, cancelled on shutdown)
ticks every 60s and fires any enabled routine whose `last_run_at + interval`
has passed. Mode semantics:
- idle_only: the agent must be idle (no run in flight) — otherwise the tick is
  skipped and `last_run_at` is still advanced so it doesn't retry every tick.
- consistent: `schedule_routine_run()` queues the run behind the agent's FIFO
  queue if the agent is busy — it never gets skipped.

Routine status lifecycle: idle -> waiting (run scheduled/queued) -> running
(run started) -> idle (run finished). `disabled` routines are never fired.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import orchestrator
from app.core.guardrails import GuardrailBlocked
from app.db.models import Agent, Routine, Workspace

_TICK_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _tick(session: AsyncSession, session_factory: async_sessionmaker) -> None:
    """Fire every due routine in the workspace. Runs in its own session."""
    now = _now()
    routines = (
        await session.scalars(
            select(Routine).where(Routine.status != "disabled")
        )
    ).all()
    for routine in routines:
        if routine.status in ("waiting", "running"):
            continue  # a run is already scheduled/in flight for it
        last = routine.last_run_at
        if last is not None:
            # SQLite may store naive datetimes; normalize to UTC-aware.
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last + timedelta(minutes=routine.interval_minutes) > now:
                continue  # not due yet

        workspace = await session.get(Workspace, routine.workspace_id)
        if workspace is not None and workspace.paused:
            continue

        agent = await session.get(Agent, routine.agent_id) if routine.agent_id else None
        if agent is None or not agent.enabled or agent.status == "disabled":
            # No valid agent: advance last_run_at so we don't retry every tick, but
            # leave the routine idle (owner sees it and can fix the assignment).
            routine.last_run_at = now
            await session.commit()
            continue

        if routine.mode == "idle_only" and agent.status != "idle":
            routine.last_run_at = now
            await session.commit()
            continue

        try:
            await orchestrator.schedule_routine_run(session, session_factory, routine, agent)
        except (GuardrailBlocked, RuntimeError):
            # schedule_routine_run already reset the routine to idle + advanced
            # last_run_at on a guardrail trip; a paused workspace raises RuntimeError.
            pass


async def run_scheduler(session_factory: async_sessionmaker, stop_event: asyncio.Event) -> None:
    """Background loop: tick every _TICK_SECONDS until stop_event is set.

    Waits one full interval before the first tick — startup must not open a DB
    session while the app is still booting (and tests' in-memory engines can't
    handle a concurrent session on the same StaticPool connection).
    """
    while not stop_event.is_set():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=_TICK_SECONDS)
        if stop_event.is_set():
            break
        try:
            async with session_factory() as session:
                await _tick(session, session_factory)
        except Exception:
            # Never let a tick crash the scheduler loop.
            pass
