from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.db.models import Agent, Run
from app.db.session import get_session
from app.schemas.agent import AgentCreate, AgentOut, AgentUpdate

workspace_agents_router = APIRouter(prefix="/workspaces/{workspace_id}/agents", tags=["agents"])
agents_router = APIRouter(prefix="/agents", tags=["agents"])


async def _get_agent_or_404(session: AsyncSession, agent_id: str) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise AppError(404, "not_found", f"agent {agent_id} not found")
    return agent


@workspace_agents_router.get("", response_model=list[AgentOut])
async def list_agents(workspace_id: str, session: AsyncSession = Depends(get_session)):
    await _get_workspace_or_404(session, workspace_id)
    result = await session.scalars(select(Agent).where(Agent.workspace_id == workspace_id))
    return result.all()


@workspace_agents_router.post("", response_model=AgentOut, status_code=201)
async def create_agent(
    workspace_id: str, body: AgentCreate, session: AsyncSession = Depends(get_session)
):
    await _get_workspace_or_404(session, workspace_id)

    agent = Agent(
        workspace_id=workspace_id,
        name=body.name,
        role=body.role,
        model=body.model,
        tool_kind=body.tool_kind,
        system_prompt=body.system_prompt,
    )
    session.add(agent)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(409, "duplicate_name", f"agent name '{body.name}' already exists in workspace")
    await session.refresh(agent)
    return agent


@agents_router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: str, body: AgentUpdate, session: AsyncSession = Depends(get_session)):
    agent = await _get_agent_or_404(session, agent_id)

    for field in ("name", "role", "model", "tool_kind", "system_prompt", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(agent, field, value)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(409, "duplicate_name", f"agent name '{body.name}' already exists in workspace")
    await session.refresh(agent)
    return agent


@agents_router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, session: AsyncSession = Depends(get_session)):
    agent = await _get_agent_or_404(session, agent_id)

    active_run = await session.scalar(
        select(Run).where(Run.agent_id == agent_id, Run.status == "running")
    )
    if active_run is not None:
        raise AppError(409, "agent_has_active_run", f"agent {agent_id} has an active run")

    await session.delete(agent)
    await session.commit()
