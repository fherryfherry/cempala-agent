"""Run orchestrator — docs/02-tsd.md §4.5, MAP-023.

`schedule()` creates a `Run` row and either starts it immediately or queues it
(one running run per agent, FIFO for the rest). `execute()` drives one run
end to end: build the prompt, stream adapter events through the event bus,
then apply the parsed ```map report (or a failure path) to the ticket.

Guardrails (MAP-027, `core/guardrails.py`): schedule-time checks (concurrency, cost-per-ticket,
handoff-depth) run in `schedule()` before a `Run` row exists; runtime checks (timeout,
cost-per-run) are polled inside `execute()`'s streaming loop and trip `ctx.cancel_event`.
No automatic handoff scheduling yet (MAP-029) — this only runs what it's told.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import mimetypes
import re
import uuid
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import AdapterEvent, RunContext, TOOLS
from app.agents.prompts import (
    AgentInfo,
    ChatMessageInfo,
    CommentInfo,
    TicketInfo,
    WorkspaceTicketSummary,
    build_chat_prompt,
    build_prompt,
    build_routine_prompt,
)
from app.api.attachments import _attachments_dir, _sanitize_filename, _storage_dir
from app.core.events import event_bus
from app.core.guardrails import (
    GuardrailBlocked,
    check_guardrails,
    check_guardrails_routine,
    guardrail_limit,
    over_cost_per_run,
    over_run_timeout,
)
from app.core.loop_detector import detect_loop
from app.core import git as git_module
from app.core.report import (
    ArtifactDraft,
    ArtifactUpdateDraft,
    SprintDraft,
    TicketDraft,
    parse_report,
)
from app.core.state_machine import STATUSES, can_transition

from app.db.models import (
    Agent,
    AgentMemory,
    ArtifactGroup,
    Attachment,
    Comment,
    CommentMention,
    Conversation,
    ConversationAttachment,
    ConversationMessage,
    Event,
    GlobalSetting,
    Role,
    Routine,
    Run,
    Sprint,
    Ticket,
    TicketAutoCheck,
    Workspace,
)

# Statuses that don't expect a follow-up handoff (docs/03-agent-design.md §5/§6): the
# flow always routes review/qa/security to a specific next reviewer, so only done/blocked
# count as "final" for the purposes of "no valid mention -> block so it doesn't hang".
_FINAL_STATUSES = frozenset({"done", "blocked"})

# MAP-050 anti-spam (auto_check.py): an auto-nudged agent with nothing new to report
# still has to fill the ```map contract's mandatory `summary`, which converges on
# near-identical boilerplate every nudge. If a trigger="auto" summary is this similar
# to the ticket's last comment, treat it as a no-op: skip posting it and record the
# skip in TicketAutoCheck so auto_check.py's backoff can slow the next nudge down.
_AUTO_CHECK_DUP_RATIO = 0.8

# Statuses where a report's mentions are informational only — there is no more work to
# hand off. Deliberately excludes "blocked": an agent reporting blocked *with* a mention
# is asking that mention to help unblock, which is real forward momentum. Without this,
# two agents can keep re-confirming an already-"done" ticket to each other forever (each
# mention scheduling a fresh run with an identical closing report) until the loop
# detector eventually catches it — a real incident, not a hypothetical.
_COMPLETION_STATUSES = frozenset({"done"})

_TAIL_CHARS = 2000

# Cap on how many of an agent's own memory notes get injected into its next prompt
# (docs/05-roadmap.md's hallucination-risk caveat on cross-ticket memory: keep it bounded
# and verbatim, not open-ended retrieval) — same idea as _PM_CHAT_TICKET_LIST_LIMIT below.
_AGENT_MEMORY_PROMPT_LIMIT = 20

# Cap on how many artifacts get listed in the prompt's artifact catalog (most recent
# first) — cheap insurance against unbounded prompt growth on large workspaces.
_ARTIFACT_CATALOG_LIMIT = 100

# Cap on how many existing epics (top-level tickets) get listed in the ```map contract's
# reuse catalog — most-recently-updated first, same insurance as _ARTIFACT_CATALOG_LIMIT.
_EPIC_CATALOG_LIMIT = 100

# run.id -> asyncio.Task, for currently-executing runs (used by the stop endpoint).
RUNNING: dict[str, asyncio.Task] = {}

# agent_id -> FIFO queue of run ids waiting for that agent to free up.
_PENDING: dict[str, deque[str]] = defaultdict(deque)
# agent_id -> whether it currently has a run actually executing.
_BUSY: set[str] = set()
# Guards read-modify-write of _PENDING/_BUSY across concurrent schedule() calls.
_LOCK = asyncio.Lock()

# run.id -> the cancel_event handed to the adapter for that run. Separate from the ORM
# `Run` row (which has no such column) since `stop()` runs in its own DB session/object
# instance and can't reach an attribute stashed on a different session's object.
_CANCEL_EVENTS: dict[str, asyncio.Event] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_sprint_date(value: str | None) -> date | None:
    """Parse a `sprints:` start_date/end_date (YYYY-MM-DD) leniently.

    Malformed values return None (sprint stays without that date) instead of
    failing the whole run — the date is timeline metadata, not the work itself.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


async def _get_or_create_sprint(
    session: AsyncSession,
    workspace_id: str,
    name: str,
    *,
    goal: str | None = None,
    duration: float | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Sprint:
    """Get-or-create a Sprint by (workspace, name), case-insensitive.

    Sprints are never created directly by the API for agent flows — PM's ```map
    `sprints:`/`tickets[].sprint` fields are the only way one comes into being
    (docs/03-agent-design.md §4). The first sprint ever created for a workspace is
    bootstrapped `active` so Board/Timeline have something to default to; after
    that, switching the active sprint is a manual owner/PM action.

    `start_date`/`end_date` (YYYY-MM-DD strings) come from the ```map `sprints:`
    block — the PM declares the sprint's calendar range when creating/updating it
    (owner request: PM must set the sprint date range, MAP-049). Invalid dates are
    ignored (left as NULL) rather than failing the run.
    """
    start = _parse_sprint_date(start_date)
    end = _parse_sprint_date(end_date)

    existing = (
        await session.scalars(select(Sprint).where(Sprint.workspace_id == workspace_id))
    ).all()
    for sprint in existing:
        if sprint.name.strip().lower() == name.strip().lower():
            if goal:
                sprint.goal = goal
            if duration is not None:
                sprint.duration_estimate = duration
            if start is not None:
                sprint.start_date = start
            if end is not None:
                sprint.end_date = end
            return sprint

    next_index = max((s.index for s in existing), default=-1) + 1
    has_active = any(s.status == "active" for s in existing)
    sprint = Sprint(
        workspace_id=workspace_id,
        name=name,
        goal=goal,
        index=next_index,
        status="planned" if has_active else "active",
        duration_estimate=duration,
        start_date=start,
        end_date=end,
    )
    session.add(sprint)
    await session.flush()
    return sprint


async def _apply_sprint_status(session: AsyncSession, sprint: Sprint, status: str | None) -> None:
    """Execute an optional `sprints:` `status:` request (report.py's
    VALID_SPRINT_STATUSES: active/completed) — previously the only way to move a
    sprint's status was the owner's manual PATCH in the UI, so a PM declaring
    `status: active`/`status: completed` in its ```map block had no effect at all.

    Local import: `app.api.sprints` imports this module (`orchestrator.schedule`,
    used by `_kick_off_sprint_tickets`), so a module-level import here would be
    circular — same workaround already used for `app.api.tickets._next_key`.
    """
    if status is None or status == sprint.status:
        return
    from app.api.sprints import activate_sprint, complete_sprint

    if status == "active":
        await activate_sprint(session, sprint)
    elif status == "completed":
        await complete_sprint(session, sprint)


async def _resolve_epic_target(
    session: AsyncSession, workspace_id: str, epic_key: str | None
) -> tuple[Ticket | None, str | None]:
    """Resolve an optional ```map `epic:` key to an existing top-level ticket.

    Returns `(epic_ticket, skip_note)` — exactly one of the pair is non-None-ish:
    `epic_ticket` set on success, or `skip_note` set (and `epic_ticket` None) when
    `epic_key` was given but doesn't resolve to a valid epic (unknown key, or a ticket
    that itself has a parent — the flat 1-level invariant means only a true top-level
    ticket may be reused as an epic). Both None means `epic_key` was empty — caller
    falls back to its own default (docs/03-agent-design.md §3).
    """
    if not epic_key:
        return None, None
    epic = await session.scalar(
        select(Ticket).where(Ticket.workspace_id == workspace_id, Ticket.key == epic_key)
    )
    if epic is None:
        return None, f"epic '{epic_key}' tidak ditemukan di workspace ini; pakai default"
    if epic.parent_id is not None:
        return None, f"'{epic_key}' bukan epic top-level (punya parent sendiri); pakai default"
    return epic, None


async def _sprint_name_exists(session: AsyncSession, workspace_id: str, name: str) -> bool:
    existing = await session.scalars(select(Sprint).where(Sprint.workspace_id == workspace_id))
    return any(s.name.strip().lower() == name.strip().lower() for s in existing)


async def create_tickets_and_sprints(
    session: AsyncSession,
    workspace: Workspace,
    *,
    sprints: list[SprintDraft],
    tickets: list[TicketDraft],
    run_id: str | None,
    actor_name: str,
) -> tuple[list[dict], list[str], list[Sprint], list[Ticket]]:
    """Create sprints/tickets from parsed ```map `sprints:`/`tickets[]` drafts,
    chat-flavored: no ticket-context epic fallback (unlike the ticket-run path).
    Does not itself schedule any run — this function only `flush()`s, it never
    commits (it runs mid-transaction inside its caller's larger report-processing
    transaction), and `schedule()` always commits internally. Callers schedule
    newly-assigned tickets themselves (`orchestrator._auto_schedule_assignee`)
    right after their own commit. Shared by `_finish_routine_run`/`_finish_chat_run`
    (immediate creation) and `conversations.create_message` (deferred creation,
    once the owner approves a sprint proposal — no `run` in scope there, hence the
    optional `run_id`).

    Returns `(tickets_report, epic_skip_notes, new_sprints, created_tickets)` —
    `new_sprints` is only `Sprint`s that didn't already exist by name (NOT every
    sprint touched, e.g. a `tickets[].sprint` reference to an already-existing
    sprint is left out) so a caller deciding whether to *activate* a sprint never
    reactivates an unrelated pre-existing one — including a completed one — just
    because a ticket happened to name it. `created_tickets` is every `Ticket` row
    built here (already flushed, `id`/`assignee_id`/`sprint_id` populated).
    """
    from app.api.tickets import _next_key  # reuse the same atomic-counter key logic

    workspace_id = workspace.id
    new_sprints: dict[str, Sprint] = {}

    for sprint_draft in sprints:
        is_new = not await _sprint_name_exists(session, workspace_id, sprint_draft.name)
        sprint = await _get_or_create_sprint(
            session,
            workspace_id,
            sprint_draft.name,
            goal=sprint_draft.goal,
            duration=sprint_draft.duration,
            start_date=sprint_draft.start_date,
            end_date=sprint_draft.end_date,
        )
        await _apply_sprint_status(session, sprint, sprint_draft.status)
        if is_new:
            new_sprints[sprint.id] = sprint

    tickets_report: list[dict] = []
    epic_skip_notes: list[str] = []
    created_tickets: list[Ticket] = []
    for draft in tickets:
        assignee_id = None
        if draft.assignee:
            assignee_agent = await session.scalar(
                select(Agent).where(
                    Agent.workspace_id == workspace_id, Agent.name == draft.assignee
                )
            )
            assignee_id = assignee_agent.id if assignee_agent else None
        sprint_id = None
        if draft.sprint:
            is_new = not await _sprint_name_exists(session, workspace_id, draft.sprint)
            sprint = await _get_or_create_sprint(session, workspace_id, draft.sprint)
            sprint_id = sprint.id
            if is_new:
                new_sprints[sprint.id] = sprint

        epic_target, epic_skip_note = await _resolve_epic_target(
            session, workspace_id, draft.epic
        )
        if epic_skip_note:
            epic_skip_notes.append(epic_skip_note)

        child = Ticket(
            workspace_id=workspace_id,
            key=await _next_key(session, workspace),
            title=draft.title,
            description=draft.description,
            status="todo",
            priority=draft.priority,
            assignee_id=assignee_id,
            parent_id=epic_target.id if epic_target is not None else None,
            category=draft.category,
            sprint_id=sprint_id,
            duration_estimate=draft.duration,
        )
        session.add(child)
        await session.flush()
        if run_id is not None:
            await event_bus.publish(
                session,
                run_id=run_id,
                workspace_id=workspace_id,
                type="status_change",
                payload={
                    "ticket_id": child.id,
                    "ticket_key": child.key,
                    "ticket_title": child.title,
                    "from": None,
                    "to": "todo",
                    "actor": actor_name,
                },
            )
        tickets_report.append({"title": draft.title, "key": child.key})
        created_tickets.append(child)

    return tickets_report, epic_skip_notes, list(new_sprints.values()), created_tickets


async def _get_or_create_artifact_group(
    session: AsyncSession, workspace_id: str, name: str
) -> ArtifactGroup:
    """Get-or-create an ArtifactGroup by (workspace, name), case-insensitive — same pattern as

    `_get_or_create_sprint`. Groups are entirely agent-driven: an agent's ```map `artifacts:`
    entry names a group freely, reusing an existing one or inventing a new one.
    """
    existing = (
        await session.scalars(select(ArtifactGroup).where(ArtifactGroup.workspace_id == workspace_id))
    ).all()
    for group in existing:
        if group.name.strip().lower() == name.strip().lower():
            return group

    group = ArtifactGroup(workspace_id=workspace_id, name=name)
    session.add(group)
    await session.flush()
    return group


async def _publish_artifacts(
    session: AsyncSession,
    workspace: Workspace,
    ticket: Ticket,
    artifacts: list[ArtifactDraft],
) -> tuple[list[dict], list[str]]:
    """Copy each ```map `artifacts:` entry's declared file into attachment storage.

    Path safety is enforced here (the only place that actually touches the filesystem for this
    feature): `draft.path` is resolved against `workspace.repo_path` and must stay inside it,
    since the string comes straight from model output. Files that escape repo_path, don't exist,
    or aren't regular files are skipped and noted rather than failing the whole report — same
    tolerance as `updates:`/`tickets:` malformed entries.
    """
    published: list[dict] = []
    skip_notes: list[str] = []
    repo_root = Path(workspace.repo_path).resolve()

    for draft in artifacts:
        candidate = (repo_root / draft.path).resolve()
        if not candidate.is_relative_to(repo_root):
            skip_notes.append(f"{draft.path}: di luar repo, diabaikan")
            continue
        if not candidate.is_file():
            skip_notes.append(f"{draft.path}: file tidak ditemukan")
            continue
        # ponytail: is_file() then read_bytes() below is TOCTOU-able in theory, but the agent
        # process already has arbitrary code execution inside repo_path for this run's whole
        # duration (ADR-010) — a swap-to-symlink race here grants nothing it doesn't already
        # have. Not worth a lock/reopen-by-fd for that non-threat model.

        group = await _get_or_create_artifact_group(session, workspace.id, draft.group)

        dest_dir = _attachments_dir() / ticket.id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"{uuid.uuid4().hex}-{_sanitize_filename(candidate.name)}"
        data = candidate.read_bytes()
        dest_path.write_bytes(data)

        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        attachment = Attachment(
            ticket_id=ticket.id,
            filename=candidate.name,
            content_type=content_type,
            size_bytes=len(data),
            path=str(dest_path.relative_to(_storage_dir())),
            origin="agent",
            group_id=group.id,
            description=draft.description or None,
        )
        session.add(attachment)
        published.append({"path": draft.path, "group": group.name})

    return published, skip_notes


async def _persist_memories(
    session: AsyncSession, agent: Agent, ticket: Ticket | None, notes: list[str]
) -> list[str]:
    saved: list[str] = []
    for note in notes:
        session.add(
            AgentMemory(
                agent_id=agent.id,
                note=note,
                origin="agent",
                source_ticket_key=ticket.key if ticket else None,
            )
        )
        saved.append(note)
    return saved


async def _apply_artifact_updates(
    session: AsyncSession,
    workspace_id: str,
    updates: list[ArtifactUpdateDraft],
) -> tuple[list[dict], list[str]]:
    """Execute PM's ```map `artifact_updates:` — organize the Artifacts menu.

    Runs AFTER `_publish_artifacts` in `_finish_run`, so artifacts published by the
    same report are already in their groups when these operations apply. Semantics:
    - rename: group -> to. If `to` already exists (case-insensitive), it degrades to a
      merge (all attachments move into the existing group, source deleted).
    - merge: from -> into. `into` is get-or-created; `from` is deleted after moving.
    - move: one attachment (by filename) from `group` to `to` (get-or-created).
    - delete: only allowed when the group has no attachments left; otherwise rejected.
    Unknown groups/files are noted and skipped — same tolerance as `updates:`/`tickets:`.
    Returns (report entries, skip notes) for the system comment and run.report.
    """
    report: list[dict] = []
    skip_notes: list[str] = []

    async def _find_group(name: str) -> ArtifactGroup | None:
        groups = (
            await session.scalars(
                select(ArtifactGroup).where(ArtifactGroup.workspace_id == workspace_id)
            )
        ).all()
        for g in groups:
            if g.name.strip().lower() == name.strip().lower():
                return g
        return None

    async def _get_or_create(name: str) -> ArtifactGroup:
        existing = await _find_group(name)
        if existing is not None:
            return existing
        group = ArtifactGroup(workspace_id=workspace_id, name=name)
        session.add(group)
        await session.flush()
        return group

    for draft in updates:
        op = draft.op
        if op == "rename":
            group = await _find_group(draft.group)
            if group is None:
                skip_notes.append(f"rename: kelompok '{draft.group}' tidak ditemukan")
                continue
            target = await _find_group(draft.to)
            if target is not None and target.id != group.id:
                # rename onto an existing group -> merge
                await session.execute(
                    update(Attachment)
                    .where(Attachment.group_id == group.id)
                    .values(group_id=target.id)
                )
                await session.delete(group)
                report.append({"op": "merge", "from": group.name, "into": target.name})
            else:
                group.name = draft.to
                report.append({"op": "rename", "group": draft.group, "to": draft.to})
        elif op == "merge":
            source = await _find_group(draft.from_group)
            if source is None:
                skip_notes.append(f"merge: kelompok '{draft.from_group}' tidak ditemukan")
                continue
            target = await _get_or_create(draft.into)
            await session.execute(
                update(Attachment)
                .where(Attachment.group_id == source.id)
                .values(group_id=target.id)
            )
            await session.delete(source)
            report.append({"op": "merge", "from": draft.from_group, "into": target.name})
        elif op == "move":
            source = await _find_group(draft.group)
            if source is None:
                skip_notes.append(f"move: kelompok '{draft.group}' tidak ditemukan")
                continue
            attachment = await session.scalar(
                select(Attachment).where(
                    Attachment.group_id == source.id, Attachment.filename == draft.file
                )
            )
            if attachment is None:
                skip_notes.append(
                    f"move: file '{draft.file}' tidak ditemukan di kelompok '{draft.group}'"
                )
                continue
            target = await _get_or_create(draft.to)
            attachment.group_id = target.id
            report.append({"op": "move", "file": draft.file, "from": draft.group, "to": target.name})
        elif op == "delete":
            group = await _find_group(draft.group)
            if group is None:
                skip_notes.append(f"delete: kelompok '{draft.group}' tidak ditemukan")
                continue
            remaining = (
                await session.scalars(select(Attachment).where(Attachment.group_id == group.id))
            ).all()
            if remaining:
                skip_notes.append(
                    f"delete: kelompok '{draft.group}' masih berisi {len(remaining)} file, ditolak"
                )
                continue
            await session.delete(group)
            report.append({"op": "delete", "group": draft.group})
        else:
            skip_notes.append(f"op '{op}' tidak dikenal")

    return report, skip_notes


_ORCHESTRATOR_MODEL_CACHE_TTL = 5.0  # seconds
_orch_model_cache: tuple[float, str | None] | None = None


async def _global_orchestrator_model(session: AsyncSession) -> str | None:
    """Read the portal-wide default model. Short in-process cache; never raises.

    Returns None when unset/errored — the caller surfaces the missing-model
    condition (system comment/message), never an exception here.
    """
    global _orch_model_cache
    now = _now().timestamp()
    if _orch_model_cache is not None and now - _orch_model_cache[0] < _ORCHESTRATOR_MODEL_CACHE_TTL:
        return _orch_model_cache[1]
    model: str | None = None
    try:
        row = await session.get(GlobalSetting, "orchestrator_model")
        val = row.value if row is not None else None
        model = val if isinstance(val, str) and val.strip() else None
    except Exception:
        model = None
    _orch_model_cache = (now, model)
    return model


def resolve_agent_model(agent_model: str | None, global_model: str | None) -> str | None:
    """Pick the model for a run: the agent's own wins; else the global default.

    Pure function (no DB), so it's trivially unit-testable. Callers load the
    global model via `_global_orchestrator_model` and pass it in.
    """
    if agent_model:
        return agent_model
    return global_model


async def _role_map(session: AsyncSession) -> dict[str, Role]:
    """Load every role row keyed by its immutable key — the single source for
    role labels/flags (dynamic roles spec). Callers fetch once per run and pass
    the results into the pure prompt/report builders."""
    roles = (await session.scalars(select(Role))).all()
    return {r.key: r for r in roles}


def _agent_info_from(agent: Agent, role: Role | None) -> AgentInfo:
    """Enrich an ORM Agent into a prompt `AgentInfo`: role label, resolved default
    system prompt (agent's own wins; else the role's — dynamic roles fallback),
    and the permission flags that gate the prompt's contract blocks."""
    return AgentInfo(
        name=agent.name,
        role=agent.role,
        system_prompt=agent.system_prompt if agent.system_prompt else (role.system_prompt if role else None),
        label=role.name if role else None,
        is_reviewer=role.is_reviewer if role else False,
        may_declare_tickets=role.may_declare_tickets if role else False,
        may_manage_artifacts=role.may_manage_artifacts if role else False,
    )


async def schedule(
    session: AsyncSession,
    session_factory: async_sessionmaker,
    *,
    ticket: Ticket,
    agent: Agent,
    trigger: str,
    parent_run_id: str | None = None,
    exclude_run_id: str | None = None,
) -> Run:
    """Create a queued Run row, then either start it now or leave it queued.

    `session` is used for the create; `session_factory` is handed to the
    background task so it can open its own session (the request's session
    closes when the endpoint returns, long before the run finishes).

    `exclude_run_id`: passed straight through to `check_guardrails` — set by callers
    scheduling a follow-up (handoff / tickets[] / epic-close) from *inside* another
    run's own `_finish_run`, so that still-"running" run doesn't count against
    `max_concurrent_runs` for the follow-up it's itself producing. Also used as the
    event-bus `run_id` for a guardrail-block's comment/status_change, since it's a
    real persisted run already in scope at that call site.
    """
    workspace = await session.get(Workspace, ticket.workspace_id)
    if workspace is not None and workspace.paused:
        raise RuntimeError("workspace paused")

    guardrails = (workspace.guardrails if workspace else None) or {}
    try:
        await check_guardrails(
            session,
            ticket,
            guardrails,
            agent_role=agent.role,
            sprint_creator_roles=workspace.sprint_creator_roles if workspace else [],
            exclude_run_id=exclude_run_id,
            trigger=trigger,
        )
        cycle = await detect_loop(session, ticket, guardrails, agent.id, trigger=trigger)
        if cycle is not None:
            raise GuardrailBlocked("loop_threshold", cycle)
    except GuardrailBlocked as exc:
        if exc.guardrail == "ticket_not_in_active_sprint":
            # A ticket outside the active sprint is not a failure — it's just not
            # due yet. Refuse the run WITHOUT touching the ticket's status (no
            # blocked transition, no status_change event): the agent must not be
            # able to move any status on a ticket whose sprint isn't active.
            # A system comment still names the guardrail so the reason is
            # traceable (CLAUDE.md: no silent failure path).
            await _write_system_comment(
                session,
                ticket.id,
                str(exc),
                ticket_key=ticket.key,
                run_id=exclude_run_id,
                workspace_id=ticket.workspace_id if exclude_run_id else None,
            )
        else:
            await _block_ticket(
                session,
                ticket,
                agent,
                str(exc),
                run_id=exclude_run_id,
                workspace_id=ticket.workspace_id if exclude_run_id else None,
            )
        await session.commit()
        raise

    global_model = await _global_orchestrator_model(session)
    run_model = resolve_agent_model(agent.model, global_model)
    if run_model is None:
        await _write_system_comment(
            session,
            ticket.id,
            "Run dibatalkan: agent ini tidak punya model dan tidak ada default global. "
            "Set model pada agent atau isi 'AI Orchestrator (default model)' di Settings.",
            ticket_key=ticket.key,
            workspace_id=ticket.workspace_id,
        )
        await session.commit()
        raise RuntimeError("no model for agent run")

    run = Run(
        ticket_id=ticket.id,
        agent_id=agent.id,
        status="queued",
        trigger=trigger,
        parent_run_id=parent_run_id,
        tool_kind=agent.tool_kind,
        model=run_model,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    async with _LOCK:
        if agent.id in _BUSY:
            _PENDING[agent.id].append(run.id)
        else:
            _BUSY.add(agent.id)
            RUNNING[run.id] = asyncio.create_task(_execute_and_advance(session_factory, run.id))

    return run


async def _auto_schedule_assignee(
    session: AsyncSession, session_factory: async_sessionmaker, ticket: Ticket
) -> None:
    """Mirrors the owner clicking "Run": fires whenever a ticket lands on an
    assignee, except backlog tickets (not queued for work yet) — owner request.
    Guardrail trips/paused workspace are swallowed, same as `_kick_off_sprint_tickets`:
    the assignment/creation itself must still succeed either way.
    """
    if ticket.status == "backlog" or ticket.assignee_id is None:
        return
    agent = await session.get(Agent, ticket.assignee_id)
    if agent is None or not agent.enabled or agent.status == "disabled":
        return
    # Dedup guard: `_execute_pending_proposal` (conversations.py) can already have
    # kicked this exact ticket off via `activate_sprint` -> `_kick_off_sprint_tickets`
    # moments earlier in the same request when a brand-new sprint goes active in the
    # same PM batch — without this check that path double-schedules.
    already = await session.scalar(
        select(Run.id)
        .where(
            Run.ticket_id == ticket.id,
            Run.agent_id == agent.id,
            Run.status.in_(("queued", "running")),
        )
        .limit(1)
    )
    if already is not None:
        return
    try:
        await schedule(session, session_factory, ticket=ticket, agent=agent, trigger="manual")
    except (GuardrailBlocked, RuntimeError):
        pass


async def schedule_routine_run(
    session: AsyncSession,
    session_factory: async_sessionmaker,
    routine: Routine,
    agent: Agent,
) -> Run:
    """Schedule a routine run (no ticket) — same queue mechanics as `schedule()`.

    Guardrails: only `max_concurrent_runs` applies (routine runs have no ticket, so
    cost-per-ticket/handoff-depth don't). A guardrail trip leaves the routine at
    `idle` with `last_run_at` set so the scheduler doesn't retry it every tick.
    """
    workspace = await session.get(Workspace, routine.workspace_id)
    if workspace is not None and workspace.paused:
        raise RuntimeError("workspace paused")

    guardrails = (workspace.guardrails if workspace else None) or {}
    try:
        await check_guardrails_routine(session, workspace.id, guardrails)
    except GuardrailBlocked as exc:
        routine.status = "idle"
        routine.last_run_at = _now()
        await session.commit()
        raise

    global_model = await _global_orchestrator_model(session)
    run_model = resolve_agent_model(agent.model, global_model)
    if run_model is None:
        routine.status = "idle"
        routine.last_run_at = _now()
        await session.commit()
        raise RuntimeError("no model for routine run")

    run = Run(
        ticket_id=None,
        agent_id=agent.id,
        status="queued",
        trigger="routine",
        routine_id=routine.id,
        tool_kind=agent.tool_kind,
        model=run_model,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    routine.status = "waiting"
    await session.commit()

    async with _LOCK:
        if agent.id in _BUSY:
            _PENDING[agent.id].append(run.id)
        else:
            _BUSY.add(agent.id)
            RUNNING[run.id] = asyncio.create_task(_execute_and_advance(session_factory, run.id))

    return run


async def _has_active_chat_run(session: AsyncSession, conversation_id: str) -> bool:
    """True if the conversation already has a chat run queued/running.

    Owner messages arrive one at a time; queueing a second run while the PM is
    still working on the previous message would let the conversation's runs race
    each other (the second run's prompt would miss the first's reply). The caller
    reports "PM masih mengetik" instead of scheduling.
    """
    active = await session.scalar(
        select(Run.id)
        .where(
            Run.conversation_id == conversation_id,
            Run.status.in_(("queued", "running")),
        )
        .limit(1)
    )
    return active is not None


async def schedule_chat(
    session: AsyncSession,
    session_factory: async_sessionmaker,
    conversation: Conversation,
    agent: Agent,
    parent_run_id: str | None = None,
) -> Run:
    """Schedule a chat run (no ticket) — same queue mechanics as `schedule()`.

    Guardrails: only `max_concurrent_runs` applies (chat runs have no ticket, so
    cost-per-ticket/handoff-depth/sprint gates don't). A guardrail trip leaves a
    system message on the conversation naming the guardrail, so the owner sees
    why the PM didn't answer.

    `parent_run_id`: set by auto-retries (chained to the failed run) so the retry
    budget (`_retry_attempt_count`) is bounded by `max_auto_retries` — same
    semantics as ticket-run retries.
    """
    workspace = await session.get(Workspace, conversation.workspace_id)
    if workspace is not None and workspace.paused:
        raise RuntimeError("workspace paused")

    guardrails = (workspace.guardrails if workspace else None) or {}
    try:
        await check_guardrails_routine(session, conversation.workspace_id, guardrails)
    except GuardrailBlocked as exc:
        await _write_system_message(
            session,
            conversation,
            f"Pesan tidak diteruskan ke PM: {exc}",
            run_id=None,
            workspace_id=conversation.workspace_id,
        )
        await session.commit()
        raise

    global_model = await _global_orchestrator_model(session)
    run_model = resolve_agent_model(agent.model, global_model)
    if run_model is None:
        await _write_system_message(
            session,
            conversation,
            "PM tidak bisa membalas: tidak ada model yang ditetapkan untuk PM dan "
            "tidak ada 'AI Orchestrator (default model)' di Settings. Set model AI di Settings.",
            run_id=None,
            workspace_id=conversation.workspace_id,
        )
        await session.commit()
        raise RuntimeError("no model for chat run")

    run = Run(
        ticket_id=None,
        conversation_id=conversation.id,
        agent_id=agent.id,
        status="queued",
        trigger="chat",
        parent_run_id=parent_run_id,
        tool_kind=agent.tool_kind,
        model=run_model,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    async with _LOCK:
        if agent.id in _BUSY:
            _PENDING[agent.id].append(run.id)
        else:
            _BUSY.add(agent.id)
            RUNNING[run.id] = asyncio.create_task(_execute_and_advance(session_factory, run.id))

    return run


async def _execute_and_advance(session_factory: async_sessionmaker, run_id: str) -> None:
    """Run one run, then dequeue the agent's next pending run (if any)."""
    try:
        await execute(session_factory, run_id)
    finally:
        RUNNING.pop(run_id, None)
        async with session_factory() as session:
            run = await session.get(Run, run_id)
            agent_id = run.agent_id if run else None

        next_run_id = None
        async with _LOCK:
            if agent_id is not None:
                queue = _PENDING.get(agent_id)
                if queue:
                    next_run_id = queue.popleft()
                else:
                    _BUSY.discard(agent_id)

        if next_run_id is not None:
            RUNNING[next_run_id] = asyncio.create_task(
                _execute_and_advance(session_factory, next_run_id)
            )


def _accumulate_text(buffer: list[str], ev: AdapterEvent) -> None:
    if ev.type != "assistant_text":
        return
    text = ev.payload.get("text")
    if isinstance(text, str):
        buffer.append(text)


def _comment_preview(body: str, limit: int = 100) -> str:
    return body[:limit]


# @nama-agent — slug of letters/digits/hyphens, same contract as owner comments
# (app/api/comments.py::MENTION_RE): the agent must write "@name" in a comment body
# for a mention to be real. A bare name without `@` is just prose and stays that way.
_MENTION_RE = re.compile(r"@([a-zA-Z0-9][a-zA-Z0-9-]*)")


async def _record_body_mentions(
    session: AsyncSession,
    comment: Comment,
    body: str,
    *,
    workspace_id: str,
    author_agent_id: str | None,
) -> list[Agent]:
    """Record `comment_mention` rows for `@name` occurrences in an agent-authored
    comment body (ticket report summaries and `comments[]` targets), and return
    the mentioned agents (self-mentions excluded) so callers that have an
    actionable channel for them can schedule a run.

    `comments[]` callers (`_finish_routine_run`/`_finish_chat_run`) use the
    returned list to schedule — a no-ticket-mode report can't declare `mention:`
    at all, so this is the only channel those comments have. The ticket-run
    summary caller (`_finish_run`) deliberately ignores the return value: a
    ticket run's actionable mentions come from the ```map `mention:` field
    (`_handoff`) only, so scheduling from `summary:` prose too would risk
    double-scheduling the same agent from one report.
    """
    names = set(_MENTION_RE.findall(body))
    if not names:
        return []
    mentioned = (
        await session.scalars(
            select(Agent).where(Agent.workspace_id == workspace_id, Agent.name.in_(names))
        )
    ).all()
    result: list[Agent] = []
    for mentioned_agent in mentioned:
        if mentioned_agent.id == author_agent_id:
            continue  # self-mention dropped, same as owner comments
        session.add(CommentMention(comment_id=comment.id, agent_id=mentioned_agent.id))
        result.append(mentioned_agent)
    return result


async def _write_system_comment(
    session: AsyncSession,
    ticket_id: str,
    body: str,
    *,
    ticket_key: str | None = None,
    run_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    session.add(Comment(ticket_id=ticket_id, author_agent_id=None, is_system=True, body=body))
    # ponytail: publishing needs a persisted run/workspace to satisfy the Event FK —
    # callers with no run in scope (e.g. recover_interrupted_runs) just skip the toast,
    # nobody's watching a live feed for a restart-time recovery comment anyway.
    if run_id is not None and workspace_id is not None and ticket_key is not None:
        await event_bus.publish(
            session,
            run_id=run_id,
            workspace_id=workspace_id,
            type="comment",
            payload={
                "ticket_id": ticket_id,
                "ticket_key": ticket_key,
                "is_system": True,
                "author": "system",
                "body_preview": _comment_preview(body),
            },
        )


async def _write_system_message(
    session: AsyncSession,
    conversation: Conversation,
    body: str,
    *,
    run_id: str | None,
    workspace_id: str | None,
) -> ConversationMessage:
    """Append a System message to a conversation (chat analog of _write_system_comment).

    Used for guardrail trips, run failures, and restart recovery — anything the
    owner must see in the chat but that no agent authored.
    """
    message = ConversationMessage(
        conversation_id=conversation.id,
        run_id=run_id,
        author_agent_id=None,
        is_system=True,
        body=body,
    )
    session.add(message)
    await session.flush()
    conversation.last_message_at = _now()
    if run_id is not None and workspace_id is not None:
        await event_bus.publish(
            session,
            run_id=run_id,
            workspace_id=workspace_id,
            type="conversation_message",
            payload={
                "conversation_id": conversation.id,
                "is_system": True,
                "author": "system",
                "body_preview": _comment_preview(body),
            },
        )
    return message


async def _write_agent_message(
    session: AsyncSession,
    conversation: Conversation,
    agent: Agent,
    body: str,
    *,
    run_id: str | None,
    workspace_id: str | None,
) -> ConversationMessage:
    """Append an agent-authored message to a conversation (the PM speaking, not System)."""
    message = ConversationMessage(
        conversation_id=conversation.id,
        run_id=run_id,
        author_agent_id=agent.id,
        is_system=False,
        body=body,
    )
    session.add(message)
    await session.flush()
    conversation.last_message_at = _now()
    if run_id is not None and workspace_id is not None:
        await event_bus.publish(
            session,
            run_id=run_id,
            workspace_id=workspace_id,
            type="conversation_message",
            payload={
                "conversation_id": conversation.id,
                "is_system": False,
                "author": agent.name,
                "body_preview": _comment_preview(body),
            },
        )
    return message


async def _block_ticket(
    session: AsyncSession,
    ticket: Ticket,
    agent: Agent,
    reason_body: str,
    *,
    run_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    """System-driven transition to `blocked` — always legal (any -> blocked, any role)."""
    if ticket.status != "blocked":
        old_status = ticket.status
        ticket.status = "blocked"
        ticket.blocked_reason = reason_body
        if run_id is not None and workspace_id is not None:
            await event_bus.publish(
                session,
                run_id=run_id,
                workspace_id=workspace_id,
                type="status_change",
                payload={
                    "ticket_id": ticket.id,
                    "ticket_key": ticket.key,
                    "ticket_title": ticket.title,
                    "from": old_status,
                    "to": "blocked",
                    "actor": agent.name,
                },
            )
    await _write_system_comment(
        session,
        ticket.id,
        reason_body,
        ticket_key=ticket.key,
        run_id=run_id,
        workspace_id=workspace_id,
    )
    # Owner-facing summary in the epic chat, so a blocked child never goes unnoticed:
    # the system comment above lands on the child ticket itself; the owner's PM chat
    # lives on the epic, so mirror a short one-liner there (skipped for top-level
    # tickets — the comment above already is their chat).
    if ticket.parent_id is not None:
        await _notify_owner_chat(
            session,
            ticket,
            agent,
            f"{agent.name} menandai {ticket.key} → blocked: {_excerpt(reason_body)}",
            run_id=run_id,
            workspace_id=workspace_id,
        )


# Which failure modes get auto-retried. Decided per call site (`_finish_run`'s failure
# branches pass `retryable=` explicitly), not by inspecting `run.error` text: a
# missing/malformed ```map block or an opencode subprocess failure (exit code, stderr,
# binary not found, no run_ended event) are exactly the cases where re-running the
# agent with an explicit "run before failed" notice can succeed. State-machine
# rejections, guardrail trips, and user stops must NOT be retried — those are
# programming errors the agent cannot adapt to, deliberate brake activations, or
# human intent.


async def _retry_attempt_count(session: AsyncSession, run: Run, agent_id: str) -> int:
    """How many consecutive failed attempts the (ticket, agent) pair has accumulated.

    Walks the `parent_run_id` chain from this run backward. The chain is only ever
    created by auto-retries (never by manual retries or mentions — those schedule
    with no `parent_run_id`, breaking the chain and resetting the count to a fresh
    window, mirroring `loop_reset_at`/`handoff_depth` reset on human unblock), and
    only retryable failures spawn auto-retries at all, so every `failed` ancestor
    in the chain is by construction a retryable failure by the same agent.
    """
    count = 1  # this run itself is a failed attempt
    parent_id = run.parent_run_id
    while parent_id is not None:
        parent = await session.get(Run, parent_id)
        if parent is None or parent.status != "failed" or parent.agent_id != agent_id:
            break
        count += 1
        parent_id = parent.parent_run_id
    return count


async def _tail_text_from_run(session: AsyncSession, run_id: str) -> str:
    """Last chunk of the assistant text a run produced before failing, for replay
    into the retry prompt (same budget as `_block_ticket`'s tail replay)."""
    if run_id is None:
        return ""
    events = (
        await session.scalars(
            select(Event)
            .where(Event.run_id == run_id, Event.type == "assistant_text")
            .order_by(Event.seq)
        )
    ).all()
    chunks = []
    for ev in events:
        t = (ev.payload or {}).get("text")
        if isinstance(t, str):
            chunks.append(t)
    return "".join(chunks)[-_TAIL_CHARS:]


async def _handle_failed_run(
    session: AsyncSession,
    run: Run,
    ticket: Ticket,
    agent: Agent,
    error_body: str,
    *,
    retryable: bool,
    session_factory: async_sessionmaker,
) -> None:
    """Common handling for a failed ticket run: auto-retry or block.

    `error_body` is the failure description written to the ticket. When
    `retryable` and the (ticket, agent) attempt count is at most
    `workspace.guardrails["max_auto_retries"]` (so `max_auto_retries` = number of
    retries after the original failure), a child run is scheduled with
    `parent_run_id` chained to this one and the ticket stays unblocked —
    the agent gets another shot with the failure injected into its prompt.
    Otherwise the ticket is blocked as before (budget exhausted, non-retryable
    failure mode, or auto-retry disabled with `max_auto_retries=0`).
    """
    workspace = await session.get(Workspace, ticket.workspace_id)
    guardrails = (workspace.guardrails if workspace else None) or {}
    max_retries = int(guardrail_limit(guardrails, "max_auto_retries"))

    if retryable and session_factory is not None and max_retries > 0:
        attempt = await _retry_attempt_count(session, run, agent.id)
        if attempt <= max_retries:
            await _write_system_comment(
                session,
                ticket.id,
                f"{error_body}\n\nAuto-retry {attempt}/{max_retries} dijalankan — "
                f"agent diberitahu kegagalan ini di prompt-nya.",
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=ticket.workspace_id,
            )
            try:
                await schedule(
                    session,
                    session_factory,
                    ticket=ticket,
                    agent=agent,
                    trigger="auto",
                    parent_run_id=run.id,
                    exclude_run_id=run.id,
                )
            except GuardrailBlocked as exc:
                # schedule() already blocked the ticket and wrote a comment naming
                # the guardrail (e.g. max_cost_per_ticket exhausted by the failed
                # attempts) — just surface the reason on the failed run itself.
                run.error = str(exc)
            except RuntimeError:
                # workspace got paused mid-flight; the ticket stays unblocked and
                # the owner can resume later.
                pass
            return

    if retryable and max_retries > 0:
        error_body = (
            f"{error_body}\n\nAuto-retry habis (max_auto_retries={max_retries}x) "
            "pada (tiket, agent) ini — tiket diblok."
        )
    await _block_ticket(
        session,
        ticket,
        agent,
        error_body,
        run_id=run.id,
        workspace_id=ticket.workspace_id,
    )


async def _build_retry_notice(session: AsyncSession, run: Run, agent: Agent) -> str | None:
    """Failure notice injected into an auto-retry run's prompt (MAP-044).

    Only auto-retry children get one — `parent_run_id` alone is not enough, since
    handoff/auto runs also chain to their parent. Requires the parent to be a
    failed run; includes its error text and the tail of what the agent actually
    said before failing, so the retry can adapt instead of repeating the mistake.
    """
    if run.parent_run_id is None:
        return None
    parent = await session.get(Run, run.parent_run_id)
    if parent is None or parent.status != "failed":
        return None

    max_retries = 0
    if parent.ticket_id is not None:
        ticket = await session.get(Ticket, parent.ticket_id)
        workspace = await session.get(Workspace, ticket.workspace_id) if ticket else None
        guardrails = (workspace.guardrails if workspace else None) or {}
        max_retries = int(guardrail_limit(guardrails, "max_auto_retries"))

    attempt = await _retry_attempt_count(session, parent, agent.id)
    tail = await _tail_text_from_run(session, parent.id)

    notice = (
        "PERINGATAN: RUN SEBELUMNYA GAGAL. Kamu menjalankan tiket ini sebelumnya dan "
        f"gagal (attempt {attempt}/{max_retries or '?'}). Alasan kegagalan:\n\n"
        f"{parent.error or 'tidak diketahui'}"
    )
    if tail:
        notice += f"\n\nOutput terakhir kamu sebelumnya:\n\n```\n{tail}\n```"
    notice += (
        "\n\nBACA KONTRAK ```map DI BAWAH INI TELITI sebelum menjawab. "
        "Jangan ulangi kesalahan yang sama — kalau output kamu sebelumnya tidak "
        "menutup dengan blok ```map yang valid, pastikan blok itu ada dan "
        "lengkap kali ini. Kalau ada kendala teknis, coba pendekatan yang berbeda."
    )
    return notice


async def _notify_owner_chat(
    session: AsyncSession,
    ticket: Ticket,
    agent: Agent,
    body: str,
    *,
    run_id: str | None,
    workspace_id: str | None,
) -> None:
    """Post a System message on the owner's PM chat for a ticket action.

    The owner's chat with the PM lives on the epic (top-level ticket) — child
    tickets under it are worked autonomously and would otherwise be invisible
    to the owner. Whenever something significant happens to a child (blocked,
    failed, or the PM filing tickets[] from the epic), mirror a short system
    comment onto the epic so the owner sees it in the chat feed without opening
    each child.

    Only meaningful when the ticket has a parent; top-level tickets are their
    own chat and already received their own system comment (or the summary
    comment) by the caller. Message is written as `is_system=True` with no agent
    author, so the frontend renders it as a System notice and the notification
    bell/toast path treats it like any other system comment.
    """
    if ticket.parent_id is None:
        return
    parent = await session.get(Ticket, ticket.parent_id)
    if parent is None:
        return
    await _write_system_comment(
        session,
        parent.id,
        body,
        ticket_key=parent.key,
        run_id=run_id,
        workspace_id=workspace_id,
    )


def _excerpt(text: str, limit: int = 200) -> str:
    """Short excerpt of a (possibly long, multi-line) reason — used for chat one-liners."""
    excerpt = (text or "").strip()
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 3] + "..."
    return excerpt


async def recover_interrupted_runs(session_factory: async_sessionmaker) -> int:
    """Startup recovery — docs/02-tsd.md §4.5, ADR-004, MAP-026.

    No in-memory orchestrator state (`RUNNING`/`_PENDING`/`_BUSY`) survives a process
    restart, so any `Run` row still `running`/`queued` in the DB is orphaned: nothing
    will ever execute or finish it. Mark those `interrupted`, free their agents back to
    `idle`, and leave one system comment per affected ticket. Returns the count of runs
    recovered (0 on a clean shutdown — the common case).
    """
    async with session_factory() as session:
        result = await session.execute(select(Run).where(Run.status.in_(("running", "queued"))))
        runs = list(result.scalars())
        if not runs:
            return 0

        now = _now()
        agent_ids: set[str] = set()
        runs_by_ticket: dict[str, list[Run]] = defaultdict(list)
        routine_ids: set[str] = set()
        conversation_ids: set[str] = set()
        for run in runs:
            run.status = "interrupted"
            run.ended_at = now
            agent_ids.add(run.agent_id)
            if run.ticket_id is not None:
                runs_by_ticket[run.ticket_id].append(run)
            if run.routine_id is not None:
                routine_ids.add(run.routine_id)
            if run.conversation_id is not None:
                conversation_ids.add(run.conversation_id)

        if agent_ids:
            agents = await session.execute(select(Agent).where(Agent.id.in_(agent_ids)))
            for agent in agents.scalars():
                agent.status = "idle"

        for ticket_id, ticket_runs in runs_by_ticket.items():
            run_ids = ", ".join(r.id for r in ticket_runs)
            body = (
                f"Backend restarted while {len(ticket_runs)} run(s) were in flight "
                f"({run_ids}). Marked `interrupted`."
            )
            await _write_system_comment(session, ticket_id, body)

        if conversation_ids:
            conversations = await session.execute(
                select(Conversation).where(Conversation.id.in_(conversation_ids))
            )
            for conversation in conversations.scalars():
                await _write_system_message(
                    session,
                    conversation,
                    "Backend restarted while a PM reply was in flight. Marked `interrupted` "
                    "— kirim ulang pesanmu kalau PM tidak membalas.",
                    run_id=None,
                    workspace_id=conversation.workspace_id,
                )

        if routine_ids:
            routines = await session.execute(select(Routine).where(Routine.id.in_(routine_ids)))
            for routine in routines.scalars():
                routine.status = "idle"

        await session.commit()
        return len(runs)


async def execute(session_factory: async_sessionmaker, run_id: str) -> None:
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return
        ticket = await session.get(Ticket, run.ticket_id) if run.ticket_id else None
        agent = await session.get(Agent, run.agent_id)
        if ticket is not None:
            workspace = await session.get(Workspace, ticket.workspace_id)
        elif run.conversation_id is not None:
            conversation = await session.get(Conversation, run.conversation_id)
            workspace = (
                await session.get(Workspace, conversation.workspace_id) if conversation else None
            )
        else:
            routine = await session.get(Routine, run.routine_id) if run.routine_id else None
            workspace = await session.get(Workspace, routine.workspace_id) if routine else None

        try:
            run.status = "running"
            run.started_at = _now()
            agent.status = "working"
            auto_transition_old_status: str | None = None
            if ticket is not None and ticket.status in ("backlog", "todo"):
                # docs/03-agent-design.md §5: "todo -> in_progress | otomatis saat run
                # dimulai, owner" — a system-driven transition (like _block_ticket's
                # any -> blocked), not attributable to any agent role, so it bypasses
                # can_transition entirely rather than going through it as some role.
                # Needed so MAP-030's auto-scheduled tickets[] children (created at
                # status="todo") can run to completion without a human PATCH in
                # between. Also covers a ticket still at "backlog" (never manually
                # moved to todo first).
                auto_transition_old_status = ticket.status
                ticket.status = "in_progress"
                # Event publishing is deferred until after run_started below (kept
                # seq=1 for run_started, matching every consumer's "events[0] is
                # run_started with the prompt" assumption) — the DB row commit here
                # doesn't depend on that ordering, only the SSE/event-bus fan-out does.
            await session.commit()

            # Auto-retry children (trigger="auto" with a parent failed run) get the
            # failure notice injected, and deliberately start with a FRESH session
            # (no `-s` resume): the retry prompt already re-narrates the failure and
            # the old conversation is exactly what went wrong.
            retry_notice = await _build_retry_notice(session, run, agent)

            if ticket is not None:
                prompt = await _build_prompt_for(
                    session, workspace, agent, ticket, run.trigger, retry_notice=retry_notice
                )
            elif run.conversation_id is not None:
                prompt = await _build_chat_prompt_for(session, workspace, agent, conversation)
            else:
                routine = await session.get(Routine, run.routine_id)
                prompt = await _build_routine_prompt_for(session, workspace, agent, routine)

            await event_bus.publish(
                session,
                run_id=run.id,
                workspace_id=workspace.id,
                type="run_started",
                payload={"prompt": prompt},
            )

            if auto_transition_old_status is not None:
                await event_bus.publish(
                    session,
                    run_id=run.id,
                    workspace_id=workspace.id,
                    type="status_change",
                    payload={
                        "ticket_id": ticket.id,
                        "ticket_key": ticket.key,
                        "ticket_title": ticket.title,
                        "from": auto_transition_old_status,
                        "to": "in_progress",
                        "actor": agent.name,
                    },
                )
                await _write_system_comment(
                    session,
                    ticket.id,
                    f"Status changed from {auto_transition_old_status} to in_progress (run started)",
                    ticket_key=ticket.key,
                    run_id=run.id,
                    workspace_id=workspace.id,
                )
                await session.commit()

            if ticket is not None:
                attachments = (
                    await session.scalars(
                        select(Attachment).where(Attachment.ticket_id == ticket.id)
                    )
                ).all()
            elif run.conversation_id is not None:
                attachments = (
                    await session.scalars(
                        select(ConversationAttachment).where(
                            ConversationAttachment.conversation_id == run.conversation_id
                        )
                    )
                ).all()
            else:
                attachments = []
            attachment_paths = [str(_storage_dir() / a.path) for a in attachments]

            if ticket is not None:
                prev_run = await session.scalar(
                    select(Run)
                    .where(
                        Run.ticket_id == ticket.id,
                        Run.agent_id == agent.id,
                        Run.id != run.id,
                        Run.session_id.is_not(None),
                    )
                    .order_by(Run.started_at.desc())
                    .limit(1)
                )
            elif run.conversation_id is not None:
                prev_run = await session.scalar(
                    select(Run)
                    .where(
                        Run.conversation_id == run.conversation_id,
                        Run.agent_id == agent.id,
                        Run.id != run.id,
                        Run.session_id.is_not(None),
                    )
                    .order_by(Run.started_at.desc())
                    .limit(1)
                )
            else:
                prev_run = None

            # Auto-retry runs must not resume the failed session (fresh conversation
            # with the failure notice instead) — see retry_notice above.
            if retry_notice is not None:
                prev_run = None

            worktree_path: str = workspace.repo_path
            merge_into: str = workspace.main_branch or "main"
            if ticket:
                epic_branch: str | None = None
                if ticket.parent_id:
                    parent_ticket = await session.get(Ticket, ticket.parent_id)
                    if parent_ticket:
                        epic_branch = git_module._epic_branch_name(parent_ticket.title)
                        merge_into = epic_branch
                try:
                    worktree_path = git_module.prepare_worktree(
                        workspace.repo_path,
                        ticket.key,
                        epic_branch=epic_branch,
                        base_branch=workspace.main_branch or "main",
                    )
                except git_module.GitError as ge:
                    await _write_system_comment(
                        session,
                        ticket.id,
                        f"Gagal membuat worktree untuk {ticket.key}: {ge}. "
                        "Run akan menggunakan repo utama (mungkin ada konflik).",
                        ticket_key=ticket.key,
                        workspace_id=workspace.id,
                    )
                    merge_into = workspace.main_branch or "main"

            ctx = RunContext(
                run_id=run.id,
                workspace_id=workspace.id,
                agent_id=agent.id,
                agent_model=run.model or await _global_orchestrator_model(session),
                ticket_id=ticket.id if ticket else None,
                repo_path=worktree_path,
                prompt=prompt,
                agent_name=agent.name,
                ticket_key=ticket.key if ticket else "",
                attachments=attachment_paths,
                prev_session_id=prev_run.session_id if prev_run else None,
                guardrails=workspace.guardrails or {},
                cancel_event=asyncio.Event(),
            )
            _CANCEL_EVENTS[run.id] = ctx.cancel_event

            tool_cls = TOOLS[agent.tool_kind]
            tool = tool_cls()

            text_buffer: list[str] = []
            terminal: AdapterEvent | None = None
            running_cost = 0.0
            # Set by whichever runtime guardrail (timeout/cost-per-run) trips first, so the
            # eventual `cancelled` terminal event can be attributed to it instead of being
            # indistinguishable from a user-initiated stop (see `stop()`, which also just
            # sets `cancel_event`).
            guardrail_cancel_reason: str | None = None

            async def _timeout_watchdog() -> None:
                nonlocal guardrail_cancel_reason
                timeout_sec = float(guardrail_limit(ctx.guardrails, "run_timeout_sec"))
                await asyncio.sleep(timeout_sec)
                if not ctx.cancel_event.is_set():
                    guardrail_cancel_reason = over_run_timeout(ctx.guardrails, timeout_sec)
                    ctx.cancel_event.set()

            watchdog_task = asyncio.create_task(_timeout_watchdog())
            try:
                async for ev in tool.run(ctx):
                    if ev.type == "run_ended":
                        terminal = ev
                    else:
                        _accumulate_text(text_buffer, ev)
                        cost = ev.payload.get("cost")
                        if cost is not None:
                            running_cost += float(cost)
                            if guardrail_cancel_reason is None:
                                reason = over_cost_per_run(ctx.guardrails, running_cost)
                                if reason is not None:
                                    guardrail_cancel_reason = reason
                                    ctx.cancel_event.set()
                    await event_bus.publish(
                        session,
                        run_id=run.id,
                        workspace_id=workspace.id,
                        type=ev.type,
                        payload=ev.payload,
                    )
            finally:
                watchdog_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watchdog_task

            await _finish_run(
                session,
                run,
                ticket,
                agent,
                terminal,
                "".join(text_buffer),
                guardrail_cancel_reason,
                worktree_path,
                merge_into,
                session_factory=session_factory,
            )

        except Exception as exc:  # noqa: BLE001 - must never leave run/agent stuck
            await session.rollback()
            run = await session.get(Run, run_id)
            ticket = await session.get(Ticket, run.ticket_id) if run.ticket_id else None
            agent = await session.get(Agent, run.agent_id)
            run.status = "failed"
            run.error = str(exc)
            run.ended_at = _now()
            if ticket is not None:
                await _block_ticket(
                    session,
                    ticket,
                    agent,
                    f"Run gagal karena error internal: {exc}",
                    run_id=run.id,
                    workspace_id=ticket.workspace_id,
                )
            elif run.conversation_id is not None:
                conversation = await session.get(Conversation, run.conversation_id)
                if conversation is not None:
                    await _write_system_message(
                        session,
                        conversation,
                        f"Run chat gagal karena error internal: {exc}",
                        run_id=run.id,
                        workspace_id=conversation.workspace_id,
                    )
            else:
                routine = await session.get(Routine, run.routine_id) if run.routine_id else None
                if routine is not None:
                    routine.status = "idle"
            agent.status = "idle"
            await session.commit()
        finally:
            _CANCEL_EVENTS.pop(run_id, None)


# Shown to a PM only when directly mentioned (not an automatic/scheduled run): lets the
# owner brainstorm conversationally before committing to a tickets[] breakdown, since
# the parser already treats a same-status "in_progress" report with no tickets[] as a
# no-op (MAP-030) — this is purely a prompt hint, no parser/state-machine change needed.
# The structural gate (no tickets[] before the owner approves) lives in report.py's
# ticket_approved flag; this text makes the expected behavior explicit to the model.
_PM_MENTION_EXTRA_INSTRUCTIONS = (
    "Ini pesan langsung dari owner (bukan run otomatis). "
    "ATURAN WAJIB: JANGAN pernah langsung membuat tickets[] di balasan pertama atau "
    "sebelum owner secara eksplisit menyetujui. Kamu WAJIB bersikap eksploratif dulu: "
    "gali informasi yang detail dari owner (tujuan, lingkup, kriteria sukses, batasan, "
    "asumsi yang belum jelas) — tapi jangan berlebihan, cukup yang relevan. Kalau "
    "idenya belum cukup jelas, balas dengan pertanyaan klarifikasi: status: "
    "in_progress, TANPA tickets[]. "
    "Kalau sudah cukup jelas, TAWARKAN FINAL PLAN dulu di summary (status: "
    "in_progress, TANPA tickets[]) dan minta owner menyetujui — misalnya 'balas "
    "\"oke lanjut\" untuk menyetujui'. FINAL PLAN ini WAJIB berisi PERSIS LIMA bagian, "
    "ditulis satu-satu supaya owner mudah membaca sebelum approve: "
    "(1) Requirement — ringkasan permintaan owner dengan bahasamu sendiri, bukan "
    "copy-paste chat; "
    "(2) Goal — tujuan/hasil akhir yang ingin dicapai; "
    "(3) Epic tujuan — cek katalog epic yang sudah ada di kontrak ```map di bawah; "
    "WAJIB sebutkan epic mana yang relevan (reuse) kalau ada, atau nyatakan 'epic "
    "baru: <nama>' HANYA kalau ini benar-benar area fitur besar baru; "
    "(4) Breakdown sprint — jadi berapa sprint, dan goal singkat tiap sprint (BUKAN "
    "nama fitur — sprint cuma timebox, nama fitur/scope itu urusan epic di poin 3); "
    "(5) Estimasi durasi — total dan/atau per sprint, dihitung realistis untuk "
    "kecepatan kerja agent AI (jauh lebih cepat dari estimasi tim manusia) — jangan "
    "menyalin rule-of-thumb durasi sprint manusia (mis. '2 minggu per sprint'). "
    "Kamu juga BOLEH chat owner duluan kapan pun di tengah perjalanan kalau ada hal "
    "yang butuh klarifikasi. "
    "Hanya setelah owner menyetujui barulah balasan berikutnya boleh membawa "
    "tickets[] — sertakan juga `sprints:` (goal & durasi tiap sprint, BUKAN nama "
    "fitur) dan, di tiap item tickets[], `epic`/`sprint`/`duration` sesuai kontrak "
    "di bawah (epic yang sudah dijanjikan di FINAL PLAN wajib konsisten dengan "
    "`epic:` yang benar-benar ditulis di tickets[]). "
    "Kamu juga bisa melihat daftar tiket LAIN di workspace ini di bawah (bagian "
    "'Tiket lain di workspace ini') — kalau owner minta kamu review/rapikan sprint "
    "tiket-tiket yang sudah ada, gunakan `updates:` dengan field `sprint`/`duration` "
    "per tiket untuk memindahkan/memperbaikinya (tidak perlu tickets[] baru untuk ini)."
)


async def _build_prompt_for(
    session, workspace: Workspace, agent: Agent, ticket: Ticket, trigger: str,
    retry_notice: str | None = None,
) -> str:
    role_map = await _role_map(session)
    roster = (
        await session.scalars(select(Agent).where(Agent.workspace_id == workspace.id))
    ).all()
    team_roster = [
        _agent_info_from(a, role_map.get(a.role)) for a in roster
    ]
    agent_info = _agent_info_from(agent, role_map.get(agent.role))

    attachments = (
        await session.scalars(select(Attachment).where(Attachment.ticket_id == ticket.id))
    ).all()
    attachment_names = [a.filename for a in attachments]

    comments = (
        await session.scalars(
            select(Comment)
            .where(Comment.ticket_id == ticket.id)
            .order_by(Comment.created_at.desc())
            .limit(5)
        )
    ).all()
    comments = list(reversed(comments))
    recent_comments = []
    for c in comments:
        author = "system"
        if c.author_agent_id:
            author_agent = await session.get(Agent, c.author_agent_id)
            author = author_agent.name if author_agent else "unknown"
        recent_comments.append(
            CommentInfo(author=author, body=c.body, created_at=c.created_at.isoformat())
        )

    prior_runs = (
        await session.scalars(
            select(Run)
            .where(Run.ticket_id == ticket.id, Run.status == "done", Run.report.is_not(None))
            .order_by(Run.started_at)
        )
    ).all()
    previous_summaries = [
        r.report.get("summary") for r in prior_runs if r.report and r.report.get("summary")
    ]

    # "Review round" = how many prior runs on this ticket were done by an agent in a
    # reviewer role (`role.is_reviewer`), regardless of which reviewer. Their summaries
    # double as the previous-review feedback shown to the anti-loop block.
    review_round = 0
    previous_review_feedback: list[str] = []
    if agent_info.is_reviewer:
        for r in prior_runs:
            run_agent = await session.get(Agent, r.agent_id)
            prior_role = role_map.get(run_agent.role) if run_agent is not None else None
            if prior_role is not None and prior_role.is_reviewer:
                review_round += 1
                if r.report and r.report.get("summary"):
                    previous_review_feedback.append(r.report["summary"])

    # Sprint context for the ticket — the base-prompt sprint rule references the
    # active sprint, so the agent must see which sprint this ticket actually sits
    # in (and whether it's the active one) or the rule is unverifiable.
    ticket_sprint_name: str | None = None
    ticket_sprint_active: bool | None = None
    if ticket.sprint_id is not None:
        t_sprint = await session.get(Sprint, ticket.sprint_id)
        if t_sprint is not None:
            ticket_sprint_name = t_sprint.name
            ticket_sprint_active = t_sprint.status == "active"

    ticket_info = TicketInfo(
        key=ticket.key,
        title=ticket.title,
        status=ticket.status,
        priority=ticket.priority,
        description=ticket.description or "",
        sprint_name=ticket_sprint_name,
        sprint_active=ticket_sprint_active,
    )

    extra_instructions = (
        _PM_MENTION_EXTRA_INSTRUCTIONS if agent.role == "pm" and trigger == "mention" else None
    )

    if retry_notice is not None:
        extra_instructions = (
            f"{extra_instructions}\n\n{retry_notice}" if extra_instructions else retry_notice
        )

    workspace_tickets: list[WorkspaceTicketSummary] = []
    if agent.role == "pm" and trigger == "mention":
        workspace_tickets = await _workspace_ticket_summaries(session, workspace.id, ticket.id)

    # Existing artifact group names (Artifacts menu) so the agent reuses a relevant
    # group instead of inventing near-duplicate names.
    artifact_groups = (
        await session.scalars(
            select(ArtifactGroup).where(ArtifactGroup.workspace_id == workspace.id)
        )
    ).all()
    existing_artifact_groups = sorted({g.name for g in artifact_groups})

    # Artifact catalog (Artifacts menu) so every agent can read/search what's already
    # been published before producing new files — most recent first, bounded.
    catalog_rows = (
        await session.execute(
            select(Attachment, Ticket.key, ArtifactGroup.name)
            .join(Ticket, Attachment.ticket_id == Ticket.id)
            .outerjoin(ArtifactGroup, Attachment.group_id == ArtifactGroup.id)
            .where(Ticket.workspace_id == workspace.id, Attachment.origin == "agent")
            .order_by(Attachment.created_at.desc())
            .limit(_ARTIFACT_CATALOG_LIMIT)
        )
    ).all()
    artifact_catalog = [
        f"[{group_name or 'Ungrouped'}] {a.filename} ({ticket_key})"
        + (f" — {a.description}" if a.description else "")
        for a, ticket_key, group_name in catalog_rows
    ]

    # Existing epics (top-level tickets) so PM/QA/Pentester reuse a relevant one via
    # `tickets[].epic` instead of spawning a fresh one-off epic every time
    # (docs/03-agent-design.md §3) — only computed for roles that can declare tickets[]
    # at all. Existing sprints (pure timeboxes, decoupled from epic/scope) so the same
    # roles reuse a sprint name exactly instead of drifting into near-duplicates.
    existing_epics: list[str] = []
    existing_sprints: list[str] = []
    if agent_info.may_declare_tickets:
        epic_rows = (
            await session.scalars(
                select(Ticket)
                .where(Ticket.workspace_id == workspace.id, Ticket.parent_id.is_(None))
                .order_by(Ticket.updated_at.desc())
                .limit(_EPIC_CATALOG_LIMIT)
            )
        ).all()
        existing_epics = [f"{e.key} — {e.title}" for e in epic_rows]

        sprint_rows = (
            await session.scalars(select(Sprint).where(Sprint.workspace_id == workspace.id))
        ).all()
        existing_sprints = sorted({s.name for s in sprint_rows})

    # This agent's own cross-ticket memory notes (```map `memory:`, docs/03-agent-design.md
    # §3) — most recent N, re-ordered chronologically like `previous_summaries` above.
    memory_rows = (
        await session.scalars(
            select(AgentMemory)
            .where(AgentMemory.agent_id == agent.id)
            .order_by(AgentMemory.created_at.desc())
            .limit(_AGENT_MEMORY_PROMPT_LIMIT)
        )
    ).all()
    agent_memories = [m.note for m in reversed(memory_rows)]

    # Workspace description (set at creation, on the homepage form): project/product context
    # shown to every agent, same mechanism as workflow_prompt below. Empty by default.
    if workspace.description and workspace.description.strip():
        description_block = f"Konteks proyek/workspace ini:\n\n{workspace.description.strip()}"
        extra_instructions = (
            f"{extra_instructions}\n\n{description_block}"
            if extra_instructions
            else description_block
        )

    # Workspace workflow prompt (Settings page): appended to every agent prompt as an
    # additional instruction block, before the ```map contract. Empty by default, so
    # behavior is unchanged unless the owner configured one.
    if workspace.workflow_prompt and workspace.workflow_prompt.strip():
        workflow_block = f"Ini alur kerja tim yang ditentukan owner workspace. Ikuti:\n\n{workspace.workflow_prompt.strip()}"
        extra_instructions = (
            f"{extra_instructions}\n\n{workflow_block}"
            if extra_instructions
            else workflow_block
        )

    return build_prompt(
        agent_info,
        workspace.repo_path,
        team_roster,
        ticket_info,
        attachments=attachment_names,
        recent_comments=recent_comments,
        previous_summaries=previous_summaries,
        review_round=review_round,
        previous_review_feedback=previous_review_feedback,
        extra_instructions=extra_instructions,
        time_unit=workspace.time_unit,
        workspace_tickets=workspace_tickets,
        existing_artifact_groups=existing_artifact_groups,
        agent_memories=agent_memories,
        artifact_catalog=artifact_catalog,
        sprint_creator_roles=set(workspace.sprint_creator_roles or ["pm"]),
        existing_epics=existing_epics,
        existing_sprints=existing_sprints,
    )


async def _build_routine_prompt_for(
    session, workspace: Workspace, agent: Agent, routine: Routine
) -> str:
    """Assemble a routine-run prompt: BASE + role block + routine prompt + workspace
    context (description/workflow) + artifact catalog + agent memory + routine ```map
    contract. No ticket context — the routine's own prompt is the task.
    """
    role_map = await _role_map(session)
    roster = (
        await session.scalars(select(Agent).where(Agent.workspace_id == workspace.id))
    ).all()
    team_roster = [
        _agent_info_from(a, role_map.get(a.role)) for a in roster
    ]
    agent_info = _agent_info_from(agent, role_map.get(agent.role))

    # Artifact catalog (Artifacts menu) so the agent can read/search what's published.
    catalog_rows = (
        await session.execute(
            select(Attachment, Ticket.key, ArtifactGroup.name)
            .join(Ticket, Attachment.ticket_id == Ticket.id)
            .outerjoin(ArtifactGroup, Attachment.group_id == ArtifactGroup.id)
            .where(Ticket.workspace_id == workspace.id, Attachment.origin == "agent")
            .order_by(Attachment.created_at.desc())
            .limit(_ARTIFACT_CATALOG_LIMIT)
        )
    ).all()
    artifact_catalog = [
        f"[{group_name or 'Ungrouped'}] {a.filename} ({ticket_key})"
        + (f" — {a.description}" if a.description else "")
        for a, ticket_key, group_name in catalog_rows
    ]

    # This agent's own cross-ticket memory notes.
    memory_rows = (
        await session.scalars(
            select(AgentMemory)
            .where(AgentMemory.agent_id == agent.id)
            .order_by(AgentMemory.created_at.desc())
            .limit(_AGENT_MEMORY_PROMPT_LIMIT)
        )
    ).all()
    agent_memories = [m.note for m in reversed(memory_rows)]

    # Same epic/sprint reuse catalogs as ticket runs (docs/03-agent-design.md §3).
    existing_epics: list[str] = []
    existing_sprints: list[str] = []
    if agent_info.may_declare_tickets:
        epic_rows = (
            await session.scalars(
                select(Ticket)
                .where(Ticket.workspace_id == workspace.id, Ticket.parent_id.is_(None))
                .order_by(Ticket.updated_at.desc())
                .limit(_EPIC_CATALOG_LIMIT)
            )
        ).all()
        existing_epics = [f"{e.key} — {e.title}" for e in epic_rows]

        sprint_rows = (
            await session.scalars(select(Sprint).where(Sprint.workspace_id == workspace.id))
        ).all()
        existing_sprints = sorted({s.name for s in sprint_rows})

    extra_instructions: str | None = None
    if workspace.description and workspace.description.strip():
        extra_instructions = f"Konteks proyek/workspace ini:\n\n{workspace.description.strip()}"
    if workspace.workflow_prompt and workspace.workflow_prompt.strip():
        workflow_block = f"Ini alur kerja tim yang ditentukan owner workspace. Ikuti:\n\n{workspace.workflow_prompt.strip()}"
        extra_instructions = (
            f"{extra_instructions}\n\n{workflow_block}"
            if extra_instructions
            else workflow_block
        )

    return build_routine_prompt(
        agent_info,
        workspace.repo_path,
        team_roster,
        routine_prompt=routine.prompt,
        extra_instructions=extra_instructions,
        agent_memories=agent_memories,
        artifact_catalog=artifact_catalog,
        sprint_creator_roles=set(workspace.sprint_creator_roles or ["pm"]),
        existing_epics=existing_epics,
        existing_sprints=existing_sprints,
    )


async def _build_chat_prompt_for(
    session, workspace: Workspace, agent: Agent, conversation: Conversation
) -> str:
    """Assemble a chat-run prompt: BASE + role block + conversation transcript +
    workspace tickets + artifact catalog + agent memory + chat ```map contract.
    """
    role_map = await _role_map(session)
    roster = (
        await session.scalars(select(Agent).where(Agent.workspace_id == workspace.id))
    ).all()
    team_roster = [
        _agent_info_from(a, role_map.get(a.role)) for a in roster
    ]
    agent_info = _agent_info_from(agent, role_map.get(agent.role))

    messages = (
        await session.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(20)
        )
    ).all()
    chat_messages: list[ChatMessageInfo] = []
    for m in reversed(messages):
        author = "Owner"
        if m.is_system:
            author = "System"
        elif m.author_agent_id:
            author_agent = await session.get(Agent, m.author_agent_id)
            author = author_agent.name if author_agent else "Agent"
        chat_messages.append(
            ChatMessageInfo(
                author=author,
                body=m.body,
                created_at=m.created_at.isoformat(),
                is_system=m.is_system,
            )
        )

    attach_rows = (
        await session.scalars(
            select(ConversationAttachment).where(
                ConversationAttachment.conversation_id == conversation.id
            )
        )
    ).all()
    attachment_names = [a.filename for a in attach_rows]

    # All workspace tickets for context, like PM owner-chat runs get (the PM may
    # need to act on or reference other tickets from the chat).
    workspace_tickets = await _workspace_ticket_summaries(
        session, workspace.id, exclude_ticket_id=""
    )

    catalog_rows = (
        await session.execute(
            select(Attachment, Ticket.key, ArtifactGroup.name)
            .join(Ticket, Attachment.ticket_id == Ticket.id)
            .outerjoin(ArtifactGroup, Attachment.group_id == ArtifactGroup.id)
            .where(Ticket.workspace_id == workspace.id, Attachment.origin == "agent")
            .order_by(Attachment.created_at.desc())
            .limit(_ARTIFACT_CATALOG_LIMIT)
        )
    ).all()
    artifact_catalog = [
        f"[{group_name or 'Ungrouped'}] {a.filename} ({ticket_key})"
        + (f" — {a.description}" if a.description else "")
        for a, ticket_key, group_name in catalog_rows
    ]

    memory_rows = (
        await session.scalars(
            select(AgentMemory)
            .where(AgentMemory.agent_id == agent.id)
            .order_by(AgentMemory.created_at.desc())
            .limit(_AGENT_MEMORY_PROMPT_LIMIT)
        )
    ).all()
    agent_memories = [m.note for m in reversed(memory_rows)]

    existing_epics: list[str] = []
    existing_sprints: list[str] = []
    has_active_sprint = True
    if agent_info.may_declare_tickets:
        epic_rows = (
            await session.scalars(
                select(Ticket)
                .where(Ticket.workspace_id == workspace.id, Ticket.parent_id.is_(None))
                .order_by(Ticket.updated_at.desc())
                .limit(_EPIC_CATALOG_LIMIT)
            )
        ).all()
        existing_epics = [f"{e.key} — {e.title}" for e in epic_rows]

        sprint_rows = (
            await session.scalars(select(Sprint).where(Sprint.workspace_id == workspace.id))
        ).all()
        existing_sprints = sorted({s.name for s in sprint_rows})
        has_active_sprint = any(s.status == "active" for s in sprint_rows)

    extra_instructions: str | None = None
    if workspace.description and workspace.description.strip():
        extra_instructions = f"Konteks proyek/workspace ini:\n\n{workspace.description.strip()}"
    if workspace.workflow_prompt and workspace.workflow_prompt.strip():
        workflow_block = f"Ini alur kerja tim yang ditentukan owner workspace. Ikuti:\n\n{workspace.workflow_prompt.strip()}"
        extra_instructions = (
            f"{extra_instructions}\n\n{workflow_block}"
            if extra_instructions
            else workflow_block
        )

    prompt = build_chat_prompt(
        agent_info,
        workspace.repo_path,
        team_roster,
        conversation_title=conversation.title,
        messages=chat_messages,
        attachments=attachment_names,
        linked_ticket=conversation.linked_ticket_key,
        workspace_tickets=workspace_tickets,
        artifact_catalog=artifact_catalog,
        agent_memories=agent_memories,
        sprint_creator_roles=set(workspace.sprint_creator_roles or ["pm"]),
        existing_epics=existing_epics,
        existing_sprints=existing_sprints,
        has_active_sprint=has_active_sprint,
    )
    if extra_instructions:
        prompt = f"{extra_instructions}\n\n{prompt}"
    return prompt


# Cap on how many other-tickets get listed in a PM owner-chat prompt (see
# _workspace_ticket_summaries) — cheap insurance against unbounded prompt growth on
# large workspaces; most-recently-updated tickets are the ones most likely relevant.
_PM_CHAT_TICKET_LIST_LIMIT = 60


async def _workspace_ticket_summaries(
    session: AsyncSession, workspace_id: str, exclude_ticket_id: str
) -> list[WorkspaceTicketSummary]:
    """Snapshot of the rest of the workspace's tickets for PM's owner-chat prompt

    (see _PM_MENTION_EXTRA_INSTRUCTIONS) so it can review/fix sprint assignment
    across existing tickets, not just the one it's currently chatting on.
    """
    sprints = (
        await session.scalars(select(Sprint).where(Sprint.workspace_id == workspace_id))
    ).all()
    sprint_names = {s.id: s.name for s in sprints}

    tickets = (
        await session.scalars(
            select(Ticket)
            .where(Ticket.workspace_id == workspace_id, Ticket.id != exclude_ticket_id)
            .order_by(Ticket.updated_at.desc())
            .limit(_PM_CHAT_TICKET_LIST_LIMIT)
        )
    ).all()
    return [
        WorkspaceTicketSummary(
            key=t.key,
            title=t.title,
            status=t.status,
            priority=t.priority,
            sprint_name=sprint_names.get(t.sprint_id),
        )
        for t in tickets
    ]


async def _finish_routine_run(
    session: AsyncSession,
    run: Run,
    agent: Agent,
    terminal: AdapterEvent | None,
    accumulated_text: str,
    guardrail_cancel_reason: str | None = None,
    *,
    session_factory: async_sessionmaker | None = None,
) -> None:
    """Finish a routine run (no ticket): parse the ```map block and execute its
    side-effect actions (comments/tickets/updates/memory/artifacts) — never a status
    transition, handoff, or block. The routine's own status is synced back to `idle`.
    """
    routine = await session.get(Routine, run.routine_id) if run.routine_id else None
    workspace_id = routine.workspace_id if routine else None

    if terminal is None:
        run.status = "failed"
        run.error = "adapter finished without a run_ended event"
        run.ended_at = _now()
        if routine is not None:
            routine.status = "idle"
        agent.status = "idle"
        await session.commit()
        return

    status = terminal.payload.get("status")
    run.session_id = terminal.payload.get("session_id") or run.session_id
    run.tokens_in = int(terminal.payload.get("tokens_in") or 0)
    run.tokens_out = int(terminal.payload.get("tokens_out") or 0)
    run.cost = float(terminal.payload.get("cost") or 0.0)
    run.ended_at = _now()

    if status == "cancelled":
        run.status = "cancelled"
        if guardrail_cancel_reason is not None:
            run.error = guardrail_cancel_reason
        if routine is not None:
            routine.status = "idle"
        agent.status = "idle"
        await session.commit()
        return

    if status == "failed":
        run.status = "failed"
        run.error = terminal.payload.get("error") or "run failed"
        if routine is not None:
            routine.status = "idle"
        agent.status = "idle"
        await session.commit()
        return

    valid_names = {
        a.name
        for a in (
            await session.scalars(
                select(Agent).where(Agent.workspace_id == workspace_id)
            )
        ).all()
    }
    workspace = await session.get(Workspace, workspace_id)
    role_map = await _role_map(session)
    actor_role = role_map.get(agent.role)
    parsed = parse_report(
        accumulated_text,
        agent.role,
        valid_names,
        actor_name=agent.name,
        ticket_approved=True,
        sprint_creator_roles=set((workspace.sprint_creator_roles if workspace else None) or ["pm"]),
        no_ticket_mode=True,
        valid_roles=set(role_map),
        may_declare_tickets=actor_role.may_declare_tickets if actor_role else False,
        may_manage_artifacts=actor_role.may_manage_artifacts if actor_role else False,
        is_pm=agent.role == "pm",
    )

    if not parsed.ok:
        run.status = "failed"
        run.error = parsed.reason
        run.report = None
        tail = accumulated_text[-_TAIL_CHARS:]
        if routine is not None:
            routine.status = "idle"
        agent.status = "idle"
        await session.commit()
        return

    # `comments:` — routine-only: comment on other tickets (author = this agent).
    comments_report: list[dict] = []
    comment_skip_notes: list[str] = []
    for draft in parsed.comments:
        target = await session.scalar(
            select(Ticket).where(
                Ticket.workspace_id == workspace_id, Ticket.key == draft.ticket_key
            )
        )
        if target is None:
            comment_skip_notes.append(f"{draft.ticket_key}: tiket tidak ditemukan di workspace ini")
            continue
        comment = Comment(
            ticket_id=target.id, author_agent_id=agent.id, is_system=False, body=draft.body
        )
        session.add(comment)
        await session.flush()
        mentioned = await _record_body_mentions(
            session, comment, draft.body, workspace_id=workspace_id, author_agent_id=agent.id
        )
        if session_factory is not None:
            for mentioned_agent in mentioned:
                if not mentioned_agent.enabled or mentioned_agent.status == "disabled":
                    continue
                try:
                    await schedule(
                        session, session_factory, ticket=target, agent=mentioned_agent, trigger="mention"
                    )
                except (GuardrailBlocked, RuntimeError):
                    continue
        await event_bus.publish(
            session,
            run_id=run.id,
            workspace_id=workspace_id,
            type="comment",
            payload={
                "ticket_id": target.id,
                "ticket_key": target.key,
                "is_system": False,
                "author": agent.name,
                "body_preview": _comment_preview(draft.body),
            },
        )
        comments_report.append({"ticket": draft.ticket_key, "applied": True})

    if comment_skip_notes:
        run.error = "; ".join(comment_skip_notes)

    # `updates:` — same semantics as ticket runs (modify other tickets' fields).
    _VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
    updates_report: list[dict] = []
    source_skip_notes: list[str] = []
    for draft in parsed.updates:
        target = await session.scalar(
            select(Ticket).where(
                Ticket.workspace_id == workspace_id, Ticket.key == draft.ticket_key
            )
        )
        if target is None:
            source_skip_notes.append(f"{draft.ticket_key}: tiket tidak ditemukan di workspace ini")
            continue

        applied: list[str] = []
        skipped: list[str] = []

        if draft.status is not None:
            if draft.status not in STATUSES:
                skipped.append(f"status '{draft.status}' tidak dikenal")
            else:
                allowed, reason = can_transition(target.status, draft.status, agent.role)
                if not allowed:
                    skipped.append(f"status -> {draft.status} ditolak: {reason}")
                else:
                    target_old_status = target.status
                    target.status = draft.status
                    applied.append(f"status → {draft.status}")
                    await event_bus.publish(
                        session,
                        run_id=run.id,
                        workspace_id=workspace_id,
                        type="status_change",
                        payload={
                            "ticket_id": target.id,
                            "ticket_key": target.key,
                            "ticket_title": target.title,
                            "from": target_old_status,
                            "to": draft.status,
                            "actor": agent.name,
                        },
                    )

        if draft.priority is not None:
            if draft.priority not in _VALID_PRIORITIES:
                skipped.append(f"priority '{draft.priority}' tidak valid")
            else:
                target.priority = draft.priority
                applied.append(f"priority → {draft.priority}")

        if draft.assignee is not None:
            assignee_agent = await session.scalar(
                select(Agent).where(
                    Agent.workspace_id == target.workspace_id, Agent.name == draft.assignee
                )
            )
            if assignee_agent is None:
                skipped.append(f"assignee '{draft.assignee}' tidak ditemukan")
            else:
                target.assignee_id = assignee_agent.id
                applied.append(f"assignee → {assignee_agent.name}")

        if draft.sprint is not None:
            sprint = await _get_or_create_sprint(session, target.workspace_id, draft.sprint)
            target.sprint_id = sprint.id
            applied.append(f"sprint → {sprint.name}")

        if draft.duration is not None:
            target.duration_estimate = draft.duration
            applied.append(f"duration → {draft.duration}")

        if applied:
            await _write_system_comment(
                session,
                target.id,
                f"Diperbarui oleh {agent.name} lewat rutinitas: " + ", ".join(applied),
                ticket_key=target.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )
        for s in skipped:
            source_skip_notes.append(f"{draft.ticket_key}: {s}")

        updates_report.append({"ticket": draft.ticket_key, "applied": applied, "skipped": skipped})

    if source_skip_notes:
        run.error = "; ".join(source_skip_notes)

    # `tickets[]` without a parent -> backlog tickets (todo, NOT auto-scheduled).
    # Gated on `parsed.sprints` too, not just `parsed.tickets` — see the same note
    # in `_finish_chat_run`.
    tickets_report: list[dict] = []
    created_tickets: list[Ticket] = []
    if parsed.tickets or parsed.sprints:
        tickets_report, routine_epic_skip_notes, _, created_tickets = await create_tickets_and_sprints(
            session,
            workspace,
            sprints=parsed.sprints,
            tickets=parsed.tickets,
            run_id=run.id,
            actor_name=agent.name,
        )
        if routine_epic_skip_notes:
            run.error = "Beberapa epic tujuan diabaikan: " + "; ".join(routine_epic_skip_notes)

    # `artifacts:` — needs a ticket (attachment FK + storage folder), so routine runs
    # can't publish files; noted and skipped.
    artifacts_report: list[dict] = []
    if parsed.artifacts:
        run.error = "artifacts[] tidak didukung di run rutinitas (butuh tiket); diabaikan"

    # `artifact_updates:` — organize the Artifacts menu (PM-only, same as ticket runs).
    artifact_updates_report: list[dict] = []
    if parsed.artifact_updates:
        artifact_updates_report, artifact_update_skip_notes = await _apply_artifact_updates(
            session, workspace_id, parsed.artifact_updates
        )
        if artifact_update_skip_notes:
            run.error = "; ".join(artifact_update_skip_notes)

    # `memory:` — same as ticket runs.
    memory_report: list[str] = []
    if parsed.memories:
        memory_report = await _persist_memories(session, agent, None, parsed.memories)

    # No ticket/conversation to comment on here (no_ticket_mode) — same as the skip
    # notes above, surfaced via run.error (Run detail/Activity feed) rather than a
    # comment, so a silently dropped tickets:/sprints:/... isn't invisible.
    dropped_notes = parsed.dropped_notes()
    if dropped_notes:
        run.error = "Sebagian aksi di blok ```map diabaikan sistem: " + "; ".join(dropped_notes)

    run.report = {
        "summary": parsed.summary,
        "comments": comments_report,
        "tickets": tickets_report,
        "updates": updates_report,
        "artifacts": artifacts_report,
        "artifact_updates": artifact_updates_report,
        "memory": memory_report,
    }

    run.status = "done"
    if routine is not None:
        routine.status = "idle"
        routine.last_run_at = _now()
    agent.status = "idle"
    await session.commit()

    if session_factory is not None:
        for t in created_tickets:
            await _auto_schedule_assignee(session, session_factory, t)


async def _finish_chat_run(
    session: AsyncSession,
    run: Run,
    agent: Agent,
    terminal: AdapterEvent | None,
    accumulated_text: str,
    guardrail_cancel_reason: str | None = None,
    *,
    session_factory: async_sessionmaker | None,
) -> None:
    """Finish a chat run (no ticket): parse the ```map block, write the PM's reply
    (`summary`) into the conversation, and execute side-effect actions — `comments[]`
    (two-way follow-up onto tickets), `tickets[]` (backlog), `updates:`, `memory:`,
    `artifact_updates:`. Never a status transition, handoff, or block: chat runs have
    no ticket to block, so failures land as System messages in the conversation.

    Failure handling: retryable failures (missing/malformed ```map block, adapter
    failure) auto-retry with the same `max_auto_retries` budget as ticket runs, via
    `schedule_chat`; a non-retryable failure (e.g. state-machine rejection of an
    `updates:` status) writes a System message with the reason.
    """
    conversation = await session.get(Conversation, run.conversation_id)
    workspace_id = conversation.workspace_id if conversation else None

    if terminal is None:
        run.status = "failed"
        run.error = "adapter finished without a run_ended event"
        run.ended_at = _now()
        await _handle_failed_chat_run(
            session,
            run,
            agent,
            conversation,
            f"Run chat gagal: {run.error}",
            retryable=True,
            session_factory=session_factory,
        )
        return

    status = terminal.payload.get("status")
    run.session_id = terminal.payload.get("session_id") or run.session_id
    run.tokens_in = int(terminal.payload.get("tokens_in") or 0)
    run.tokens_out = int(terminal.payload.get("tokens_out") or 0)
    run.cost = float(terminal.payload.get("cost") or 0.0)
    run.ended_at = _now()

    if status == "cancelled":
        run.status = "cancelled"
        if guardrail_cancel_reason is not None:
            run.error = guardrail_cancel_reason
        if conversation is not None:
            await _write_system_message(
                session,
                conversation,
                f"Run chat dihentikan: {guardrail_cancel_reason or 'dibatalkan'}",
                run_id=run.id,
                workspace_id=workspace_id,
            )
        agent.status = "idle"
        await session.commit()
        return

    if status == "failed":
        run.status = "failed"
        run.error = terminal.payload.get("error") or "run failed"
        await _handle_failed_chat_run(
            session,
            run,
            agent,
            conversation,
            f"Run chat gagal: {run.error}",
            retryable=True,
            session_factory=session_factory,
        )
        return

    if conversation is None or workspace_id is None:
        run.status = "failed"
        run.error = "conversation not found for chat run"
        run.ended_at = _now()
        agent.status = "idle"
        await session.commit()
        return

    valid_names = {
        a.name
        for a in (
            await session.scalars(select(Agent).where(Agent.workspace_id == workspace_id))
        ).all()
    }
    workspace = await session.get(Workspace, workspace_id)
    role_map = await _role_map(session)
    actor_role = role_map.get(agent.role)
    parsed = parse_report(
        accumulated_text,
        agent.role,
        valid_names,
        actor_name=agent.name,
        ticket_approved=True,
        sprint_creator_roles=set((workspace.sprint_creator_roles if workspace else None) or ["pm"]),
        no_ticket_mode=True,
        valid_roles=set(role_map),
        may_declare_tickets=actor_role.may_declare_tickets if actor_role else False,
        may_manage_artifacts=actor_role.may_manage_artifacts if actor_role else False,
        is_pm=agent.role == "pm",
    )

    if not parsed.ok:
        run.status = "failed"
        run.error = parsed.reason
        run.report = None
        tail = accumulated_text[-_TAIL_CHARS:]
        await _handle_failed_chat_run(
            session,
            run,
            agent,
            conversation,
            f"Blok ```map hilang/rusak ({parsed.reason}). Output terakhir agent:\n\n{tail}",
            retryable=True,
            session_factory=session_factory,
        )
        return

    # `summary` is the PM's reply to the owner — written into the conversation.
    await _write_agent_message(
        session,
        conversation,
        agent,
        parsed.summary,
        run_id=run.id,
        workspace_id=workspace_id,
    )

    # `parsed.summary` above was written by the agent BEFORE this parse ran, so it
    # can't know any of tickets/sprints/updates/... got silently dropped (role not
    # allowed, plan not yet approved, or malformed shape). Post the correction right
    # under the agent's own message so the owner isn't left believing a claim that
    # didn't hold.
    dropped_notes = parsed.dropped_notes()
    if dropped_notes:
        await _write_system_message(
            session,
            conversation,
            "Sebagian aksi di blok ```map diabaikan sistem: " + "; ".join(dropped_notes),
            run_id=run.id,
            workspace_id=workspace_id,
        )

    # `comments:` — the two-way follow-up: comment on other existing tickets.
    comments_report: list[dict] = []
    comment_skip_notes: list[str] = []
    for draft in parsed.comments:
        target = await session.scalar(
            select(Ticket).where(
                Ticket.workspace_id == workspace_id, Ticket.key == draft.ticket_key
            )
        )
        if target is None:
            comment_skip_notes.append(f"{draft.ticket_key}: tiket tidak ditemukan di workspace ini")
            continue
        comment = Comment(
            ticket_id=target.id, author_agent_id=agent.id, is_system=False, body=draft.body
        )
        session.add(comment)
        await session.flush()
        mentioned = await _record_body_mentions(
            session, comment, draft.body, workspace_id=workspace_id, author_agent_id=agent.id
        )
        if session_factory is not None:
            for mentioned_agent in mentioned:
                if not mentioned_agent.enabled or mentioned_agent.status == "disabled":
                    continue
                try:
                    await schedule(
                        session, session_factory, ticket=target, agent=mentioned_agent, trigger="mention"
                    )
                except (GuardrailBlocked, RuntimeError):
                    continue
        await event_bus.publish(
            session,
            run_id=run.id,
            workspace_id=workspace_id,
            type="comment",
            payload={
                "ticket_id": target.id,
                "ticket_key": target.key,
                "is_system": False,
                "author": agent.name,
                "body_preview": _comment_preview(draft.body),
            },
        )
        comments_report.append({"ticket": draft.ticket_key, "applied": True})

    if comment_skip_notes:
        run.error = "; ".join(comment_skip_notes)

    # `updates:` — same semantics as routine runs (modify other tickets' fields).
    updates_report: list[dict] = []
    source_skip_notes: list[str] = []
    _VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
    for draft in parsed.updates:
        target = await session.scalar(
            select(Ticket).where(
                Ticket.workspace_id == workspace_id, Ticket.key == draft.ticket_key
            )
        )
        if target is None:
            source_skip_notes.append(f"{draft.ticket_key}: tiket tidak ditemukan di workspace ini")
            continue

        applied: list[str] = []
        skipped: list[str] = []

        if draft.status is not None:
            if draft.status not in STATUSES:
                skipped.append(f"status '{draft.status}' tidak dikenal")
            else:
                allowed, reason = can_transition(target.status, draft.status, agent.role)
                if not allowed:
                    skipped.append(f"status -> {draft.status} ditolak: {reason}")
                else:
                    target_old_status = target.status
                    target.status = draft.status
                    applied.append(f"status → {draft.status}")
                    await event_bus.publish(
                        session,
                        run_id=run.id,
                        workspace_id=workspace_id,
                        type="status_change",
                        payload={
                            "ticket_id": target.id,
                            "ticket_key": target.key,
                            "ticket_title": target.title,
                            "from": target_old_status,
                            "to": draft.status,
                            "actor": agent.name,
                        },
                    )

        if draft.priority is not None:
            if draft.priority not in _VALID_PRIORITIES:
                skipped.append(f"priority '{draft.priority}' tidak valid")
            else:
                target.priority = draft.priority
                applied.append(f"priority → {draft.priority}")

        if draft.assignee is not None:
            assignee_agent = await session.scalar(
                select(Agent).where(
                    Agent.workspace_id == target.workspace_id, Agent.name == draft.assignee
                )
            )
            if assignee_agent is None:
                skipped.append(f"assignee '{draft.assignee}' tidak ditemukan")
            else:
                target.assignee_id = assignee_agent.id
                applied.append(f"assignee → {assignee_agent.name}")

        if draft.sprint is not None:
            sprint = await _get_or_create_sprint(session, target.workspace_id, draft.sprint)
            target.sprint_id = sprint.id
            applied.append(f"sprint → {sprint.name}")

        if draft.duration is not None:
            target.duration_estimate = draft.duration
            applied.append(f"duration → {draft.duration}")

        if applied:
            await _write_system_comment(
                session,
                target.id,
                f"Diperbarui oleh {agent.name} lewat chat: " + ", ".join(applied),
                ticket_key=target.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )
        for s in skipped:
            source_skip_notes.append(f"{draft.ticket_key}: {s}")

        updates_report.append({"ticket": draft.ticket_key, "applied": applied, "skipped": skipped})

    if source_skip_notes:
        run.error = "; ".join(source_skip_notes)

    # `tickets[]` without a parent -> backlog tickets (todo). Any created ticket
    # that lands with an assignee (and isn't itself `backlog` status) gets its run
    # auto-scheduled after this function's own commit, below.
    # Gated on `parsed.sprints` too, not just `parsed.tickets` — a PM
    # activating/completing/declaring a sprint with no new tickets in the same
    # report is a valid, common shape and must not be silently skipped.
    tickets_report: list[dict] = []
    created_tickets: list[Ticket] = []
    if parsed.tickets or parsed.sprints:
        has_active_sprint = (
            await session.scalar(
                select(Sprint.id)
                .where(Sprint.workspace_id == workspace_id, Sprint.status == "active")
                .limit(1)
            )
        ) is not None

        if has_active_sprint:
            tickets_report, chat_epic_skip_notes, _, created_tickets = await create_tickets_and_sprints(
                session,
                workspace,
                sprints=parsed.sprints,
                tickets=parsed.tickets,
                run_id=run.id,
                actor_name=agent.name,
            )
            if chat_epic_skip_notes:
                run.error = "Beberapa epic tujuan diabaikan: " + "; ".join(chat_epic_skip_notes)
        else:
            # No active sprint: hold everything as a proposal (owner request —
            # PM must propose creating+activating a new sprint and wait for
            # explicit approval, not just silently spin one up). Nothing is
            # created until `conversations.create_message` sees an APPROVAL_RE
            # match against this conversation's `pending_proposal`.
            conversation.pending_proposal = json.dumps(
                {
                    "sprints": [asdict(d) for d in parsed.sprints],
                    "tickets": [asdict(d) for d in parsed.tickets],
                }
            )
            proposal_lines = [
                f"- Sprint baru: **{d.name}**" + (f" — {d.goal}" if d.goal else "")
                for d in parsed.sprints
            ] + [
                f"- Tiket: **{d.title}**" + (f" (assignee: {d.assignee})" if d.assignee else "")
                for d in parsed.tickets
            ]
            await _write_agent_message(
                session,
                conversation,
                agent,
                "Tidak ada sprint aktif saat ini. Usulan dari "
                f"{agent.name}:\n" + "\n".join(proposal_lines) + "\n\n"
                'Balas "oke"/"lanjut" untuk approve — sprint dan tiket di atas baru '
                "dibuat dan sprintnya diaktifkan setelah kamu setuju.",
                run_id=run.id,
                workspace_id=workspace_id,
            )

    # `artifacts:` — needs a ticket (attachment FK + storage folder), so chat runs
    # can't publish files; noted and skipped.
    artifacts_report: list[dict] = []
    if parsed.artifacts:
        run.error = "artifacts[] tidak didukung di run chat (butuh tiket); diabaikan"

    # `artifact_updates:` — organize the Artifacts menu (PM-only, same as ticket runs).
    artifact_updates_report: list[dict] = []
    if parsed.artifact_updates:
        artifact_updates_report, artifact_update_skip_notes = await _apply_artifact_updates(
            session, workspace_id, parsed.artifact_updates
        )
        if artifact_update_skip_notes:
            run.error = "; ".join(artifact_update_skip_notes)

    # `memory:` — same as ticket runs.
    memory_report: list[str] = []
    if parsed.memories:
        memory_report = await _persist_memories(session, agent, None, parsed.memories)

    run.report = {
        "summary": parsed.summary,
        "comments": comments_report,
        "tickets": tickets_report,
        "updates": updates_report,
        "artifacts": artifacts_report,
        "artifact_updates": artifact_updates_report,
        "memory": memory_report,
    }

    run.status = "done"
    agent.status = "idle"
    await session.commit()

    if session_factory is not None:
        for t in created_tickets:
            await _auto_schedule_assignee(session, session_factory, t)


async def _handle_failed_chat_run(
    session: AsyncSession,
    run: Run,
    agent: Agent,
    conversation: Conversation | None,
    error_body: str,
    *,
    retryable: bool,
    session_factory: async_sessionmaker | None,
) -> None:
    """Common failure handling for a chat run: auto-retry (same budget as ticket
    runs) or a System message on the conversation with the error + output tail.

    A chat run has no ticket to block, so "giving up" means: failed run status +
    System message telling the owner what happened.
    """
    agent.status = "idle"
    if conversation is None:
        await session.commit()
        return
    workspace = await session.get(Workspace, conversation.workspace_id)
    guardrails = (workspace.guardrails if workspace else None) or {}
    max_retries = int(guardrail_limit(guardrails, "max_auto_retries"))

    if retryable and session_factory is not None and max_retries > 0:
        attempt = await _retry_attempt_count(session, run, agent.id)
        if attempt <= max_retries:
            await _write_system_message(
                session,
                conversation,
                f"{error_body}\n\nAuto-retry {attempt}/{max_retries} dijalankan — "
                f"agent diberitahu kegagalan ini di prompt-nya.",
                run_id=run.id,
                workspace_id=conversation.workspace_id,
            )
            try:
                await schedule_chat(
                    session,
                    session_factory,
                    conversation,
                    agent,
                    parent_run_id=run.id,
                )
            except GuardrailBlocked:
                run.error = str(run.error or "")
                # schedule_chat already wrote its own System message naming the guardrail.
            except RuntimeError:
                pass
            await session.commit()
            return

    if retryable and max_retries > 0:
        error_body = (
            f"{error_body}\n\nAuto-retry habis (max_auto_retries={max_retries}x) "
            "pada chat ini — PM tidak membalas."
        )
    await _write_system_message(
        session,
        conversation,
        error_body,
        run_id=run.id,
        workspace_id=conversation.workspace_id,
    )
    await session.commit()


async def _cleanup_worktree_abandoned(
    repo_path: str, worktree_path: str, ticket_key: str
) -> None:
    """Remove an abandoned worktree without merging (agent was cancelled/failed).

    Safe to call even if the worktree doesn't exist. The branch is NOT deleted so
    nothing is lost.
    """
    try:
        git_module.cleanup_abandoned_worktree(repo_path, ticket_key)
    except git_module.GitError:
        pass  # already gone — nothing to clean up


async def _finish_run(
    session: AsyncSession,
    run: Run,
    ticket: Ticket | None,
    agent: Agent,
    terminal: AdapterEvent | None,
    accumulated_text: str,
    guardrail_cancel_reason: str | None = None,
    worktree_path: str = "",
    merge_into: str = "main",
    *,
    session_factory: async_sessionmaker | None = None,
) -> None:
    if ticket is None:
        # non-ticket run (chat / routine) — nothing to do with worktrees
        if run.conversation_id is not None:
            await _finish_chat_run(
                session,
                run,
                agent,
                terminal,
                accumulated_text,
                guardrail_cancel_reason,
                session_factory=session_factory,
            )
            return
        await _finish_routine_run(
            session,
            run,
            agent,
            terminal,
            accumulated_text,
            guardrail_cancel_reason,
            session_factory=session_factory,
        )
        return

    # For ticket runs, fetch workspace early so it's available in all early-return paths
    workspace = await session.get(Workspace, ticket.workspace_id)
    workspace_id = ticket.workspace_id

    if terminal is None:
        # Adapter misbehaved: no run_ended event at all. Treat like a failure.
        run.status = "failed"
        run.error = "adapter finished without a run_ended event"
        run.ended_at = _now()
        await _handle_failed_run(
            session,
            run,
            ticket,
            agent,
            f"Run gagal: {run.error}",
            retryable=True,
            session_factory=session_factory,
        )
        agent.status = "idle"
        await session.commit()
        return

    status = terminal.payload.get("status")
    run.session_id = terminal.payload.get("session_id") or run.session_id
    run.tokens_in = int(terminal.payload.get("tokens_in") or 0)
    run.tokens_out = int(terminal.payload.get("tokens_out") or 0)
    run.cost = float(terminal.payload.get("cost") or 0.0)
    run.ended_at = _now()
    ticket.cost_used = (ticket.cost_used or 0.0) + run.cost

    if status == "cancelled":
        run.status = "cancelled"
        if worktree_path and worktree_path != workspace.repo_path:
            await _cleanup_worktree_abandoned(workspace.repo_path, worktree_path, ticket.key)
        if guardrail_cancel_reason is not None:
            await _write_system_comment(
                session,
                ticket.id,
                guardrail_cancel_reason,
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )
            # Same owner-facing mirror as _block_ticket: a guardrail trip on a child
            # must reach the owner's epic chat, not just the child's own comments.
            if ticket.parent_id is not None:
                await _notify_owner_chat(
                    session,
                    ticket,
                    agent,
                    f"Run {agent.name} pada {ticket.key} dihentikan: {_excerpt(guardrail_cancel_reason)}",
                    run_id=run.id,
                    workspace_id=workspace_id,
                )
        agent.status = "idle"
        await session.commit()
        return

    if status == "failed":
        run.status = "failed"
        run.error = terminal.payload.get("error") or "run failed"
        if worktree_path and worktree_path != workspace.repo_path:
            await _cleanup_worktree_abandoned(workspace.repo_path, worktree_path, ticket.key)
        await _handle_failed_run(
            session,
            run,
            ticket,
            agent,
            f"Run gagal: {run.error}",
            retryable=True,
            session_factory=session_factory,
        )
        agent.status = "idle"
        await session.commit()
        return

    # status == "done" -> parse the accumulated assistant text for the ```map block.
    valid_names = {
        a.name
        for a in (
            await session.scalars(
                select(Agent).where(Agent.workspace_id == ticket.workspace_id)
            )
        ).all()
    }
    role_map = await _role_map(session)
    actor_role = role_map.get(agent.role)
    parsed = parse_report(
        accumulated_text,
        agent.role,
        valid_names,
        actor_name=agent.name,
        # Explorative gate applies to owner-chat PM runs only (trigger="mention"):
        # a manual board run is implicitly approved by the owner pressing Run.
        ticket_approved=run.trigger != "mention" or ticket.approved_at is not None,
        # Per-workspace setting (Settings page): which roles may declare `sprints:`.
        sprint_creator_roles=set((workspace.sprint_creator_roles if workspace else None) or ["pm"]),
        valid_roles=set(role_map),
        may_declare_tickets=actor_role.may_declare_tickets if actor_role else False,
        may_manage_artifacts=actor_role.may_manage_artifacts if actor_role else False,
        is_pm=agent.role == "pm",
    )

    if not parsed.ok:
        run.status = "failed"
        run.error = parsed.reason
        run.report = None
        if worktree_path and worktree_path != workspace.repo_path:
            await _cleanup_worktree_abandoned(workspace.repo_path, worktree_path, ticket.key)
        tail = accumulated_text[-_TAIL_CHARS:]
        await _handle_failed_run(
            session,
            run,
            ticket,
            agent,
            f"Blok ```map hilang/rusak ({parsed.reason}). Output terakhir agent:\n\n{tail}",
            retryable=True,
            session_factory=session_factory,
        )
        agent.status = "idle"
        await session.commit()
        return

    # Same-status re-declaration isn't a real transition, so it doesn't go through
    # can_transition's from/to matrix at all (test_state_machine_matrix.py's exhaustive
    # table has no from==to entry — every pair is illegal there by construction).
    # docs/03-agent-design.md §4/§8: PM breaking an epic down reports "status:
    # in_progress" on a ticket the run-start auto-transition already moved
    # todo -> in_progress ("... 4. status: in_progress. Berhenti — sub-tiket akan
    # dikerjakan sendiri"). parse_report already confirmed the role may declare this
    # status at all, so nothing more to check here.
    if parsed.status != ticket.status:
        allowed, reason = can_transition(ticket.status, parsed.status, agent.role)
        if not allowed:
            run.status = "failed"
            run.error = reason
            run.report = None
            await _block_ticket(
                session,
                ticket,
                agent,
                f"Transisi status dari ```map ditolak state machine: {reason}",
                run_id=run.id,
                workspace_id=workspace_id,
            )
            agent.status = "idle"
            await session.commit()
            return
        if parsed.status == "blocked":
            ticket.blocked_reason = parsed.summary

    # run.status is set to "done" only right before the final commit below (not here) —
    # event_bus.publish() commits the session immediately (events need durability for
    # live subscribers), and several publishes happen between here and the end of this
    # function (comment/status_change for the summary, updates:, tickets[], handoff).
    # Setting it this early would let a client polling GET /runs/{id} observe "done"
    # before the handoff/block-ticket side effects below are actually committed —
    # exactly the race test_valid_map_block_transitions_ticket_and_records_mentions
    # caught (ticket read back as "review" instead of the handoff engine's "blocked").
    old_status = ticket.status
    if parsed.status != old_status:
        ticket.status = parsed.status
    if old_status == "blocked" and parsed.status != "blocked":
        ticket.blocked_reason = None

    # `updates:` (v1, narrow by design): let this report modify OTHER existing tickets'
    # status/priority/assignee. Never schedules a run on the target — changing another
    # ticket's fields via updates: is deliberately not forward momentum for that ticket;
    # the target's own next run (if any) is triggered the normal way. Computed here,
    # before run.report is built and flushed below, so the "updates" summary is
    # complete on first write — JSON columns aren't mutation-tracked, so mutating a
    # nested list in place after a flush wouldn't be picked up by a later commit.
    _VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
    updates_report: list[dict] = []
    source_skip_notes: list[str] = []
    for draft in parsed.updates:
        target = await session.scalar(
            select(Ticket).where(
                Ticket.workspace_id == ticket.workspace_id, Ticket.key == draft.ticket_key
            )
        )
        if target is None:
            source_skip_notes.append(f"{draft.ticket_key}: tiket tidak ditemukan di workspace ini")
            continue

        applied: list[str] = []
        skipped: list[str] = []

        if draft.status is not None:
            if draft.status not in STATUSES:
                skipped.append(f"status '{draft.status}' tidak dikenal")
            else:
                allowed, reason = can_transition(target.status, draft.status, agent.role)
                if not allowed:
                    skipped.append(f"status -> {draft.status} ditolak: {reason}")
                else:
                    target_old_status = target.status
                    target.status = draft.status
                    applied.append(f"status → {draft.status}")
                    await event_bus.publish(
                        session,
                        run_id=run.id,
                        workspace_id=workspace_id,
                        type="status_change",
                        payload={
                            "ticket_id": target.id,
                            "ticket_key": target.key,
                            "ticket_title": target.title,
                            "from": target_old_status,
                            "to": draft.status,
                            "actor": agent.name,
                        },
                    )

        if draft.priority is not None:
            if draft.priority not in _VALID_PRIORITIES:
                skipped.append(f"priority '{draft.priority}' tidak valid")
            else:
                target.priority = draft.priority
                applied.append(f"priority → {draft.priority}")

        if draft.assignee is not None:
            assignee_agent = await session.scalar(
                select(Agent).where(
                    Agent.workspace_id == target.workspace_id, Agent.name == draft.assignee
                )
            )
            if assignee_agent is None:
                skipped.append(f"assignee '{draft.assignee}' tidak ditemukan")
            else:
                target.assignee_id = assignee_agent.id
                applied.append(f"assignee → {assignee_agent.name}")

        if draft.sprint is not None:
            sprint = await _get_or_create_sprint(session, target.workspace_id, draft.sprint)
            target.sprint_id = sprint.id
            applied.append(f"sprint → {sprint.name}")

        if draft.duration is not None:
            target.duration_estimate = draft.duration
            applied.append(f"duration → {draft.duration}")

        if applied:
            await _write_system_comment(
                session,
                target.id,
                f"Diperbarui oleh {agent.name} lewat laporan {ticket.key}: " + ", ".join(applied),
                ticket_key=target.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )
        for s in skipped:
            source_skip_notes.append(f"{draft.ticket_key}: {s}")

        updates_report.append({"ticket": draft.ticket_key, "applied": applied, "skipped": skipped})

    if source_skip_notes:
        await _write_system_comment(
            session,
            ticket.id,
            "Beberapa updates diabaikan: " + "; ".join(source_skip_notes),
            ticket_key=ticket.key,
            run_id=run.id,
            workspace_id=workspace_id,
        )

    artifacts_report: list[dict] = []
    if parsed.artifacts:
        workspace_for_artifacts = await session.get(Workspace, workspace_id)
        artifacts_report, artifact_skip_notes = await _publish_artifacts(
            session, workspace_for_artifacts, ticket, parsed.artifacts
        )
        if artifacts_report:
            names = ", ".join(f"{a['path']} → {a['group']}" for a in artifacts_report)
            await _write_system_comment(
                session,
                ticket.id,
                f"Artifact dipublikasikan: {names}",
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )
        if artifact_skip_notes:
            await _write_system_comment(
                session,
                ticket.id,
                "Beberapa artifact diabaikan: " + "; ".join(artifact_skip_notes),
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )

    artifact_updates_report: list[dict] = []
    if parsed.artifact_updates:
        artifact_updates_report, artifact_update_skip_notes = await _apply_artifact_updates(
            session, workspace_id, parsed.artifact_updates
        )
        if artifact_updates_report:
            names = "; ".join(
                f"{u['op']} {u.get('group') or u.get('from') or u.get('file')}"
                + (f" → {u.get('to') or u.get('into')}" if u.get("to") or u.get("into") else "")
                for u in artifact_updates_report
            )
            await _write_system_comment(
                session,
                ticket.id,
                f"Artifact diorganisir oleh {agent.name}: {names}",
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )
        if artifact_update_skip_notes:
            await _write_system_comment(
                session,
                ticket.id,
                "Beberapa artifact_updates diabaikan: " + "; ".join(artifact_update_skip_notes),
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )

    memory_report: list[str] = []
    if parsed.memories:
        memory_report = await _persist_memories(session, agent, ticket, parsed.memories)
        if memory_report:
            notes_str = "; ".join(memory_report)
            await _write_system_comment(
                session,
                ticket.id,
                f"Memory disimpan untuk {agent.name}: {notes_str}",
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )

    run.report = {
        "status": parsed.status,
        "summary": parsed.summary,
        "mention": parsed.valid_mentions,
        "unknown_mentions": parsed.unknown_mentions,
        "tickets": [
            {
                "title": t.title,
                "description": t.description,
                "assignee": t.assignee,
                "priority": t.priority,
                "category": t.category,
                "sprint": t.sprint,
                "duration": t.duration,
            }
            for t in parsed.tickets
        ],
        "sprints": [
            {
                "name": s.name,
                "goal": s.goal,
                "duration": s.duration,
                "start_date": s.start_date,
                "end_date": s.end_date,
            }
            for s in parsed.sprints
        ],
        "updates": updates_report,
        "artifacts": artifacts_report,
        "artifact_updates": artifact_updates_report,
        "memory": memory_report,
    }

    is_duplicate_auto_nudge = False
    if run.trigger == "auto":
        # Compare against the last agent-authored comment specifically — system
        # comments (status changes, guardrail notes, ...) aren't what the agent is
        # repeating itself against.
        prev_comment = await session.scalar(
            select(Comment)
            .where(Comment.ticket_id == ticket.id, Comment.is_system.is_(False))
            .order_by(Comment.created_at.desc())
            .limit(1)
        )
        if prev_comment is not None:
            ratio = difflib.SequenceMatcher(None, prev_comment.body, parsed.summary).ratio()
            is_duplicate_auto_nudge = ratio >= _AUTO_CHECK_DUP_RATIO

    comment: Comment | None = None
    auto_check_state = await session.get(TicketAutoCheck, ticket.id)
    if is_duplicate_auto_nudge:
        # Nothing new to say — skip the Comment (no spam). The full summary is still
        # in run.report above, inspectable via the Run detail / Activity feed.
        if auto_check_state is None:
            auto_check_state = TicketAutoCheck(ticket_id=ticket.id, skip_count=0, last_nudge_at=_now())
            session.add(auto_check_state)
        auto_check_state.skip_count += 1
        auto_check_state.last_nudge_at = _now()
    else:
        # Real content (or a non-auto trigger) — post normally and reset backoff, since
        # real progress means the next auto-check should go back to the tight cadence.
        if auto_check_state is not None:
            await session.delete(auto_check_state)

        comment = Comment(
            ticket_id=ticket.id, author_agent_id=agent.id, is_system=False, body=parsed.summary
        )
        session.add(comment)
        await session.flush()
        await _record_body_mentions(
            session, comment, parsed.summary, workspace_id=workspace_id, author_agent_id=agent.id
        )
        await event_bus.publish(
            session,
            run_id=run.id,
            workspace_id=workspace_id,
            type="comment",
            payload={
                "ticket_id": ticket.id,
                "ticket_key": ticket.key,
                "is_system": False,
                "author": agent.name,
                "body_preview": _comment_preview(parsed.summary),
            },
        )

    if old_status != parsed.status:
        await event_bus.publish(
            session,
            run_id=run.id,
            workspace_id=workspace_id,
            type="status_change",
            payload={
                "ticket_id": ticket.id,
                "ticket_key": ticket.key,
                "ticket_title": ticket.title,
                "from": old_status,
                "to": parsed.status,
                "actor": agent.name,
            },
        )
        await _write_system_comment(
            session,
            ticket.id,
            f"Status changed from {old_status} to {parsed.status}",
            ticket_key=ticket.key,
            run_id=run.id,
            workspace_id=workspace_id,
        )

    if comment is not None and parsed.valid_mentions:
        mentioned = (
            await session.scalars(
                select(Agent).where(
                    Agent.workspace_id == ticket.workspace_id, Agent.name.in_(parsed.valid_mentions)
                )
            )
        ).all()
        for mentioned_agent in mentioned:
            session.add(CommentMention(comment_id=comment.id, agent_id=mentioned_agent.id))

    to_auto_schedule: list[tuple[Ticket, Agent]] = []
    epic_skip_notes: list[str] = []
    created_child_keys: list[str] = []

    # `sprints:` must not be gated on `tickets:` also being present — a PM
    # activating/completing a sprint (or just declaring one) with no new
    # tickets in the same report is a completely valid, common report shape.
    for sprint_draft in parsed.sprints:
        sprint = await _get_or_create_sprint(
            session,
            ticket.workspace_id,
            sprint_draft.name,
            goal=sprint_draft.goal,
            duration=sprint_draft.duration,
            start_date=sprint_draft.start_date,
            end_date=sprint_draft.end_date,
        )
        await _apply_sprint_status(session, sprint, sprint_draft.status)

    if parsed.tickets:
        from app.api.tickets import _next_key  # reuse the same atomic-counter key logic

        for draft in parsed.tickets:
            assignee_id = None
            assignee_agent = None
            if draft.assignee:
                assignee_agent = await session.scalar(
                    select(Agent).where(
                        Agent.workspace_id == ticket.workspace_id, Agent.name == draft.assignee
                    )
                )
                assignee_id = assignee_agent.id if assignee_agent else None
            sprint_id = None
            if draft.sprint:
                sprint = await _get_or_create_sprint(session, ticket.workspace_id, draft.sprint)
                sprint_id = sprint.id

            # Epic resolution (docs/03-agent-design.md §3): explicit `epic:` wins if
            # valid; otherwise attach to THIS ticket's own epic if it has one (keeps
            # QA/Pentester bug reports flat siblings under the same epic instead of
            # silently nesting 2 levels deep); otherwise this ticket itself becomes
            # the epic (unchanged default when there's no parent to inherit).
            epic_target, epic_skip_note = await _resolve_epic_target(
                session, ticket.workspace_id, draft.epic
            )
            if epic_skip_note:
                epic_skip_notes.append(epic_skip_note)
            parent_id = (
                epic_target.id
                if epic_target is not None
                else (ticket.parent_id or ticket.id)
            )

            child = Ticket(
                workspace_id=ticket.workspace_id,
                key=await _next_key(session, workspace),
                title=draft.title,
                description=draft.description,
                status="todo",
                priority=draft.priority,
                assignee_id=assignee_id,
                parent_id=parent_id,
                category=draft.category,
                sprint_id=sprint_id,
                duration_estimate=draft.duration,
            )
            session.add(child)
            await session.flush()  # populate child.id before publishing
            created_child_keys.append(child.key)
            await event_bus.publish(
                session,
                run_id=run.id,
                workspace_id=workspace_id,
                type="status_change",
                payload={
                    "ticket_id": child.id,
                    "ticket_key": child.key,
                    "ticket_title": child.title,
                    "from": None,
                    "to": "todo",
                    "actor": agent.name,
                },
            )
            # MAP-030 AC: "tickets[] dari PM/QA/Pentester langsung terjadwal untuk
            # assignee-nya". No assignee (draft.assignee empty/unresolvable) or a
            # disabled assignee -> leave it at todo, unscheduled (owner can run it
            # manually later) - same "nonaktif" bar _handoff already applies.
            if (
                assignee_agent is not None
                and assignee_agent.enabled
                and assignee_agent.status != "disabled"
            ):
                to_auto_schedule.append((child, assignee_agent))

    if epic_skip_notes:
        await _write_system_comment(
            session,
            ticket.id,
            "Beberapa epic tujuan diabaikan: " + "; ".join(epic_skip_notes),
            ticket_key=ticket.key,
            run_id=run.id,
            workspace_id=workspace_id,
        )

    # `parsed.summary` above was written by the agent BEFORE this parse ran, so it
    # can't know any of tickets/sprints/updates/... got silently dropped (role not
    # allowed, plan not yet approved, or malformed shape). Without this, the agent's
    # own claimed outcome is the only account posted — surface the real one too, on
    # the ticket and mirrored to the owner's epic chat if this is a child ticket.
    dropped_notes = parsed.dropped_notes()
    if dropped_notes:
        dropped_body = "Sebagian aksi di blok ```map diabaikan sistem: " + "; ".join(dropped_notes)
        await _write_system_comment(
            session,
            ticket.id,
            dropped_body,
            ticket_key=ticket.key,
            run_id=run.id,
            workspace_id=workspace_id,
        )
        await _notify_owner_chat(
            session, ticket, agent, dropped_body, run_id=run.id, workspace_id=workspace_id
        )

    agent.status = "idle"

    if to_auto_schedule and session_factory is not None:
        await session.flush()  # populate child.id before schedule() reads it
        for child, assignee_agent in to_auto_schedule:
            try:
                await schedule(
                    session,
                    session_factory,
                    ticket=child,
                    agent=assignee_agent,
                    trigger="auto",
                    exclude_run_id=run.id,
                )
            except (GuardrailBlocked, RuntimeError):
                # schedule() already left the child ticket in a sane state (blocked
                # with a system comment, or untouched if the workspace got paused);
                # nothing more to do for this one child.
                pass

    # Owner-facing "PM balas" in the epic chat: whenever a report filed tickets[],
    # mirror a short system message onto the owner's chat listing the new children
    # (skip when the report itself was on a top-level ticket — that ticket IS the
    # owner chat, and its own summary comment already landed there above). Without
    # this, the PM "answers" the owner's chat by breaking down tickets but says
    # nothing in the conversation, which reads as the PM going silent.
    if parsed.tickets and ticket.parent_id is not None:
        keys = ", ".join(f"`{k}`" for k in created_child_keys) or " (no sub-tickets)"
        await _notify_owner_chat(
            session,
            ticket,
            agent,
            f"{agent.name} memecah {ticket.key} menjadi sub-tiket: {keys}",
            run_id=run.id,
            workspace_id=workspace_id,
        )

    if session_factory is not None:
        await _handoff(session, session_factory, run, ticket, agent, parsed)
        await _maybe_wake_parent_pm(session, session_factory, ticket, run_id=run.id)

    # Worktree merge: after a successful run on a ticket, merge the feature branch
    # back into the epic branch (or main) and clean up the worktree.
    # Only when the run succeeded and the agent declared merge_branch=true.
    if (
        worktree_path
        and worktree_path != workspace.repo_path
        and parsed.merge_branch
    ):
        try:
            git_module.merge_and_cleanup_worktree(
                workspace.repo_path,
                worktree_path,
                ticket.key,
                merge_into=merge_into,
            )
        except git_module.GitError as ge:
            await _write_system_comment(
                session,
                ticket.id,
                f"Worktree merge gagal setelah run selesai: {ge}. "
                "Worktree dan branch tetap tersedia untuk manual merge.",
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=workspace_id,
            )
    elif worktree_path and worktree_path != workspace.repo_path:
        pass

    run.status = "done"
    await session.commit()


async def _maybe_wake_parent_pm(
    session: AsyncSession,
    session_factory: async_sessionmaker,
    ticket: Ticket,
    *,
    run_id: str | None = None,
) -> None:
    """MAP-030: "PM menutup epic saat semua anak done" (docs/03-agent-design.md §4/§8).

    Nothing else re-invokes PM once the children it filed via `tickets[]` finish —
    each child's own handoff chain ends at its terminal status, and PM only reports
    on the epic when it's actually asked to run. So: whenever a ticket with a parent
    reaches a terminal status (done/blocked) and every sibling has also reached a
    terminal status, schedule one more PM run on the parent so it can declare the
    epic `done` (all children done) or `blocked` (docs §4: "Kalau ada sub-tiket yang
    blocked: status: blocked"). Runs once per completing sibling by construction: the
    "all siblings terminal" check only passes on the report that completes the set.
    """
    if ticket.parent_id is None or ticket.status not in _FINAL_STATUSES:
        return
    parent = await session.get(Ticket, ticket.parent_id)
    if parent is None or parent.status in _FINAL_STATUSES:
        return
    siblings = (await session.scalars(select(Ticket).where(Ticket.parent_id == parent.id))).all()
    if not siblings or any(s.status not in _FINAL_STATUSES for s in siblings):
        return

    pm = (
        await session.scalars(
            select(Agent).where(
                Agent.workspace_id == parent.workspace_id,
                Agent.role == "pm",
                Agent.enabled.is_(True),
                Agent.status != "disabled",
            )
        )
    ).first()
    if pm is None:
        await _write_system_comment(
            session,
            parent.id,
            "Semua sub-tiket selesai, tapi tidak ada agent PM aktif untuk menutup epic ini.",
            ticket_key=parent.key,
            run_id=run_id,
            workspace_id=parent.workspace_id,
        )
        return
    # Bounded by the same max_handoff_depth guardrail as a regular handoff, on the
    # PARENT (not the child that just finished): a misbehaving PM that keeps declaring
    # more tickets[] instead of closing the epic (docs say it shouldn't, but nothing
    # here should rely on that) would otherwise re-trigger this wake-up forever, since
    # nothing else bounds it. schedule() re-checks the guardrail with this incremented
    # value before creating the Run row.
    parent.handoff_depth = (parent.handoff_depth or 0) + 1
    try:
        await schedule(
            session, session_factory, ticket=parent, agent=pm, trigger="auto", exclude_run_id=run_id
        )
    except (GuardrailBlocked, RuntimeError):
        pass  # schedule() already left the parent in a sane state, or workspace paused


async def _resolve_role_agent(
    session: AsyncSession, ticket: Ticket, role: str
) -> Agent | None:
    """Role-not-name fallback (docs/03-agent-design.md §6): pick the `idle` agent with
    that role and the fewest existing runs on this ticket; if none are idle, fall back
    to the busiest-tolerant choice among all enabled agents with that role (schedule()
    queues it automatically since the agent is busy). Ties broken by creation order.
    """
    candidates = (
        await session.scalars(
            select(Agent).where(
                Agent.workspace_id == ticket.workspace_id,
                Agent.role == role,
                Agent.enabled.is_(True),
                Agent.status != "disabled",
            )
        )
    ).all()
    if not candidates:
        return None

    counts_rows = (
        await session.execute(
            select(Run.agent_id, func.count())
            .where(Run.ticket_id == ticket.id, Run.agent_id.in_([c.id for c in candidates]))
            .group_by(Run.agent_id)
        )
    ).all()
    run_counts = dict(counts_rows)

    idle = [a for a in candidates if a.status == "idle"]
    pool = idle if idle else candidates
    pool = sorted(pool, key=lambda a: (run_counts.get(a.id, 0), a.created_at, a.id))
    return pool[0]


async def _handoff(
    session: AsyncSession,
    session_factory: async_sessionmaker,
    run: Run,
    ticket: Ticket,
    agent: Agent,
    parsed,
) -> None:
    """MAP-029: turn a successfully-parsed report's mentions into follow-up runs.

    Resolution order per mention name:
    1. Already-valid agent name (`parsed.valid_mentions`) -> that agent, unless disabled.
    2. Otherwise (`parsed.unknown_mentions`) -> if the name is actually a role string,
       resolve via `_resolve_role_agent`; if it's neither a name nor a role, it's truly
       unknown and only gets logged.

    handoff_depth: incremented ONCE per report (not once per mention). A report's
    `mention` list is fan-out within a single handoff *step*; `handoff_depth` tracks
    chain *depth* (docs: "Tiap handoff menaikkan ticket.handoff_depth" / "Rantai mention
    menaikkan handoff_depth ... berhenti di max_handoff_depth" — a "rantai" is a sequence
    of steps, not a sequence of individual mentions). Incrementing per-mention would let
    a single wide report (e.g. QA filing bugs to 3 engineers) exhaust max_handoff_depth
    in one step, which isn't what "chain" depth is meant to bound; incrementing once per
    step still fully bounds runaway A -> B -> A -> B ... chains, which is the actual
    runaway-loop risk this guardrail exists for.
    """
    role_map = await _role_map(session)
    targets: list[Agent] = []
    notes: list[str] = []
    seen_ids: set[str] = set()

    if parsed.valid_mentions:
        mentioned = (
            await session.scalars(
                select(Agent).where(
                    Agent.workspace_id == ticket.workspace_id,
                    Agent.name.in_(parsed.valid_mentions),
                )
            )
        ).all()
        by_name = {a.name: a for a in mentioned}
        for name in parsed.valid_mentions:
            a = by_name.get(name)
            if a is None:
                continue
            if not a.enabled or a.status == "disabled":
                notes.append(f"agent {name} nonaktif")
                continue
            if a.id not in seen_ids:
                seen_ids.add(a.id)
                targets.append(a)

    for name in parsed.unknown_mentions:
        if name not in role_map:
            notes.append(f"mention '{name}' tidak dikenal")
            continue
        resolved = await _resolve_role_agent(session, ticket, name)
        if resolved is None:
            notes.append(f"tidak ada agent dengan role '{name}' di workspace")
            continue
        if not resolved.enabled or resolved.status == "disabled":
            notes.append(f"agent {resolved.name} nonaktif")
            continue
        if resolved.id == run.agent_id:
            # Self-mention via role (e.g. the only lead mentioning "lead"): same
            # rule as the name-mention self-drop in report.py — a report that
            # handoffs to itself would loop forever.
            notes.append(f"mention role '{name}' menunjuk ke diri sendiri ({resolved.name}); diabaikan")
            continue
        if resolved.id not in seen_ids:
            seen_ids.add(resolved.id)
            targets.append(resolved)

    if targets:
        if ticket.status in _COMPLETION_STATUSES:
            await _write_system_comment(
                session,
                ticket.id,
                "Tiket sudah " + ticket.status + " — mention ("
                + ", ".join(a.name for a in targets)
                + ") dicatat sebagai info, tidak memicu run baru.",
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=ticket.workspace_id,
            )
            return
        ticket.handoff_depth = (ticket.handoff_depth or 0) + 1
        # A handoff moves the ticket to the mentioned agent: the board/detail/timeline
        # all render assignee from ticket.assignee_id, so it must follow the handoff
        # target. Fan-out (several mentions in one report) still schedules every
        # target, but the ticket can only have one assignee — the FIRST valid target
        # wins (docs/03-agent-design.md §6).
        if targets[0].id != ticket.assignee_id:
            ticket.assignee_id = targets[0].id
        for target in targets:
            try:
                await schedule(
                    session,
                    session_factory,
                    ticket=ticket,
                    agent=target,
                    trigger="handoff",
                    parent_run_id=run.id,
                    exclude_run_id=run.id,
                )
            except GuardrailBlocked:
                # schedule() already transitioned the ticket to blocked with a system
                # comment naming the guardrail; nothing more to do for this report.
                break
            except RuntimeError:
                # workspace got paused between _finish_run starting and this call.
                break
        return

    if parsed.tickets:
        # MAP-030: PM/QA/Pentester fanning out to tickets[] (docs/03-agent-design.md
        # §4/§8 — PM's breakdown report: "status: in_progress. Berhenti — sub-tiket
        # akan dikerjakan sendiri oleh agent yang kamu assign") is itself forward
        # momentum for THIS ticket even with no mention: the children (already
        # auto-scheduled above, in _finish_run) are what carries it forward, not a
        # handoff on the parent. Don't force-block it for "no valid handoff target".
        if notes:
            await _write_system_comment(
                session,
                ticket.id,
                "Catatan mention: " + "; ".join(notes),
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=ticket.workspace_id,
            )
        return

    # Nothing resolved: per docs/03-agent-design.md §6, a non-final status with no
    # valid handoff target must not be left hanging.
    if ticket.status not in _FINAL_STATUSES:
        # Exploration exception: an owner-chat PM run that is still in the plan phase
        # (ticket not yet approved) reports "in_progress" with no tickets[] on
        # purpose — the conversation continues until the owner approves. Blocking it
        # would make the chat unusable.
        if agent.role == "pm" and ticket.approved_at is None:
            await _write_system_comment(
                session,
                ticket.id,
                "Menunggu persetujuan user untuk plan (atau pertanyaan lanjutan). "
                "Tiket tidak diblokir — obrolan bisa dilanjutkan.",
                ticket_key=ticket.key,
                run_id=run.id,
                workspace_id=ticket.workspace_id,
            )
            return
        reason = "; ".join(notes) if notes else "tidak ada mention pada laporan"
        await _block_ticket(
            session,
            ticket,
            agent,
            f"Tidak ada target handoff yang valid ({reason}); tiket diblokir agar tidak menggantung.",
            run_id=run.id,
            workspace_id=ticket.workspace_id,
        )
    elif notes:
        await _write_system_comment(
            session,
            ticket.id,
            "Catatan mention: " + "; ".join(notes),
            ticket_key=ticket.key,
            run_id=run.id,
            workspace_id=ticket.workspace_id,
        )


async def stop(run_id: str) -> bool:
    """Signal cancellation for a running run. Returns True if a running task was found."""
    cancel_event = _CANCEL_EVENTS.get(run_id)
    if cancel_event is None:
        return False
    cancel_event.set()
    return True


async def cancel_queued(agent_id: str, run_id: str) -> bool:
    """Remove a not-yet-started run from its agent's pending FIFO queue.

    Used when stopping a `queued` run: it hasn't reached `execute()` yet, so there's no
    cancel_event to signal — the caller is responsible for marking the Run row cancelled.
    """
    async with _LOCK:
        queue = _PENDING.get(agent_id)
        if queue and run_id in queue:
            queue.remove(run_id)
            return True
    return False
