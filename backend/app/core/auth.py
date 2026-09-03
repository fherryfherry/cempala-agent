"""Login + per-workspace RBAC (ADR-016, supersedes ADR-005's no-auth posture).

Session = a signed `itsdangerous` cookie carrying only the user id (stateless — no
server-side session table). Deactivating a user takes effect on their very next
request because `get_current_user` re-checks `User.is_active` every time, even
though the cookie signature itself stays valid until it expires.

RBAC is two-axis: `User.is_superadmin` (manages the global user list, creates
workspaces, bypasses every per-workspace check) and `WorkspaceMember.role`
(viewer < editor < admin, scoped to one workspace). Most routers are keyed by an
entity other than `workspace_id` (a ticket `key`, a `run_id`, ...) — the
`_entity_workspace_dependency` factory below resolves the owning workspace via
that entity's existing `_get_x_or_404` helper (imported lazily to avoid import
cycles, since those routers import from here) before checking the role.
"""

from __future__ import annotations

import hmac
from enum import IntEnum

import bcrypt
from fastapi import Depends, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.config import INTERNAL_MCP_SECRET, settings
from app.db.models import Agent, Conversation, Routine, Ticket, User, WorkspaceMember
from app.db.session import get_session

SESSION_COOKIE = "map_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600
INTERNAL_SECRET_HEADER = "x-map-internal-secret"

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="map-session")

# Not a DB row — never persisted, never returned to a client, only ever the
# return value of get_current_user for a request authenticated via
# INTERNAL_SECRET_HEADER (the MCP server, see app/config.py's INTERNAL_MCP_SECRET
# docstring). is_superadmin=True so it bypasses every per-workspace check; the
# MCP tools it's used for never write a user_id anywhere.
_INTERNAL_USER = User(
    id="internal-mcp",
    email="internal-mcp@cempala.local",
    password_hash="",
    is_superadmin=True,
    is_active=True,
)

# bcrypt's own limit — truncate rather than let long passwords raise ValueError.
_MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:_MAX_PASSWORD_BYTES], bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode()[:_MAX_PASSWORD_BYTES], password_hash.encode())


def create_session_cookie(user_id: str) -> str:
    return _serializer.dumps(user_id)


def read_session_cookie(token: str) -> str | None:
    try:
        return _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    internal_secret = request.headers.get(INTERNAL_SECRET_HEADER)
    if internal_secret and hmac.compare_digest(internal_secret, INTERNAL_MCP_SECRET):
        return _INTERNAL_USER

    token = request.cookies.get(SESSION_COOKIE)
    user_id = read_session_cookie(token) if token else None
    if user_id is None:
        raise AppError(401, "unauthorized", "login required")
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError(401, "unauthorized", "login required")
    return user


async def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if not user.is_superadmin:
        raise AppError(403, "forbidden", "superadmin only")
    return user


class WorkspaceRole(IntEnum):
    viewer = 1
    editor = 2
    admin = 3


_ROLE_RANK = {r.name: r for r in WorkspaceRole}


async def _workspace_role_for(
    session: AsyncSession, user: User, workspace_id: str
) -> WorkspaceRole | None:
    if user.is_superadmin:
        return WorkspaceRole.admin
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id == workspace_id
        )
    )
    return _ROLE_RANK[member.role] if member else None


async def _check_workspace_role(
    session: AsyncSession, user: User, workspace_id: str, min_role: WorkspaceRole
) -> None:
    rank = await _workspace_role_for(session, user, workspace_id)
    if rank is None or rank < min_role:
        raise AppError(
            403, "forbidden", f"requires '{min_role.name}' access to this workspace"
        )


def require_workspace_role(min_role: WorkspaceRole):
    """For routes with `workspace_id` directly in the path."""

    async def dep(
        workspace_id: str,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> User:
        await _check_workspace_role(session, user, workspace_id, min_role)
        return user

    return dep


# --- entity -> workspace_id resolvers, for routes keyed by something else ---


async def _ticket_workspace_id(session: AsyncSession, key: str) -> str:
    from app.api.tickets import _get_ticket_or_404

    ticket = await _get_ticket_or_404(session, key)
    return ticket.workspace_id


async def _agent_workspace_id(session: AsyncSession, agent_id: str) -> str:
    from app.api.agents import _get_agent_or_404

    agent = await _get_agent_or_404(session, agent_id)
    return agent.workspace_id


async def _sprint_workspace_id(session: AsyncSession, sprint_id: str) -> str:
    from app.api.sprints import _get_sprint_or_404

    sprint = await _get_sprint_or_404(session, sprint_id)
    return sprint.workspace_id


async def _routine_workspace_id(session: AsyncSession, routine_id: str) -> str:
    from app.api.routines import _get_routine_or_404

    routine = await _get_routine_or_404(session, routine_id)
    return routine.workspace_id


async def _conversation_workspace_id(session: AsyncSession, conversation_id: str) -> str:
    from app.api.conversations import _get_conversation_or_404

    conversation = await _get_conversation_or_404(session, conversation_id)
    return conversation.workspace_id


async def _run_workspace_id(session: AsyncSession, run_id: str) -> str:
    from app.api.runs import _get_run_or_404

    run = await _get_run_or_404(session, run_id)
    if run.ticket_id is not None:
        ticket = await session.get(Ticket, run.ticket_id)
        return ticket.workspace_id
    if run.conversation_id is not None:
        conversation = await session.get(Conversation, run.conversation_id)
        return conversation.workspace_id
    if run.routine_id is not None:
        routine = await session.get(Routine, run.routine_id)
        return routine.workspace_id
    raise AppError(404, "not_found", f"run {run_id} has no workspace context")


async def _attachment_workspace_id(session: AsyncSession, attachment_id: str) -> str:
    from app.db.models import Attachment

    attachment = await session.get(Attachment, attachment_id)
    if attachment is None:
        raise AppError(404, "not_found", f"attachment {attachment_id} not found")
    ticket = await session.get(Ticket, attachment.ticket_id)
    return ticket.workspace_id


async def _conversation_attachment_workspace_id(session: AsyncSession, attachment_id: str) -> str:
    from app.db.models import ConversationAttachment

    attachment = await session.get(ConversationAttachment, attachment_id)
    if attachment is None:
        raise AppError(404, "not_found", f"attachment {attachment_id} not found")
    conversation = await session.get(Conversation, attachment.conversation_id)
    return conversation.workspace_id


async def _memory_workspace_id(session: AsyncSession, memory_id: str) -> str:
    from app.db.models import AgentMemory

    memory = await session.get(AgentMemory, memory_id)
    if memory is None:
        raise AppError(404, "not_found", f"agent memory {memory_id} not found")
    agent = await session.get(Agent, memory.agent_id)
    return agent.workspace_id


def _entity_workspace_dependency(resolver, param_name: str):
    """Build a `require_x_role(min_role)` factory from a `(session, id) -> workspace_id`
    resolver and the name of the path param carrying that id — reads it off
    `request.path_params` rather than redeclaring it, since the route function
    already declares the typed param for FastAPI's own validation/docs."""

    def factory(min_role: WorkspaceRole):
        async def dep(
            request: Request,
            user: User = Depends(get_current_user),
            session: AsyncSession = Depends(get_session),
        ) -> User:
            workspace_id = await resolver(session, request.path_params[param_name])
            await _check_workspace_role(session, user, workspace_id, min_role)
            return user

        return dep

    return factory


require_ticket_role = _entity_workspace_dependency(_ticket_workspace_id, "key")
require_agent_role = _entity_workspace_dependency(_agent_workspace_id, "agent_id")
require_sprint_role = _entity_workspace_dependency(_sprint_workspace_id, "sprint_id")
require_routine_role = _entity_workspace_dependency(_routine_workspace_id, "routine_id")
require_conversation_role = _entity_workspace_dependency(
    _conversation_workspace_id, "conversation_id"
)
require_run_role = _entity_workspace_dependency(_run_workspace_id, "run_id")
require_attachment_role = _entity_workspace_dependency(_attachment_workspace_id, "attachment_id")
require_conversation_attachment_role = _entity_workspace_dependency(
    _conversation_attachment_workspace_id, "attachment_id"
)
require_memory_role = _entity_workspace_dependency(_memory_workspace_id, "memory_id")


async def bootstrap_admin(async_session) -> None:
    """Create the first superadmin from `ADMIN_EMAIL`/`ADMIN_PASSWORD` if the `user`
    table is empty. Never raises — a missing/incomplete env config just means nobody
    can log in yet (logged, not fatal), matching this app's local-first posture."""
    import logging

    logger = logging.getLogger(__name__)
    async with async_session() as session:
        existing = await session.scalar(select(User.id).limit(1))
        if existing is not None:
            return
        if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
            logger.warning(
                "no user accounts exist and ADMIN_EMAIL/ADMIN_PASSWORD are not set — "
                "nobody can log in until an admin is created"
            )
            return
        session.add(
            User(
                email=settings.ADMIN_EMAIL,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                is_superadmin=True,
            )
        )
        await session.commit()
        logger.info("bootstrapped first superadmin account: %s", settings.ADMIN_EMAIL)
