from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Status = Literal[
    "backlog", "todo", "in_progress", "review", "qa", "security", "done", "blocked"
]
Priority = Literal["low", "medium", "high", "urgent"]
Category = Literal["feature", "improvement", "fix", "security", "performance"]


class TicketCreate(BaseModel):
    title: str
    description: str | None = None
    priority: Priority = "medium"
    assignee_id: str | None = None
    parent_id: str | None = None
    category: Category | None = None
    sprint_id: str | None = None
    duration_estimate: float | None = None
    # Every ticket must belong to an epic (parent_id) unless the caller explicitly
    # opts into creating a new one — no silent default to a parentless ticket.
    # See app/api/tickets.py::create_ticket for the enforcement.
    is_new_epic: bool = False


class TicketUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: Priority | None = None
    assignee_id: str | None = None
    status: Status | None = None
    category: Category | None = None
    sprint_id: str | None = None
    duration_estimate: float | None = None
    # Who is making this change, for status-transition permission checks (ADR-005: no
    # auth, so the caller states it). None = owner, who may perform any transition.
    actor_agent_id: str | None = None


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
    category: str | None = None
    sprint_id: str | None = None
    duration_estimate: float | None = None
    approved_at: datetime | None = None
    blocked_reason: str | None = None
    loop_reset_at: datetime | None = None
    cost_used: float
    handoff_depth: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CommentCreate(BaseModel):
    body: str
    author_agent_id: str | None = None


class CommentOut(BaseModel):
    id: str
    ticket_id: str
    author_agent_id: str | None
    is_system: bool
    body: str
    created_at: datetime
    mentions: list[str] = []

    model_config = {"from_attributes": True}


class AttachmentOut(BaseModel):
    id: str
    ticket_id: str
    filename: str
    content_type: str
    size_bytes: int
    path: str
    origin: str
    description: str | None = None
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
    parent: TicketOut | None = None
