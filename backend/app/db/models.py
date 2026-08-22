"""SQLAlchemy 2.0 models — exact schema from docs/02-tsd.md §2."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    repo_path: Mapped[str] = mapped_column(String, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    guardrails: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ticket_counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Agent(Base):
    __tablename__ = "agent"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_agent_workspace_name"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("pm", "lead", "engineer", "designer", "qa", "pentester", name="agent_role"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String, nullable=False)
    tool_kind: Mapped[str] = mapped_column(
        Enum("opencode", "claude", "agy", "codex", name="agent_tool_kind"), nullable=False
    )
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("idle", "working", "error", "disabled", name="agent_status"),
        default="idle",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Ticket(Base):
    __tablename__ = "ticket"
    __table_args__ = (Index("ix_ticket_workspace_status", "workspace_id", "status"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "backlog",
            "todo",
            "in_progress",
            "review",
            "qa",
            "security",
            "done",
            "blocked",
            name="ticket_status",
        ),
        default="backlog",
        nullable=False,
    )
    priority: Mapped[str] = mapped_column(
        Enum("low", "medium", "high", "urgent", name="ticket_priority"),
        default="medium",
        nullable=False,
    )
    assignee_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent.id", ondelete="SET NULL"), nullable=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ticket.id", ondelete="SET NULL"), nullable=True
    )
    cost_used: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    handoff_depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class Attachment(Base):
    __tablename__ = "attachment"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Comment(Base):
    __tablename__ = "comment"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False
    )
    author_agent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent.id", ondelete="SET NULL"), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommentMention(Base):
    __tablename__ = "comment_mention"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    comment_id: Mapped[str] = mapped_column(
        String, ForeignKey("comment.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent.id", ondelete="CASCADE"), nullable=False
    )


class Run(Base):
    __tablename__ = "run"
    __table_args__ = (Index("ix_run_status", "status"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "queued", "running", "done", "failed", "cancelled", "interrupted", name="run_status"
        ),
        default="queued",
        nullable=False,
    )
    trigger: Mapped[str] = mapped_column(
        Enum("manual", "mention", "handoff", "auto", name="run_trigger"), nullable=False
    )
    parent_run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("run.id", ondelete="SET NULL"), nullable=True
    )
    tool_kind: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    __tablename__ = "event"
    __table_args__ = (
        Index("ix_event_workspace_id", "workspace_id", "id"),
        Index("ix_event_run_seq", "run_id", "seq"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(String, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(
        Enum(
            "run_started",
            "assistant_text",
            "reasoning",
            "tool_call",
            "tool_result",
            "status_change",
            "comment",
            "handoff",
            "error",
            "run_ended",
            name="event_type",
        ),
        nullable=False,
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
