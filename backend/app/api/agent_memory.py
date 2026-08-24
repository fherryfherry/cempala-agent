from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent_or_404
from app.api.errors import AppError
from app.core.report import MAX_MEMORY_NOTE_LEN
from app.db.models import AgentMemory
from app.db.session import get_session
from app.schemas.agent_memory import AgentMemoryCreate, AgentMemoryOut

agent_memory_router = APIRouter(prefix="/agents/{agent_id}/memory", tags=["agent-memory"])
memory_router = APIRouter(prefix="/agent-memory", tags=["agent-memory"])


@agent_memory_router.get("", response_model=list[AgentMemoryOut])
async def list_agent_memory(agent_id: str, session: AsyncSession = Depends(get_session)):
    await _get_agent_or_404(session, agent_id)
    result = await session.scalars(
        select(AgentMemory)
        .where(AgentMemory.agent_id == agent_id)
        .order_by(AgentMemory.created_at.desc())
    )
    return result.all()


@agent_memory_router.post("", response_model=AgentMemoryOut, status_code=201)
async def create_agent_memory(
    agent_id: str, body: AgentMemoryCreate, session: AsyncSession = Depends(get_session)
):
    await _get_agent_or_404(session, agent_id)

    note = body.note.strip()
    if not note:
        raise AppError(422, "empty_note", "memory note cannot be empty")
    # Same cap as agent-authored notes (report.py) — otherwise this manual path bypasses
    # the bounded-prompt-growth guarantee the whole feature is designed around.
    note = note[:MAX_MEMORY_NOTE_LEN]

    memory = AgentMemory(agent_id=agent_id, note=note, origin="owner", source_ticket_key=None)
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return memory


@memory_router.patch("/{memory_id}", response_model=AgentMemoryOut)
async def update_agent_memory(
    memory_id: str, body: AgentMemoryCreate, session: AsyncSession = Depends(get_session)
):
    memory = await session.get(AgentMemory, memory_id)
    if memory is None:
        raise AppError(404, "not_found", f"agent memory {memory_id} not found")

    note = body.note.strip()
    if not note:
        raise AppError(422, "empty_note", "memory note cannot be empty")
    memory.note = note[:MAX_MEMORY_NOTE_LEN]
    await session.commit()
    await session.refresh(memory)
    return memory


@memory_router.delete("/{memory_id}", status_code=204)
async def delete_agent_memory(memory_id: str, session: AsyncSession = Depends(get_session)):
    memory = await session.get(AgentMemory, memory_id)
    if memory is None:
        raise AppError(404, "not_found", f"agent memory {memory_id} not found")
    await session.delete(memory)
    await session.commit()
