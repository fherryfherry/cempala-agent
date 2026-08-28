"""SQLAlchemy 2.0 models — exact schema from docs/02-tsd.md §2."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime(timezone=True) is a no-op on SQLite: values round-trip as naive
    datetimes, which then serialize without a UTC offset and get misread as
    local time on the frontend. Every column of this type is always written via
    _now(), so a naive value read back is always semantically UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    """Identity/lifecycle only — guardrails, workflow_prompt, sprint_creator_roles,
    time_unit, timezone, and main_branch moved to `<repo_path>/.cempala/settings.yaml`
    (ADR-015); see `core/settings_store.py`."""

    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    repo_path: Mapped[str] = mapped_column(String, nullable=False)
    # Free-text project context (what this product/repo is), shown to every agent as part of
    # the prompt — see core/orchestrator.py::_build_prompt_for.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ticket_counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class Role(Base):
    """Global, workspace-agnostic role definitions — docs/superpowers/specs/
    2026-08-27-dynamic-roles-design.md.

    The 8 builtin roles are seeded by migration (is_builtin=True, undeletable);
    custom roles can be created/edited/deleted freely. `key` is the immutable
    slug agents reference (`agent.role`); the `"pm"` heuristics across the
    codebase are safe because the key is immutable and pm is undeletable.
    `system_prompt` is the default prompt agents fall back to when their own
    `system_prompt` is null.
    """

    __tablename__ = "role"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    may_declare_tickets: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    may_manage_artifacts: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_reviewer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


def _seed_builtin_roles(target, connection, **kwargs):
    """Seed the 8 builtin roles on every fresh schema (test fixtures via
    `Base.metadata.create_all` and any other create-all path). Production schema
    is managed by Alembic, which backfills the same data via its own migration —
    this event only fires for `create_all` engines, never for migrated ones."""
    from app.agents.prompts import DEFAULT_ROLE_PROMPTS
    from app.core.role_defs import BUILTIN_ROLES

    now = _now()
    for role in BUILTIN_ROLES:
        connection.execute(
            target.insert().values(
                id=f"builtin-{role['key']}",
                key=role["key"],
                name=role["name"],
                description=None,
                system_prompt=DEFAULT_ROLE_PROMPTS.get(role["key"]),
                is_builtin=True,
                may_declare_tickets=role["may_declare_tickets"],
                may_manage_artifacts=role["may_manage_artifacts"],
                is_reviewer=role["is_reviewer"],
                created_at=now,
            )
        )


event.listen(Role.__table__, "after_create", _seed_builtin_roles)


class Sprint(Base):
    __tablename__ = "sprint"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("planned", "active", "completed", name="sprint_status"),
        default="planned",
        nullable=False,
    )
    duration_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Calendar dates, set/edited manually by the owner/PM in the UI — never derived from
    # duration_estimate and never populated via a ```map block (see orchestrator.py).
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class Agent(Base):
    __tablename__ = "agent"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_agent_workspace_name"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Plain string, foreign-key-free: agents may reference any existing role key;
    # orphaned keys are prevented at the API/parser level (role lookup), and role
    # deletion is blocked while agents still use it.
    role: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_kind: Mapped[str] = mapped_column(
        Enum("opencode", "claude", "agy", "codex", "cmd", name="agent_tool_kind"), nullable=False
    )
    fallback_tool_kind: Mapped[str | None] = mapped_column(
        Enum("opencode", "claude", "agy", "codex", "cmd", name="agent_tool_kind"), nullable=True
    )
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_template: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_color: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("idle", "working", "error", "disabled", name="agent_status"),
        default="idle",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    # Optional context link to a ticket this chat is about (display-only, not a
    # storage coupling — chat lives in its own tables, ADR: chat != comments).
    linked_ticket_key: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=_now, onupdate=_now
    )
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # JSON-serialized {"sprints": [...], "tickets": [...]} drafts, set when the PM
    # proposes a new sprint while none is active (no active sprint = nothing gets
    # created until the owner replies with an APPROVAL_RE match in this chat).
    pending_proposal: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConversationMessage(Base):
    __tablename__ = "conversation_message"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False
    )
    # Set for agent/system messages produced by a chat run; NULL for owner messages.
    run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("run.id", ondelete="SET NULL"), nullable=True
    )
    # NULL = owner message.
    author_agent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent.id", ondelete="SET NULL"), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class ConversationAttachment(Base):
    __tablename__ = "conversation_attachment"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("conversation_message.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class AgentMemory(Base):
    __tablename__ = "agent_memory"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    # "agent" = written via the ```map `memory:` field (source_ticket_key set); "owner" =
    # added manually via the Agents -> Memory UI (source_ticket_key always NULL).
    origin: Mapped[str] = mapped_column(
        Enum("agent", "owner", name="agent_memory_origin"), nullable=False
    )
    source_ticket_key: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class Routine(Base):
    __tablename__ = "routine"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(
        Enum("idle_only", "consistent", name="routine_mode"), nullable=False
    )
    agent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent.id", ondelete="SET NULL"), nullable=True
    )
    # idle = menunggu interval; waiting = run terjadwal/antre; running = run sedang jalan;
    # disabled = dimatikan owner (menggantikan `enabled` bool).
    status: Mapped[str] = mapped_column(
        Enum("idle", "waiting", "running", "disabled", name="routine_status"),
        default="idle",
        nullable=False,
    )
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=_now, onupdate=_now
    )


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
    sprint_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("sprint.id", ondelete="SET NULL"), nullable=True
    )
    duration_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    category: Mapped[str | None] = mapped_column(
        Enum("feature", "improvement", "fix", "security", "performance", name="ticket_category"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when a human unblocks the ticket (blocked -> anything else). Loop detection
    # (app/core/loop_detector.py) ignores runs before this point, so an unblocked ticket
    # gets a genuinely fresh window instead of instantly re-tripping on old history.
    loop_reset_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cost_used: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    handoff_depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=_now, onupdate=_now
    )


class TicketAutoCheck(Base):
    """Auto-check backoff state (MAP-050 anti-spam), kept off the `Ticket` row on
    purpose: any write to `Ticket` — even an unrelated `cost_used` bump — fires
    `onupdate` on `Ticket.updated_at`, which would reset the same staleness clock
    this table is meant to slow down.
    """

    __tablename__ = "ticket_auto_check"

    ticket_id: Mapped[str] = mapped_column(
        String, ForeignKey("ticket.id", ondelete="CASCADE"), primary_key=True
    )
    skip_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_nudge_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class ArtifactGroup(Base):
    __tablename__ = "artifact_group"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


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
    # "upload" = human-attached context file (ticket detail page); "agent" = declared via the
    # ```map `artifacts:` field and copied from repo_path by the orchestrator.
    origin: Mapped[str] = mapped_column(
        Enum("upload", "agent", name="attachment_origin"), default="upload", nullable=False
    )
    group_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("artifact_group.id", ondelete="SET NULL"), nullable=True
    )
    # Agent-supplied note from the ```map `artifacts:` entry (origin="agent" only); always
    # NULL for origin="upload".
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


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
    # NULL for routine runs (trigger="routine") and chat runs (trigger="chat") —
    # those have no ticket.
    ticket_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=True
    )
    # Set only for chat runs (trigger="chat") — links the run to its Conversation.
    conversation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("conversation.id", ondelete="SET NULL"), nullable=True
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
        Enum(
            "manual", "mention", "handoff", "auto", "routine", "chat", name="run_trigger"
        ),
        nullable=False,
    )
    # Set only for routine runs — links the run to its Routine for status sync.
    routine_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("routine.id", ondelete="SET NULL"), nullable=True
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
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


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
            "conversation_message",
            "handoff",
            "error",
            "run_ended",
            name="event_type",
        ),
        nullable=False,
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
