import re

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent_or_404
from app.api.errors import AppError
from app.api.tickets import _get_ticket_or_404
from app.core import orchestrator
from app.core.guardrails import GuardrailBlocked
from app.core.settings_store import SettingsLoadError
from app.db import session as db_session
from app.db.models import Agent, Comment, CommentMention
from app.db.session import get_session
from app.schemas.ticket import CommentCreate, CommentOut

comments_router = APIRouter(prefix="/tickets/{key}/comments", tags=["comments"])

# @nama-agent — slug of letters/digits/hyphens, e.g. "@eng-1".
MENTION_RE = re.compile(r"@([a-zA-Z0-9][a-zA-Z0-9-]*)")

# Owner replies that count as explicit approval of the PM's plan (Bagian B design).
# Matched against the stripped owner comment, case-insensitive, tolerating the chat's
# "@pmname " prefix so "oke lanjut" / "Lanjut" / "acc" / "gas" etc. all count.
APPROVAL_RE = re.compile(
    r"^\s*(?:@[a-zA-Z0-9][a-zA-Z0-9-]*\s+)?"
    r"(oke|ok|okay|lanjut|setuju|acc|approved|sip|gas|gass|gaskeun|kerjakan|boleh|silahkan|silakan)\b",
    re.IGNORECASE,
)


def _is_approval(body: str) -> bool:
    return bool(APPROVAL_RE.match(body.strip()))


@comments_router.get("", response_model=list[CommentOut])
async def list_comments(
    key: str,
    limit: int | None = None,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    ticket = await _get_ticket_or_404(session, key)
    stmt = (
        select(Comment)
        .where(Comment.ticket_id == ticket.id)
        .order_by(Comment.created_at.desc())  # most recent first
    )
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    comments = (await session.scalars(stmt)).all()
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

    # Owner (human) approval of a PM's plan: first explicit approval marks the ticket
    # as approved, unlocking tickets[] for subsequent PM runs (docs/03-agent-design.md §4).
    if body.author_agent_id is None and ticket.approved_at is None and _is_approval(body.body.strip()):
        from datetime import datetime, timezone

        ticket.approved_at = datetime.now(timezone.utc)
        session.add(
            Comment(
                ticket_id=ticket.id,
                author_agent_id=None,
                is_system=True,
                body="Plan disetujui owner — PM boleh membuat sub-tiket.",
            )
        )

    names = set(MENTION_RE.findall(body.body))
    to_trigger: list[Agent] = []
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
            # Owner comments AND agent-authored ones (e.g. the MCP `post_comment` tool,
            # used mid-run to leave a follow-up on any ticket) trigger a run the same
            # way — self-mention is already excluded above, so this can't double-fire
            # an agent against its own report.
            if agent.enabled and agent.status != "disabled":
                to_trigger.append(agent)

    await session.commit()

    for agent in to_trigger:
        # trigger="mention": a human nudging an agent, distinct from "handoff" (agent
        # report driven). Does not touch ticket.handoff_depth — that guardrail tracks
        # agent-to-agent chain risk, not human-initiated runs.
        try:
            await orchestrator.schedule(
                session,
                db_session.async_session,
                ticket=ticket,
                agent=agent,
                trigger="mention",
            )
        except (GuardrailBlocked, RuntimeError, SettingsLoadError):
            # schedule() already recorded the reason (blocked ticket / paused
            # workspace) if applicable; the comment itself still succeeds. A
            # malformed .cempala/settings.yaml is treated the same way.
            pass
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
