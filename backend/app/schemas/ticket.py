from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Status = Literal["backlog", "todo", "in_progress", "review", "qa", "security", "done", "blocked"]
Priority = Literal["low", "medium", "high", "urgent"]


class TicketCreate(BaseModel):
    title: str
    description: str | None = None
    priority: Priority = "medium"
    assignee_id: str | None = None
    parent_id: str | None = None


class TicketUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: Priority | None = None
    assignee_id: str | None = None
    status: Status | None = None


class TicketOut(BaseModel):
    id: str
    workspace_id: str
    key: str
    title: str
    description: str | None
    status: str
    priority: str
    assignee_id: str | None
    parent_id: str | None
    cost_used: float
    handoff_depth: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CommentOut(BaseModel):
    id: str
    ticket_id: str
    author_agent_id: str | None
    is_system: bool
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AttachmentOut(BaseModel):
    id: str
    ticket_id: str
    filename: str
    content_type: str
    size_bytes: int
    path: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: str
    ticket_id: str
    agent_id: str
    status: str
    trigger: str
    tool_kind: str
    model: str
    cost: float
    started_at: datetime | None
    ended_at: datetime | None

    model_config = {"from_attributes": True}


class TicketDetail(TicketOut):
    comments: list[CommentOut]
    attachments: list[AttachmentOut]
    runs: list[RunOut]
    children: list[TicketOut]
