"""MAP: read-only Artifacts menu — agent-generated attachments grouped by ArtifactGroup,

across every ticket in a workspace. Groups are created dynamically by agents via the ```map
`artifacts:` field (orchestrator._publish_artifacts); nothing here creates or renames a group.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workspaces import _get_workspace_or_404
from app.db.models import ArtifactGroup, Attachment, Ticket
from app.db.session import get_session
from app.schemas.artifact import ArtifactAttachmentOut, ArtifactGroupOut
from app.schemas.ticket import AttachmentOut

workspace_artifacts_router = APIRouter(prefix="/workspaces/{workspace_id}/artifacts", tags=["artifacts"])


@workspace_artifacts_router.get("", response_model=list[ArtifactGroupOut])
async def list_artifacts(workspace_id: str, session: AsyncSession = Depends(get_session)):
    await _get_workspace_or_404(session, workspace_id)

    rows = (
        await session.execute(
            select(Attachment, Ticket.key, Ticket.title)
            .join(Ticket, Attachment.ticket_id == Ticket.id)
            .where(Ticket.workspace_id == workspace_id, Attachment.origin == "agent")
            .order_by(Attachment.created_at)
        )
    ).all()

    groups = (
        await session.scalars(select(ArtifactGroup).where(ArtifactGroup.workspace_id == workspace_id))
    ).all()
    group_names = {g.id: g.name for g in groups}

    buckets: dict[str | None, list[ArtifactAttachmentOut]] = {}
    for attachment, ticket_key, ticket_title in rows:
        buckets.setdefault(attachment.group_id, []).append(
            ArtifactAttachmentOut(
                **AttachmentOut.model_validate(attachment).model_dump(),
                ticket_key=ticket_key,
                ticket_title=ticket_title,
            )
        )

    result = [
        ArtifactGroupOut(id=group_id, name=group_names.get(group_id, "Unknown"), attachments=attachments)
        for group_id, attachments in buckets.items()
        if group_id is not None
    ]
    result.sort(key=lambda g: g.name.lower())

    if None in buckets:
        result.append(ArtifactGroupOut(id=None, name="Ungrouped", attachments=buckets[None]))

    return result
