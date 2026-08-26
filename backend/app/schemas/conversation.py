"""Pydantic schemas for conversations (chat, separated from ticket comments)."""

from datetime import datetime

from pydantic import BaseModel


class ConversationAttachmentOut(BaseModel):
    id: str
    conversation_id: str
    message_id: str | None = None
    filename: str
    content_type: str
    size_bytes: int
    path: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationMessageOut(BaseModel):
    id: str
    conversation_id: str
    run_id: str | None = None
    author_agent_id: str | None = None
    is_system: bool
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: str
    workspace_id: str
    title: str
    linked_ticket_key: str | None = None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None

    model_config = {"from_attributes": True}


class ConversationCreate(BaseModel):
    title: str
    linked_ticket_key: str | None = None


class ConversationMessageCreate(BaseModel):
    body: str
