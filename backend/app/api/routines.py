"""Routine CRUD + manual trigger — scheduled agent tasks without a ticket.

Routines are fired by the in-process scheduler (core/routine_scheduler.py) or
manually via POST /routines/{id}/run. Execution reuses the orchestrator's
routine-run path (Run with ticket_id=NULL, trigger="routine").
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent_or_404
from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.core.auth import WorkspaceRole, require_routine_role, require_workspace_role
from app.core import orchestrator
from app.core.guardrails import GuardrailBlocked
from app.core.settings_store import SettingsLoadError
from app.db import session as db_session
from app.db.models import Routine
from app.db.session import get_session
from app.schemas.routine import RoutineCreate, RoutineOut, RoutineUpdate

workspace_routines_router = APIRouter(
    prefix="/workspaces/{workspace_id}/routines", tags=["routines"]
)
routines_router = APIRouter(prefix="/routines", tags=["routines"])


async def _get_routine_or_404(session: AsyncSession, routine_id: str) -> Routine:
    routine = await session.get(Routine, routine_id)
    if routine is None:
        raise AppError(404, "not_found", f"routine {routine_id} not found")
    return routine


@workspace_routines_router.get("", response_model=list[RoutineOut])
async def list_routines(
    workspace_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.viewer)),
):
    await _get_workspace_or_404(session, workspace_id)
    result = await session.scalars(
        select(Routine).where(Routine.workspace_id == workspace_id).order_by(Routine.created_at)
    )
    return result.all()


@workspace_routines_router.post("", response_model=RoutineOut, status_code=201)
async def create_routine(
    workspace_id: str,
    body: RoutineCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.editor)),
):
    await _get_workspace_or_404(session, workspace_id)
    if body.agent_id is not None:
        await _get_agent_or_404(session, body.agent_id)

    routine = Routine(
        workspace_id=workspace_id,
        name=body.name,
        prompt=body.prompt,
        interval_minutes=body.interval_minutes,
        mode=body.mode,
        agent_id=body.agent_id,
        status="idle",
    )
    session.add(routine)
    await session.commit()
    await session.refresh(routine)
    return routine


@routines_router.patch("/{routine_id}", response_model=RoutineOut)
async def update_routine(
    routine_id: str,
    body: RoutineUpdate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_routine_role(WorkspaceRole.editor)),
):
    routine = await _get_routine_or_404(session, routine_id)
    if body.name is not None:
        routine.name = body.name
    if body.prompt is not None:
        routine.prompt = body.prompt
    if body.interval_minutes is not None:
        routine.interval_minutes = body.interval_minutes
    if body.mode is not None:
        routine.mode = body.mode
    if body.agent_id is not None:
        await _get_agent_or_404(session, body.agent_id)
        routine.agent_id = body.agent_id
    if body.status is not None:
        routine.status = body.status
    await session.commit()
    await session.refresh(routine)
    return routine


@routines_router.delete("/{routine_id}", status_code=204)
async def delete_routine(
    routine_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_routine_role(WorkspaceRole.editor)),
):
    routine = await _get_routine_or_404(session, routine_id)
    await session.delete(routine)
    await session.commit()


@routines_router.post("/{routine_id}/run", response_model=RoutineOut)
async def run_routine_now(
    routine_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_routine_role(WorkspaceRole.editor)),
):
    """Manually trigger a routine run (test button)."""
    routine = await _get_routine_or_404(session, routine_id)
    if routine.status == "disabled":
        raise AppError(409, "routine_disabled", "routine is disabled")
    agent = await _get_agent_or_404(session, routine.agent_id) if routine.agent_id else None
    if agent is None or not agent.enabled or agent.status == "disabled":
        raise AppError(409, "no_agent", "routine has no valid agent assigned")

    try:
        await orchestrator.schedule_routine_run(session, db_session.async_session, routine, agent)
    except RuntimeError as exc:
        raise AppError(409, "workspace_paused", str(exc))
    except GuardrailBlocked as exc:
        raise AppError(409, "guardrail_blocked", str(exc))
    except SettingsLoadError as exc:
        raise AppError(500, "invalid_workspace_settings", str(exc))

    await session.refresh(routine)
    return routine
