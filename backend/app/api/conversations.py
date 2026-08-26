"""Conversations API — chat between owner and the PM, stored separately from
ticket comments (tables `conversation`/`conversation_message`/`conversation_attachment`).

Flow: owner posts a message -> `ConversationMessage` row (author NULL = owner) ->
`orchestrator.schedule_chat()` triggers a PM chat run (no ticket). The PM's reply
(`summary` in the ```map block) lands back in the conversation; `comments[]`
lets the PM follow up on real tickets as part of the same run.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent_or_404
from app.api.attachments import _sanitize_filename, _storage_dir
from app.api.comments import _is_approval
from app.api.errors import AppError
from app.api.sprints import activate_sprint
from app.api.tickets import _get_ticket_or_404
from app.api.workspaces import _get_workspace_or_404
from app.core import orchestrator
from app.core.guardrails import GuardrailBlocked
from app.core.report import SprintDraft, TicketDraft
from app.db import session as db_session
from app.db.models import (
    Agent,
    Conversation,
    ConversationAttachment,
    ConversationMessage,
    Sprint,
    Workspace,
)
from app.db.session import get_session
from app.schemas.conversation import (
    ConversationAttachmentOut,
    ConversationCreate,
    ConversationMessageCreate,
    ConversationMessageOut,
    ConversationOut,
)

MAX_SIZE = 25 * 1024 * 1024
_CHUNK = 1024 * 1024

workspace_conversations_router = APIRouter(
    prefix="/workspaces/{workspace_id}/conversations", tags=["conversations"]
)
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


def _conversation_storage_dir(conversation_id: str) -> Path:
    d = _storage_dir() / "conversations" / conversation_id
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _get_conversation_or_404(session: AsyncSession, conversation_id: str) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise AppError(404, "not_found", f"conversation {conversation_id} not found")
    return conversation


@workspace_conversations_router.get("", response_model=list[ConversationOut])
async def list_conversations(
    workspace_id: str, session: AsyncSession = Depends(get_session)
):
    await _get_workspace_or_404(session, workspace_id)
    conversations = (
        await session.scalars(
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id)
            .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc())
        )
    ).all()
    return conversations


@workspace_conversations_router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(
    workspace_id: str,
    body: ConversationCreate,
    session: AsyncSession = Depends(get_session),
):
    await _get_workspace_or_404(session, workspace_id)
    if body.linked_ticket_key:
        await _get_ticket_or_404(session, body.linked_ticket_key)
    conversation = Conversation(
        workspace_id=workspace_id,
        title=body.title.strip() or "Chat",
        linked_ticket_key=body.linked_ticket_key,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


@conversations_router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: str, session: AsyncSession = Depends(get_session)
):
    return await _get_conversation_or_404(session, conversation_id)


@conversations_router.get("/{conversation_id}/messages", response_model=list[ConversationMessageOut])
async def list_messages(
    conversation_id: str,
    limit: int | None = None,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    await _get_conversation_or_404(session, conversation_id)
    stmt = (
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.desc())
    )
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    messages = (await session.scalars(stmt)).all()
    return list(reversed(messages))


@conversations_router.post(
    "/{conversation_id}/messages", response_model=ConversationMessageOut, status_code=201
)
async def create_message(
    conversation_id: str,
    body: ConversationMessageCreate,
    session: AsyncSession = Depends(get_session),
):
    """Owner message in a conversation: persisted, then triggers a PM chat run.

    Only the PM may chat (one enabled PM per workspace, like the old chat page). A
    message sent while a chat run is already queued/running is still persisted and
    returns normally — it queues behind the active run and the PM's prompt includes
    the whole transcript, so it gets answered when the current run finishes.
    """
    conversation = await _get_conversation_or_404(session, conversation_id)
    if not body.body.strip():
        raise AppError(422, "empty_body", "message body must not be empty")

    workspace = await session.get(Workspace, conversation.workspace_id)
    if workspace is None:
        raise AppError(404, "not_found", "workspace not found")

    pm = await session.scalar(
        select(Agent).where(
            Agent.workspace_id == workspace.id,
            Agent.role == "pm",
            Agent.enabled.is_(True),
            Agent.status != "disabled",
        )
    )
    if pm is None:
        raise AppError(
            422,
            "no_pm",
            "no enabled PM agent in this workspace — chat needs a PM to talk to",
        )

    message = ConversationMessage(
        conversation_id=conversation.id,
        author_agent_id=None,
        is_system=False,
        body=body.body,
    )
    session.add(message)
    conversation.last_message_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(message)

    # Owner approving a pending sprint proposal (PM declared sprints[]/tickets[]
    # while no sprint was active — orchestrator._finish_chat_run held it instead
    # of creating anything, owner request: PM must propose+get approval first).
    # Nothing for the PM to answer here, so this replaces the normal chat-run
    # scheduling below rather than running alongside it.
    if conversation.pending_proposal and _is_approval(body.body.strip()):
        proposal = await _claim_pending_proposal(session, conversation)
        if proposal is not None:
            try:
                await _execute_pending_proposal(session, conversation, workspace, pm, proposal)
            except Exception as exc:
                # `_claim_pending_proposal` already cleared pending_proposal, so a
                # retry ("oke" again) can't double-execute — the owner has to ask
                # the PM to re-propose, same as any other failed action in chat.
                await orchestrator._write_system_message(
                    session,
                    conversation,
                    f"Proposal disetujui tapi gagal dieksekusi: {exc}. Minta PM mengusulkan ulang.",
                    run_id=None,
                    workspace_id=workspace.id,
                )
                await session.commit()
        return message

    # Skip scheduling if a chat run is already in flight for this conversation —
    # the running prompt already includes this message in its transcript.
    if not await orchestrator._has_active_chat_run(session, conversation.id):
        try:
            await orchestrator.schedule_chat(
                session,
                db_session.async_session,
                conversation=conversation,
                agent=pm,
            )
        except (GuardrailBlocked, RuntimeError):
            # Guardrail trips already wrote a System message on the conversation
            # (schedule_chat); a paused workspace surfaces as a message too. The
            # owner's message itself is already persisted either way.
            pass
    return message


async def _claim_pending_proposal(
    session: AsyncSession, conversation: Conversation
) -> dict | None:
    """Atomically consume `conversation.pending_proposal`, so two near-simultaneous
    approval messages (e.g. a double-clicked send) can't both execute the same
    proposal. A plain `if conversation.pending_proposal` check isn't enough —
    both requests can load the same pre-clear value before either commits — so
    this does a DB-level compare-and-clear (`UPDATE ... WHERE pending_proposal
    = <value just read>`) and only the request whose row actually changed
    (`rowcount == 1`) gets to execute it. Returns the parsed proposal dict, or
    None if it was already claimed by a concurrent request.
    """
    raw = conversation.pending_proposal
    result = await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id, Conversation.pending_proposal == raw)
        .values(pending_proposal=None)
    )
    await session.commit()
    if result.rowcount == 0:
        return None
    conversation.pending_proposal = None
    return json.loads(raw)


async def _execute_pending_proposal(
    session: AsyncSession,
    conversation: Conversation,
    workspace: Workspace,
    pm: Agent,
    data: dict,
) -> None:
    """Owner just approved a sprint proposal (already claimed by
    `_claim_pending_proposal`): actually create the sprint(s)/ticket(s) the PM
    proposed (mirrors the immediate-creation path in
    `orchestrator._finish_chat_run`) and activate whichever *newly created*
    sprint ended up active — `create_tickets_and_sprints` only bootstraps one
    sprint active per batch (matching the single-active-sprint invariant), so
    this never fights itself demoting sprints it just activated.
    """
    sprints = [SprintDraft(**d) for d in data.get("sprints", [])]
    tickets = [TicketDraft(**d) for d in data.get("tickets", [])]

    tickets_report, epic_skip_notes, new_sprints, created_tickets = (
        await orchestrator.create_tickets_and_sprints(
            session,
            workspace,
            sprints=sprints,
            tickets=tickets,
            run_id=None,
            actor_name=pm.name,
        )
    )

    activated: list[Sprint] = []
    not_activated: list[Sprint] = []
    for sprint in new_sprints:
        if sprint.status == "active":
            await activate_sprint(session, sprint)
            activated.append(sprint)
        else:
            not_activated.append(sprint)

    await session.commit()

    for t in created_tickets:
        await orchestrator._auto_schedule_assignee(session, db_session.async_session, t)

    body_lines = ["Proposal disetujui owner."]
    if activated:
        body_lines.append("Sprint diaktifkan: " + ", ".join(s.name for s in activated))
    if not_activated:
        body_lines.append(
            "Sprint dibuat tapi TIDAK diaktifkan (sudah ada sprint aktif lain saat proposal "
            "ini dieksekusi): " + ", ".join(s.name for s in not_activated)
        )
    if tickets_report:
        body_lines.append(
            "Tiket dibuat:\n" + "\n".join(f"- `{t['key']}` {t['title']}" for t in tickets_report)
        )
    if epic_skip_notes:
        body_lines.append("; ".join(epic_skip_notes))
    if len(body_lines) == 1:
        body_lines.append("Tidak ada sprint/tiket baru di proposal ini.")

    await orchestrator._write_system_message(
        session, conversation, "\n\n".join(body_lines), run_id=None, workspace_id=workspace.id
    )
    await session.commit()


@conversations_router.get(
    "/{conversation_id}/attachments", response_model=list[ConversationAttachmentOut]
)
async def list_attachments(
    conversation_id: str, session: AsyncSession = Depends(get_session)
):
    await _get_conversation_or_404(session, conversation_id)
    attachments = (
        await session.scalars(
            select(ConversationAttachment).where(
                ConversationAttachment.conversation_id == conversation_id
            )
        )
    ).all()
    return attachments


@conversations_router.post(
    "/{conversation_id}/attachments", response_model=ConversationAttachmentOut, status_code=201
)
async def upload_attachment(
    conversation_id: str,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
):
    conversation = await _get_conversation_or_404(session, conversation_id)
    dest_dir = _conversation_storage_dir(conversation.id)
    dest_path = dest_dir / f"{uuid.uuid4().hex}-{_sanitize_filename(file.filename or 'file')}"

    size = 0
    try:
        with open(dest_path, "wb") as out:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > MAX_SIZE:
                    raise AppError(413, "file_too_large", "attachment exceeds 25 MB limit")
                out.write(chunk)
    except AppError:
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    attachment = ConversationAttachment(
        conversation_id=conversation.id,
        filename=file.filename or "file",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size,
        path=str(dest_path.relative_to(_storage_dir())),
    )
    session.add(attachment)
    conversation.last_message_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(attachment)
    return attachment


@conversations_router.get("/attachments/{attachment_id}/download")
async def download_attachment(
    attachment_id: str, session: AsyncSession = Depends(get_session)
):
    attachment = await session.get(ConversationAttachment, attachment_id)
    if attachment is None:
        raise AppError(404, "not_found", f"attachment {attachment_id} not found")
    path = _storage_dir() / attachment.path
    if not path.is_file():
        raise AppError(404, "not_found", "attachment file missing on disk")
    return FileResponse(path, filename=attachment.filename)


@conversations_router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: str, session: AsyncSession = Depends(get_session)
):
    attachment = await session.get(ConversationAttachment, attachment_id)
    if attachment is None:
        raise AppError(404, "not_found", f"attachment {attachment_id} not found")
    path = _storage_dir() / attachment.path
    if path.is_file():
        path.unlink(missing_ok=True)
    await session.delete(attachment)
    await session.commit()
