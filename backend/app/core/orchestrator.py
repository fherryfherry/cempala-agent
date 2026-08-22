"""Run orchestrator — docs/02-tsd.md §4.5, MAP-023.

`schedule()` creates a `Run` row and either starts it immediately or queues it
(one running run per agent, FIFO for the rest). `execute()` drives one run
end to end: build the prompt, stream adapter events through the event bus,
then apply the parsed ```map report (or a failure path) to the ticket.

No guardrail checks here yet (MAP-027 hooks in at the marked spot below) and
no automatic handoff scheduling (MAP-029) — this only runs what it's told.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import AdapterEvent, RunContext, TOOLS
from app.agents.prompts import AgentInfo, CommentInfo, TicketInfo, build_prompt
from app.api.attachments import _storage_dir
from app.core.events import event_bus
from app.core.report import parse_report
from app.core.state_machine import can_transition
from app.db.models import Agent, Attachment, Comment, CommentMention, Run, Ticket, Workspace

_TAIL_CHARS = 2000

# run.id -> asyncio.Task, for currently-executing runs (used by the stop endpoint).
RUNNING: dict[str, asyncio.Task] = {}

# agent_id -> FIFO queue of run ids waiting for that agent to free up.
_PENDING: dict[str, deque[str]] = defaultdict(deque)
# agent_id -> whether it currently has a run actually executing.
_BUSY: set[str] = set()
# Guards read-modify-write of _PENDING/_BUSY across concurrent schedule() calls.
_LOCK = asyncio.Lock()

# run.id -> the cancel_event handed to the adapter for that run. Separate from the ORM
# `Run` row (which has no such column) since `stop()` runs in its own DB session/object
# instance and can't reach an attribute stashed on a different session's object.
_CANCEL_EVENTS: dict[str, asyncio.Event] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def schedule(
    session: AsyncSession,
    session_factory: async_sessionmaker,
    *,
    ticket: Ticket,
    agent: Agent,
    trigger: str,
    parent_run_id: str | None = None,
) -> Run:
    """Create a queued Run row, then either start it now or leave it queued.

    `session` is used for the create; `session_factory` is handed to the
    background task so it can open its own session (the request's session
    closes when the endpoint returns, long before the run finishes).
    """
    workspace = await session.get(Workspace, ticket.workspace_id)
    if workspace is not None and workspace.paused:
        raise RuntimeError("workspace paused")

    # ponytail: guardrail checks (cost/timeout/handoff-depth/loop-detector) belong here —
    # MAP-027 hooks in before the Run row is created. Not built yet, skipped entirely.

    run = Run(
        ticket_id=ticket.id,
        agent_id=agent.id,
        status="queued",
        trigger=trigger,
        parent_run_id=parent_run_id,
        tool_kind=agent.tool_kind,
        model=agent.model,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    async with _LOCK:
        if agent.id in _BUSY:
            _PENDING[agent.id].append(run.id)
        else:
            _BUSY.add(agent.id)
            RUNNING[run.id] = asyncio.create_task(_execute_and_advance(session_factory, run.id))

    return run


async def _execute_and_advance(session_factory: async_sessionmaker, run_id: str) -> None:
    """Run one run, then dequeue the agent's next pending run (if any)."""
    try:
        await execute(session_factory, run_id)
    finally:
        RUNNING.pop(run_id, None)
        async with session_factory() as session:
            run = await session.get(Run, run_id)
            agent_id = run.agent_id if run else None

        next_run_id = None
        async with _LOCK:
            if agent_id is not None:
                queue = _PENDING.get(agent_id)
                if queue:
                    next_run_id = queue.popleft()
                else:
                    _BUSY.discard(agent_id)

        if next_run_id is not None:
            RUNNING[next_run_id] = asyncio.create_task(
                _execute_and_advance(session_factory, next_run_id)
            )


def _accumulate_text(buffer: list[str], ev: AdapterEvent) -> None:
    if ev.type != "assistant_text":
        return
    text = ev.payload.get("text")
    if isinstance(text, str):
        buffer.append(text)


async def _write_system_comment(session: AsyncSession, ticket_id: str, body: str) -> None:
    session.add(Comment(ticket_id=ticket_id, author_agent_id=None, is_system=True, body=body))


async def _block_ticket(
    session: AsyncSession, ticket: Ticket, agent: Agent, reason_body: str
) -> None:
    """System-driven transition to `blocked` — always legal (any -> blocked, any role)."""
    if ticket.status != "blocked":
        ticket.status = "blocked"
    await _write_system_comment(session, ticket.id, reason_body)


async def execute(session_factory: async_sessionmaker, run_id: str) -> None:
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return
        ticket = await session.get(Ticket, run.ticket_id)
        agent = await session.get(Agent, run.agent_id)
        workspace = await session.get(Workspace, ticket.workspace_id)

        try:
            run.status = "running"
            run.started_at = _now()
            agent.status = "working"
            await session.commit()

            prompt = await _build_prompt_for(session, workspace, agent, ticket)

            await event_bus.publish(
                session,
                run_id=run.id,
                workspace_id=workspace.id,
                type="run_started",
                payload={"prompt": prompt},
            )

            attachments = (
                await session.scalars(
                    select(Attachment).where(Attachment.ticket_id == ticket.id)
                )
            ).all()
            attachment_paths = [str(_storage_dir() / a.path) for a in attachments]

            prev_run = await session.scalar(
                select(Run)
                .where(
                    Run.ticket_id == ticket.id,
                    Run.agent_id == agent.id,
                    Run.id != run.id,
                    Run.session_id.is_not(None),
                )
                .order_by(Run.started_at.desc())
                .limit(1)
            )

            ctx = RunContext(
                run_id=run.id,
                workspace_id=workspace.id,
                agent_id=agent.id,
                agent_model=agent.model,
                ticket_id=ticket.id,
                repo_path=workspace.repo_path,
                prompt=prompt,
                attachments=attachment_paths,
                prev_session_id=prev_run.session_id if prev_run else None,
                guardrails=workspace.guardrails or {},
                cancel_event=asyncio.Event(),
            )
            _CANCEL_EVENTS[run.id] = ctx.cancel_event

            tool_cls = TOOLS[agent.tool_kind]
            tool = tool_cls()

            text_buffer: list[str] = []
            terminal: AdapterEvent | None = None

            async for ev in tool.run(ctx):
                if ev.type == "run_ended":
                    terminal = ev
                else:
                    _accumulate_text(text_buffer, ev)
                await event_bus.publish(
                    session,
                    run_id=run.id,
                    workspace_id=workspace.id,
                    type=ev.type,
                    payload=ev.payload,
                )

            await _finish_run(session, run, ticket, agent, terminal, "".join(text_buffer))

        except Exception as exc:  # noqa: BLE001 - must never leave run/agent stuck
            await session.rollback()
            run = await session.get(Run, run_id)
            ticket = await session.get(Ticket, run.ticket_id)
            agent = await session.get(Agent, run.agent_id)
            run.status = "failed"
            run.error = str(exc)
            run.ended_at = _now()
            await _block_ticket(
                session, ticket, agent, f"Run gagal karena error internal: {exc}"
            )
            agent.status = "idle"
            await session.commit()
        finally:
            _CANCEL_EVENTS.pop(run_id, None)


async def _build_prompt_for(session, workspace: Workspace, agent: Agent, ticket: Ticket) -> str:
    roster = (
        await session.scalars(select(Agent).where(Agent.workspace_id == workspace.id))
    ).all()
    team_roster = [AgentInfo(name=a.name, role=a.role) for a in roster]
    agent_info = AgentInfo(name=agent.name, role=agent.role, system_prompt=agent.system_prompt)

    attachments = (
        await session.scalars(select(Attachment).where(Attachment.ticket_id == ticket.id))
    ).all()
    attachment_names = [a.filename for a in attachments]

    comments = (
        await session.scalars(
            select(Comment)
            .where(Comment.ticket_id == ticket.id)
            .order_by(Comment.created_at.desc())
            .limit(5)
        )
    ).all()
    comments = list(reversed(comments))
    recent_comments = []
    for c in comments:
        author = "system"
        if c.author_agent_id:
            author_agent = await session.get(Agent, c.author_agent_id)
            author = author_agent.name if author_agent else "unknown"
        recent_comments.append(
            CommentInfo(author=author, body=c.body, created_at=c.created_at.isoformat())
        )

    prior_runs = (
        await session.scalars(
            select(Run)
            .where(Run.ticket_id == ticket.id, Run.status == "done", Run.report.is_not(None))
            .order_by(Run.started_at)
        )
    ).all()
    previous_summaries = [
        r.report.get("summary") for r in prior_runs if r.report and r.report.get("summary")
    ]

    # "Review round" = how many prior runs on this ticket were done by an agent in a
    # reviewer role (lead/qa/pentester), regardless of which reviewer. Their summaries
    # double as the previous-review feedback shown to the anti-loop block.
    review_round = 0
    previous_review_feedback: list[str] = []
    if agent.role in {"lead", "qa", "pentester"}:
        for r in prior_runs:
            run_agent = await session.get(Agent, r.agent_id)
            if run_agent is not None and run_agent.role in {"lead", "qa", "pentester"}:
                review_round += 1
                if r.report and r.report.get("summary"):
                    previous_review_feedback.append(r.report["summary"])

    ticket_info = TicketInfo(
        key=ticket.key,
        title=ticket.title,
        status=ticket.status,
        priority=ticket.priority,
        description=ticket.description or "",
    )

    return build_prompt(
        agent_info,
        workspace.repo_path,
        team_roster,
        ticket_info,
        attachments=attachment_names,
        recent_comments=recent_comments,
        previous_summaries=previous_summaries,
        review_round=review_round,
        previous_review_feedback=previous_review_feedback,
    )


async def _finish_run(
    session: AsyncSession,
    run: Run,
    ticket: Ticket,
    agent: Agent,
    terminal: AdapterEvent | None,
    accumulated_text: str,
) -> None:
    if terminal is None:
        # Adapter misbehaved: no run_ended event at all. Treat like a failure.
        run.status = "failed"
        run.error = "adapter finished without a run_ended event"
        run.ended_at = _now()
        await _block_ticket(session, ticket, agent, f"Run gagal: {run.error}")
        agent.status = "idle"
        await session.commit()
        return

    status = terminal.payload.get("status")
    run.session_id = terminal.payload.get("session_id") or run.session_id
    run.tokens_in = int(terminal.payload.get("tokens_in") or 0)
    run.tokens_out = int(terminal.payload.get("tokens_out") or 0)
    run.cost = float(terminal.payload.get("cost") or 0.0)
    run.ended_at = _now()
    ticket.cost_used = (ticket.cost_used or 0.0) + run.cost

    if status == "cancelled":
        run.status = "cancelled"
        agent.status = "idle"
        await session.commit()
        return

    if status == "failed":
        run.status = "failed"
        run.error = terminal.payload.get("error") or "run failed"
        await _block_ticket(session, ticket, agent, f"Run gagal: {run.error}")
        agent.status = "idle"
        await session.commit()
        return

    # status == "done" -> parse the accumulated assistant text for the ```map block.
    valid_names = {
        a.name
        for a in (
            await session.scalars(
                select(Agent).where(Agent.workspace_id == ticket.workspace_id)
            )
        ).all()
    }
    parsed = parse_report(
        accumulated_text, agent.role, valid_names, actor_name=agent.name
    )

    if not parsed.ok:
        run.status = "failed"
        run.error = parsed.reason
        run.report = None
        tail = accumulated_text[-_TAIL_CHARS:]
        await _block_ticket(
            session,
            ticket,
            agent,
            f"Blok ```map hilang/rusak ({parsed.reason}). Output terakhir agent:\n\n{tail}",
        )
        agent.status = "idle"
        await session.commit()
        return

    allowed, reason = can_transition(ticket.status, parsed.status, agent.role)
    if not allowed:
        run.status = "failed"
        run.error = reason
        run.report = None
        await _block_ticket(
            session,
            ticket,
            agent,
            f"Transisi status dari ```map ditolak state machine: {reason}",
        )
        agent.status = "idle"
        await session.commit()
        return

    run.status = "done"
    old_status = ticket.status
    ticket.status = parsed.status
    run.report = {
        "status": parsed.status,
        "summary": parsed.summary,
        "mention": parsed.valid_mentions,
        "unknown_mentions": parsed.unknown_mentions,
        "tickets": [
            {
                "title": t.title,
                "description": t.description,
                "assignee": t.assignee,
                "priority": t.priority,
            }
            for t in parsed.tickets
        ],
    }

    comment = Comment(
        ticket_id=ticket.id, author_agent_id=agent.id, is_system=False, body=parsed.summary
    )
    session.add(comment)
    await session.flush()

    if old_status != parsed.status:
        await _write_system_comment(
            session, ticket.id, f"Status changed from {old_status} to {parsed.status}"
        )

    if parsed.valid_mentions:
        mentioned = (
            await session.scalars(
                select(Agent).where(
                    Agent.workspace_id == ticket.workspace_id, Agent.name.in_(parsed.valid_mentions)
                )
            )
        ).all()
        for mentioned_agent in mentioned:
            session.add(CommentMention(comment_id=comment.id, agent_id=mentioned_agent.id))

    if parsed.tickets:
        from app.api.tickets import _next_key  # reuse the same atomic-counter key logic

        workspace = await session.get(Workspace, ticket.workspace_id)
        for draft in parsed.tickets:
            assignee_id = None
            if draft.assignee:
                assignee = await session.scalar(
                    select(Agent).where(
                        Agent.workspace_id == ticket.workspace_id, Agent.name == draft.assignee
                    )
                )
                assignee_id = assignee.id if assignee else None
            child = Ticket(
                workspace_id=ticket.workspace_id,
                key=await _next_key(session, workspace),
                title=draft.title,
                description=draft.description,
                status="todo",
                priority=draft.priority,
                assignee_id=assignee_id,
                parent_id=ticket.id,
            )
            session.add(child)

    agent.status = "idle"
    await session.commit()


async def stop(run_id: str) -> bool:
    """Signal cancellation for a running run. Returns True if a running task was found."""
    cancel_event = _CANCEL_EVENTS.get(run_id)
    if cancel_event is None:
        return False
    cancel_event.set()
    return True


async def cancel_queued(agent_id: str, run_id: str) -> bool:
    """Remove a not-yet-started run from its agent's pending FIFO queue.

    Used when stopping a `queued` run: it hasn't reached `execute()` yet, so there's no
    cancel_event to signal — the caller is responsible for marking the Run row cancelled.
    """
    async with _LOCK:
        queue = _PENDING.get(agent_id)
        if queue and run_id in queue:
            queue.remove(run_id)
            return True
    return False
