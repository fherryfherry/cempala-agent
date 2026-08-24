from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.db.models import Agent, AgentMemory, Run
from app.db.session import get_session
from app.schemas.agent import AgentCreate, AgentListOut, AgentOut, AgentUpdate

workspace_agents_router = APIRouter(prefix="/workspaces/{workspace_id}/agents", tags=["agents"])
agents_router = APIRouter(prefix="/agents", tags=["agents"])


async def _get_agent_or_404(session: AsyncSession, agent_id: str) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise AppError(404, "not_found", f"agent {agent_id} not found")
    return agent


@workspace_agents_router.get("", response_model=list[AgentListOut])
async def list_agents(workspace_id: str, session: AsyncSession = Depends(get_session)):
    await _get_workspace_or_404(session, workspace_id)
    memory_count = (
        select(func.count())
        .where(AgentMemory.agent_id == Agent.id)
        .correlate(Agent)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Agent, memory_count.label("memory_count")).where(
                Agent.workspace_id == workspace_id
            )
        )
    ).all()
    return [
        AgentListOut.model_validate(
            {**AgentOut.model_validate(agent).model_dump(), "memory_count": count}
        )
        for agent, count in rows
    ]


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
        avatar_template=body.avatar_template,
        avatar_color=body.avatar_color,
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

    # avatar_template/avatar_color are the one pair where explicit null is meaningful
    # (clearing back to plain initials), so honor field presence over non-null values.
    if "avatar_template" in body.model_fields_set:
        agent.avatar_template = body.avatar_template
    if "avatar_color" in body.model_fields_set:
        agent.avatar_color = body.avatar_color

    # avatar_template/avatar_color are the one pair where explicit null is meaningful
    # (clearing back to plain initials), so honor field presence over non-null values.
    if "avatar_template" in body.model_fields_set:
        agent.avatar_template = body.avatar_template
    if "avatar_color" in body.model_fields_set:
        agent.avatar_color = body.avatar_color

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
