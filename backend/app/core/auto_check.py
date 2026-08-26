"""Auto-check scheduler (MAP-050) — proactive follow-up on stale tickets.

Built-in (not a Routine): every workspace gets this by default, no per-workspace
setup needed; the interval and staleness thresholds are tunable in Settings
(`workspace.guardrails["auto_check_interval_minutes"]` and
`["auto_check_stale_minutes"]`, both default 3; 0 disables).

What it does on each tick, per workspace:
- Find tickets in the workspace's ACTIVE sprint whose status still needs work
  (in_progress/review/qa/security/blocked) and whose `updated_at` is older than
  `auto_check_stale_minutes` — the assigned agent hasn't made progress for a while.
- If the assigned agent exists and is idle -> schedule a follow-up run for them.
- Otherwise (no assignee, busy/disabled agent) -> the PM gets the nudge instead:
  the PM is responsible for the whole sprint and can pick the ticket up or
  reassign it. Non-PM agents are only ever nudged for their own tickets.
- Paused workspaces, guardrail trips, and schedule() failures are all skipped —
  never block, never fail loudly.

Runs are scheduled with `trigger="auto"` (same as auto-retries), so they pass
through the normal guardrails (concurrency, sprint gate, cost-per-ticket).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import orchestrator
from app.core.guardrails import GuardrailBlocked, guardrail_limit
from app.db.models import Agent, Sprint, Ticket, TicketAutoCheck, Workspace

# How often the scheduler wakes up. The actual per-workspace interval is
# auto_check_interval_minutes (settings); this is just the scan granularity.
_TICK_SECONDS = 30

# Statuses that still need work — a ticket in any of these can be nudged.
_ACTIONABLE_STATUSES = {"in_progress", "review", "qa", "security", "blocked"}

# Exponential backoff cap for repeated no-op nudges (MAP-050 anti-spam): a ticket
# that keeps getting a near-duplicate "nothing new" report (orchestrator.py's
# _AUTO_CHECK_DUP_RATIO check) gets nudged less and less often — stale_min * 2,4,8
# ... up to this multiplier — instead of every stale_min forever. Any genuinely new
# report resets skip_count to 0 (orchestrator.py), so real progress snaps the cadence
# back to stale_min immediately.
_BACKOFF_CAP_MULTIPLIER = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _in_backoff(state: TicketAutoCheck | None, stale_min: int, now: datetime) -> bool:
    """True if a repeatedly no-op ticket hasn't cleared its backoff window yet."""
    if state is None:
        return False
    last_nudge_at = state.last_nudge_at
    if last_nudge_at.tzinfo is None:
        # SQLite may store naive datetimes; normalize to UTC-aware (same pattern as
        # routine_scheduler.py's last_run_at handling).
        last_nudge_at = last_nudge_at.replace(tzinfo=timezone.utc)
    wait_min = stale_min * min(2**state.skip_count, _BACKOFF_CAP_MULTIPLIER)
    return now < last_nudge_at + timedelta(minutes=wait_min)


async def _nudge(
    session: AsyncSession,
    session_factory: async_sessionmaker,
    ticket: Ticket,
    agent: Agent,
) -> None:
    """Schedule a follow-up run for one (ticket, agent), swallowing failures."""
    try:
        await orchestrator.schedule(
            session,
            session_factory,
            ticket=ticket,
            agent=agent,
            trigger="auto",
        )
    except (GuardrailBlocked, RuntimeError):
        # schedule() already recorded the reason (guardrail / paused workspace)
        # as a system comment; skip and continue.
        pass


async def _tick(session: AsyncSession, session_factory: async_sessionmaker) -> None:
    """One auto-check scan across all workspaces."""
    now = _now()
    workspaces = (await session.scalars(select(Workspace))).all()
    for workspace in workspaces:
        guardrails = workspace.guardrails or {}
        interval = int(guardrail_limit(guardrails, "auto_check_interval_minutes"))
        stale_min = int(guardrail_limit(guardrails, "auto_check_stale_minutes"))
        if interval <= 0 or stale_min <= 0:
            continue  # auto-check disabled for this workspace

        active_sprint = await session.scalar(
            select(Sprint).where(
                Sprint.workspace_id == workspace.id, Sprint.status == "active"
            )
        )
        if active_sprint is None:
            continue  # no active sprint -> nothing to nudge

        tickets = (
            await session.scalars(
                select(Ticket)
                .where(
                    Ticket.workspace_id == workspace.id,
                    Ticket.sprint_id == active_sprint.id,
                    Ticket.status.in_(_ACTIONABLE_STATUSES),
                    Ticket.updated_at < now - timedelta(minutes=stale_min),
                )
            )
        ).all()
        if not tickets:
            continue

        auto_check_states = (
            await session.scalars(
                select(TicketAutoCheck).where(
                    TicketAutoCheck.ticket_id.in_([t.id for t in tickets])
                )
            )
        ).all()
        states_by_ticket = {s.ticket_id: s for s in auto_check_states}
        tickets = [t for t in tickets if not _in_backoff(states_by_ticket.get(t.id), stale_min, now)]
        if not tickets:
            continue

        agents = (
            await session.scalars(select(Agent).where(Agent.workspace_id == workspace.id))
        ).all()
        by_id = {a.id: a for a in agents}
        pm = next((a for a in agents if a.role == "pm" and a.enabled and a.status != "disabled"), None)

        for ticket in tickets:
            agent = by_id.get(ticket.assignee_id)
            if agent is not None and agent.enabled and agent.status != "disabled":
                if agent.status == "idle":
                    await _nudge(session, session_factory, ticket, agent)
                elif pm is not None and pm.id != agent.id and pm.status == "idle":
                    # Assigned agent is busy — the PM (responsible for the whole
                    # sprint) gets the follow-up so it can triage/reassign.
                    await _nudge(session, session_factory, ticket, pm)
            elif pm is not None and pm.status == "idle":
                # No usable assignee — PM picks it up.
                await _nudge(session, session_factory, ticket, pm)


async def run_auto_check(
    session_factory: async_sessionmaker, stop_event: asyncio.Event
) -> None:
    """Background loop: scan for stale tickets every _TICK_SECONDS until stop.

    Waits one full interval before the first tick — same startup-safe pattern as
    routine_scheduler (no DB session while the app is still booting).
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
