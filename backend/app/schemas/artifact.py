from pydantic import BaseModel

from app.schemas.ticket import AttachmentOut


class ArtifactAttachmentOut(AttachmentOut):
    ticket_key: str
    ticket_title: str


class ArtifactGroupOut(BaseModel):
    id: str | None  # None for the "ungrouped" bucket
    name: str
    attachments: list[ArtifactAttachmentOut]
