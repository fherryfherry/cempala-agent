"""MAP-011: attachment upload/download/delete. Storage is fixed under STORAGE_DIR/attachments/,
unrelated to any workspace's repo_path (see CLAUDE.md security posture)."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.tickets import _get_ticket_or_404
from app.core.auth import WorkspaceRole, require_attachment_role, require_ticket_role
from app.config import settings
from app.db.models import Attachment
from app.db.session import get_session
from app.schemas.ticket import AttachmentOut

MAX_SIZE = 25 * 1024 * 1024
_CHUNK = 1024 * 1024

ticket_attachments_router = APIRouter(prefix="/tickets/{key}/attachments", tags=["attachments"])
attachments_router = APIRouter(prefix="/attachments", tags=["attachments"])


def _storage_dir() -> Path:
    p = Path(settings.STORAGE_DIR)
    if not p.is_absolute():
        # STORAGE_DIR default "../storage" is relative to backend/, not to cwd — resolve
        # against this file's location (app/api/attachments.py -> app -> backend) so it's
        # correct regardless of where the process was launched from.
        p = Path(__file__).resolve().parent.parent.parent / p
    return p.resolve()


def _attachments_dir() -> Path:
    return _storage_dir() / "attachments"


def _sanitize_filename(name: str) -> str:
    """Flatten to a basename only — strips directories and ".." components. The uuid
    prefix added by the caller guarantees no collision/overwrite even for identical names."""
    base = os.path.basename((name or "").replace("\\", "/"))
    return base if base and base not in (".", "..") else "file"


async def _get_attachment_or_404(session: AsyncSession, attachment_id: str) -> Attachment:
    attachment = await session.get(Attachment, attachment_id)
    if attachment is None:
        raise AppError(404, "not_found", f"attachment {attachment_id} not found")
    return attachment


@ticket_attachments_router.post("", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    key: str,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_ticket_role(WorkspaceRole.editor)),
):
    ticket = await _get_ticket_or_404(session, key)

    dest_dir = _attachments_dir() / ticket.id
    dest_dir.mkdir(parents=True, exist_ok=True)
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

    attachment = Attachment(
        ticket_id=ticket.id,
        filename=file.filename or "file",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size,
        path=str(dest_path.relative_to(_storage_dir())),
    )
    session.add(attachment)
    await session.commit()
    await session.refresh(attachment)
    return attachment


@attachments_router.get("/{attachment_id}")
async def download_attachment(
    attachment_id: str,
    inline: bool = False,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_attachment_role(WorkspaceRole.viewer)),
):
    attachment = await _get_attachment_or_404(session, attachment_id)
    file_path = _storage_dir() / attachment.path
    if not file_path.is_file():
        raise AppError(404, "not_found", "attachment file missing on disk")
    return FileResponse(
        file_path,
        media_type=attachment.content_type,
        filename=attachment.filename,
        content_disposition_type="inline" if inline else "attachment",
    )


@attachments_router.delete("/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_attachment_role(WorkspaceRole.editor)),
):
    attachment = await _get_attachment_or_404(session, attachment_id)
    (_storage_dir() / attachment.path).unlink(missing_ok=True)
    await session.delete(attachment)
    await session.commit()
