import re

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent_or_404
from app.api.errors import AppError
from app.api.tickets import _get_ticket_or_404
from app.db.models import Agent, Comment, CommentMention
from app.db.session import get_session
from app.schemas.ticket import CommentCreate, CommentOut

comments_router = APIRouter(prefix="/tickets/{key}/comments", tags=["comments"])

# @nama-agent — slug of letters/digits/hyphens, e.g. "@eng-1".
MENTION_RE = re.compile(r"@([a-zA-Z0-9][a-zA-Z0-9-]*)")


@comments_router.get("", response_model=list[CommentOut])
async def list_comments(key: str, session: AsyncSession = Depends(get_session)):
    ticket = await _get_ticket_or_404(session, key)
    comments = (
        await session.scalars(
            select(Comment).where(Comment.ticket_id == ticket.id).order_by(Comment.created_at)
        )
    ).all()
    return [await _to_out(session, c) for c in comments]


@comments_router.post("", response_model=CommentOut, status_code=201)
async def create_comment(key: str, body: CommentCreate, session: AsyncSession = Depends(get_session)):
    ticket = await _get_ticket_or_404(session, key)

    if body.author_agent_id is not None:
        await _get_agent_or_404(session, body.author_agent_id)

    comment = Comment(
        ticket_id=ticket.id,
        author_agent_id=body.author_agent_id,
        is_system=False,
        body=body.body,
    )
    session.add(comment)
    await session.flush()

    names = set(MENTION_RE.findall(body.body))
    if names:
        mentioned_agents = (
            await session.scalars(
                select(Agent).where(Agent.workspace_id == ticket.workspace_id, Agent.name.in_(names))
            )
        ).all()
        for agent in mentioned_agents:
            if agent.id == body.author_agent_id:
                continue  # self-mention discarded
            session.add(CommentMention(comment_id=comment.id, agent_id=agent.id))

    await session.commit()
    await session.refresh(comment)
    return await _to_out(session, comment)


async def _to_out(session: AsyncSession, comment: Comment) -> CommentOut:
    agent_ids = (
        await session.scalars(
            select(CommentMention.agent_id).where(CommentMention.comment_id == comment.id)
        )
    ).all()
    names: list[str] = []
    if agent_ids:
        names = list(
            (await session.scalars(select(Agent.name).where(Agent.id.in_(agent_ids)))).all()
        )
    return CommentOut(**CommentOut.model_validate(comment).model_dump(exclude={"mentions"}), mentions=names)
