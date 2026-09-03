"""MAP-023: POST /tickets/{key}/run, POST /runs/{id}/stop, GET /runs/{id},
GET /workspaces/{id}/runs.

Schedules real background work (`app.core.orchestrator`) — the request handler creates
the `Run` row and returns immediately with status `queued`/`running`; execution happens
in an `asyncio.Task` outside the request/response cycle, using its own DB session
(the request's session closes as soon as the response is sent).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent_or_404
from app.api.errors import AppError
from app.api.tickets import _get_ticket_or_404
from app.api.workspaces import _get_workspace_or_404
from app.core.auth import WorkspaceRole, require_run_role, require_ticket_role, require_workspace_role
from app.core import orchestrator
from app.core.guardrails import GuardrailBlocked
from app.core.settings_store import SettingsLoadError
from app.db import session as db_session
from app.db.models import Agent, Conversation, Event, Routine, Run, Ticket
from app.db.session import get_session
from app.schemas.run import EventOut, RunCreate, RunDetail, RunOut

# Run statuses a retry/resume is offered for — the "stopped without a real terminal
# report" outcomes. `cancelled` is included (owner-initiated Stop ⇒ Resume), but only
# when `error` is None: a `cancelled` run with `error` set was killed by a runtime
# guardrail (timeout/cost), which is a deliberate brake that must not be resumed
# (ADR-014). `done` is intentionally excluded: it finished, nothing to recover.
_RETRYABLE_RUN_STATUSES = frozenset({"failed", "interrupted", "cancelled"})

runs_router = APIRouter(prefix="/runs", tags=["runs"])
ticket_run_router = APIRouter(prefix="/tickets/{key}/run", tags=["runs"])
workspace_runs_router = APIRouter(prefix="/workspaces/{workspace_id}/runs", tags=["runs"])


async def _get_run_or_404(session: AsyncSession, run_id: str) -> Run:
    run = await session.get(Run, run_id)
    if run is None:
        raise AppError(404, "not_found", f"run {run_id} not found")
    return run


@ticket_run_router.post("", response_model=RunOut, status_code=201)
async def start_run(
    key: str,
    body: RunCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_ticket_role(WorkspaceRole.editor)),
):
    ticket = await _get_ticket_or_404(session, key)

    agent_id = body.agent_id or ticket.assignee_id
    if agent_id is None:
        raise AppError(
            422,
            "no_agent",
            "agent_id was not provided and the ticket has no assignee",
        )
    agent = await _get_agent_or_404(session, agent_id)

    try:
        run = await orchestrator.schedule(
            session,
            db_session.async_session,
            ticket=ticket,
            agent=agent,
            trigger="manual",
        )
    except RuntimeError as exc:
        raise AppError(409, "workspace_paused", str(exc))
    except GuardrailBlocked as exc:
        raise AppError(409, "guardrail_blocked", str(exc))
    except SettingsLoadError as exc:
        raise AppError(500, "invalid_workspace_settings", str(exc))

    return run


@runs_router.post("/{run_id}/retry", response_model=RunOut, status_code=201)
async def retry_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_run_role(WorkspaceRole.editor)),
):
    """Re-trigger the same agent on the same ticket a `failed`/`interrupted` run left

    behind, or re-trigger a `cancelled` run the owner stopped (UI calls this "Resume").
    Mechanically identical to pressing "Run" again for that ticket+agent —
    `orchestrator.execute()`'s session-continuation lookup (status-agnostic) already
    picks up the old run's `session_id` if one was persisted, so this needs no
    special-casing to "resume" (docs/02-tsd.md §4.2/§4.5).

    The one thing this endpoint does beyond a plain `schedule()` call: if the ticket
    is currently `blocked` (the common case after `failed` — see `_block_ticket`
    call sites in `core/orchestrator.py`), it clears that block the same way
    `PATCH /tickets/{key}` does when a human moves a ticket off `blocked`
    (`app/api/tickets.py::update_ticket`) — otherwise stale `handoff_depth`/loop
    history from *before* the failure could immediately re-trip a guardrail the
    owner just explicitly asked to move past by clicking Retry.
    """
    run = await _get_run_or_404(session, run_id)
    if run.status not in _RETRYABLE_RUN_STATUSES:
        raise AppError(
            409,
            "not_retryable",
            f"run {run_id} has status '{run.status}', not failed/interrupted/cancelled",
        )
    # A `cancelled` run with `error` set was killed by a runtime guardrail (timeout,
    # cost), not by the owner pressing Stop — never resume a deliberate brake.
    if run.status == "cancelled" and run.error is not None:
        raise AppError(
            409,
            "not_retryable",
            f"run {run_id} was cancelled by a guardrail, not the owner; it cannot be resumed",
        )
    if run.ticket_id is None:
        raise AppError(
            409,
            "not_retryable",
            f"run {run_id} has no ticket (routine/chat run) and cannot be retried",
        )

    ticket = await session.get(Ticket, run.ticket_id)
    agent = await session.get(Agent, run.agent_id)

    if ticket.status == "blocked":
        ticket.blocked_reason = None
        ticket.loop_reset_at = datetime.now(timezone.utc)
        ticket.handoff_depth = 0

    try:
        new_run = await orchestrator.schedule(
            session,
            db_session.async_session,
            ticket=ticket,
            agent=agent,
            trigger="manual",
        )
    except RuntimeError as exc:
        raise AppError(409, "workspace_paused", str(exc))
    except GuardrailBlocked as exc:
        raise AppError(409, "guardrail_blocked", str(exc))
    except SettingsLoadError as exc:
        raise AppError(500, "invalid_workspace_settings", str(exc))

    return new_run


@runs_router.post("/{run_id}/stop", response_model=RunOut)
async def stop_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_run_role(WorkspaceRole.editor)),
):
    run = await _get_run_or_404(session, run_id)

    if run.status == "running":
        await orchestrator.stop(run_id)
        # Actual status flip to "cancelled" happens in execute()'s run_ended handling,
        # once the adapter reacts to cancel_event and finishes. Return current state.
    elif run.status == "queued":
        await orchestrator.cancel_queued(run.agent_id, run_id)
        run.status = "cancelled"
        await session.commit()
        await session.refresh(run)

    return run


@runs_router.get("/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: str,
    offset: int = 0,
    limit: int = 500,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_run_role(WorkspaceRole.viewer)),
):
    run = await _get_run_or_404(session, run_id)
    events = (
        await session.scalars(
            select(Event)
            .where(Event.run_id == run_id)
            .order_by(Event.seq)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return RunDetail(
        **RunOut.model_validate(run).model_dump(),
        events=[EventOut.model_validate(e) for e in events],
    )


@workspace_runs_router.get("", response_model=list[RunOut])
async def list_runs(
    workspace_id: str,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.viewer)),
):
    await _get_workspace_or_404(session, workspace_id)

    stmt = (
        select(Run)
        .outerjoin(Ticket, Run.ticket_id == Ticket.id)
        .where(
            (Ticket.workspace_id == workspace_id)
            | (
                Run.routine_id.in_(
                    select(Routine.id).where(Routine.workspace_id == workspace_id)
                )
            )
            | (
                Run.conversation_id.in_(
                    select(Conversation.id).where(Conversation.workspace_id == workspace_id)
                )
            )
        )
    )
    if status is not None:
        stmt = stmt.where(Run.status == status)

    result = await session.scalars(stmt)
    return result.all()
