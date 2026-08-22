"""Parser for the ```map fenced block agents close their answers with.

Pure Python — no FastAPI/HTTP, no DB session. Callers pass in the workspace's
valid agent names and the actor's role/name; this module never fetches
anything itself, which keeps it testable without a DB (docs/02-tsd.md §4.3,
docs/03-agent-design.md §3, ADR-009).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from app.core.state_machine import STATUSES

# Role -> statuses that role's ```map block is allowed to declare.
# docs/03-agent-design.md §3. Distinct from (and enforced in addition to)
# the from/to state-machine transition matrix in state_machine.py.
ROLE_ALLOWED_STATUSES: dict[str, frozenset[str]] = {
    "pm": frozenset({"in_progress", "done", "blocked"}),
    "lead": frozenset({"qa", "in_progress", "blocked"}),
    "engineer": frozenset({"review", "blocked"}),
    "designer": frozenset({"review", "blocked"}),
    "qa": frozenset({"security", "in_progress", "blocked"}),
    "pentester": frozenset({"done", "in_progress", "blocked"}),
}

# Roles allowed to include tickets[] in their block. docs/03-agent-design.md §3.
ROLES_ALLOWED_TICKETS = frozenset({"pm", "qa", "pentester"})

_MAP_BLOCK_RE = re.compile(r"```map\s*\n(.*?)```", re.DOTALL)


@dataclass
class TicketDraft:
    title: str
    description: str = ""
    assignee: str | None = None
    priority: str = "medium"


@dataclass
class TicketUpdateDraft:
    ticket_key: str
    status: str | None = None
    priority: str | None = None
    assignee: str | None = None


@dataclass
class ParseResult:
    ok: bool
    reason: str | None = None
    status: str | None = None
    mention: list[str] = field(default_factory=list)
    valid_mentions: list[str] = field(default_factory=list)
    unknown_mentions: list[str] = field(default_factory=list)
    summary: str | None = None
    tickets: list[TicketDraft] = field(default_factory=list)
    tickets_dropped: bool = False
    tickets_dropped_reason: str | None = None
    updates: list[TicketUpdateDraft] = field(default_factory=list)
    updates_dropped: bool = False
    updates_dropped_reason: str | None = None


def _invalid(reason: str) -> ParseResult:
    return ParseResult(ok=False, reason=reason)


def parse_report(
    text: str,
    actor_role: str,
    valid_agent_names: set[str],
    actor_name: str | None = None,
) -> ParseResult:
    matches = _MAP_BLOCK_RE.findall(text or "")
    if not matches:
        return _invalid("no ```map block found in agent output")

    raw = matches[-1]  # multiple blocks -> last one wins

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return _invalid(f"```map block is not valid YAML: {exc}")

    if not isinstance(data, dict):
        return _invalid("```map block did not parse to a YAML mapping")

    status = data.get("status")
    if not isinstance(status, str) or not status:
        return _invalid("```map block missing required 'status'")

    if status not in STATUSES:
        return _invalid(f"unknown status '{status}'")

    allowed = ROLE_ALLOWED_STATUSES.get(actor_role, frozenset())
    if actor_role not in ROLE_ALLOWED_STATUSES:
        return _invalid(f"unknown role '{actor_role}'")
    if status not in allowed:
        return _invalid(
            f"role '{actor_role}' is not allowed to declare status '{status}' "
            f"(allowed: {sorted(allowed)})"
        )

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return _invalid("```map block missing required non-empty 'summary'")

    mention_raw = data.get("mention") or []
    if isinstance(mention_raw, str):
        mention_raw = [mention_raw]
    if not isinstance(mention_raw, list):
        return _invalid("'mention' must be a list of agent names")
    mention = [str(m) for m in mention_raw]

    valid_mentions: list[str] = []
    unknown_mentions: list[str] = []
    for name in mention:
        if actor_name is not None and name == actor_name:
            continue  # self-mention silently dropped (docs/03-agent-design.md §6)
        if name in valid_agent_names:
            valid_mentions.append(name)
        else:
            unknown_mentions.append(name)

    tickets: list[TicketDraft] = []
    tickets_dropped = False
    tickets_dropped_reason = None
    tickets_raw = data.get("tickets")
    if tickets_raw:
        if actor_role not in ROLES_ALLOWED_TICKETS:
            tickets_dropped = True
            tickets_dropped_reason = (
                f"role '{actor_role}' is not allowed to declare tickets[] "
                f"(allowed: {sorted(ROLES_ALLOWED_TICKETS)}); dropped"
            )
        elif not isinstance(tickets_raw, list):
            tickets_dropped = True
            tickets_dropped_reason = "'tickets' must be a list; dropped"
        else:
            for item in tickets_raw:
                if not isinstance(item, dict) or not item.get("title"):
                    continue  # skip malformed entries, don't fail whole parse
                tickets.append(
                    TicketDraft(
                        title=str(item["title"]),
                        description=str(item.get("description") or ""),
                        assignee=item.get("assignee"),
                        priority=str(item.get("priority") or "medium"),
                    )
                )

    updates: list[TicketUpdateDraft] = []
    updates_dropped = False
    updates_dropped_reason = None
    updates_raw = data.get("updates")
    if updates_raw:
        if actor_role not in ROLES_ALLOWED_TICKETS:
            updates_dropped = True
            updates_dropped_reason = (
                f"role '{actor_role}' is not allowed to declare updates[] "
                f"(allowed: {sorted(ROLES_ALLOWED_TICKETS)}); dropped"
            )
        elif not isinstance(updates_raw, list):
            updates_dropped = True
            updates_dropped_reason = "'updates' must be a list; dropped"
        else:
            for item in updates_raw:
                if not isinstance(item, dict) or not item.get("ticket"):
                    continue  # skip malformed entries, don't fail whole parse
                updates.append(
                    TicketUpdateDraft(
                        ticket_key=str(item["ticket"]),
                        status=str(item["status"]) if item.get("status") else None,
                        priority=str(item["priority"]) if item.get("priority") else None,
                        assignee=str(item["assignee"]) if item.get("assignee") else None,
                    )
                )

    return ParseResult(
        ok=True,
        status=status,
        mention=mention,
        valid_mentions=valid_mentions,
        unknown_mentions=unknown_mentions,
        summary=summary,
        tickets=tickets,
        tickets_dropped=tickets_dropped,
        tickets_dropped_reason=tickets_dropped_reason,
        updates=updates,
        updates_dropped=updates_dropped,
        updates_dropped_reason=updates_dropped_reason,
    )
