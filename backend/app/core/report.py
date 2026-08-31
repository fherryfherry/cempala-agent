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

# Categories a ticket may carry (docs/02-tsd.md §2, kanban category badge).
VALID_CATEGORIES = frozenset({"feature", "improvement", "fix", "security", "performance"})

# Max length of a single `memory:` note, so one run can't flood the table with a giant blob
# (docs/05-roadmap.md's own hallucination-risk caveat on cross-ticket agent memory).
MAX_MEMORY_NOTE_LEN = 500

_MAP_BLOCK_RE = re.compile(r"```map\s*\n(.*?)```", re.DOTALL)


@dataclass
class TicketDraft:
    title: str
    description: str = ""
    assignee: str | None = None
    priority: str = "medium"
    category: str | None = None
    sprint: str | None = None
    duration: float | None = None
    # Key of an existing top-level ticket (epic) this new ticket should attach to,
    # instead of the ticket currently being reported on (docs/03-agent-design.md §3).
    # Existence/validity is checked by the orchestrator (this module stays DB-free).
    epic: str | None = None


# Statuses a `sprints:` entry may request via `status:` — moving an EXISTING sprint
# to active/completed, on top of the plain create/update `_get_or_create_sprint`
# already did. "planned" isn't here: that's the default/fallback, never something
# an agent needs to explicitly ask for.
VALID_SPRINT_STATUSES = frozenset({"active", "completed"})


@dataclass
class SprintDraft:
    name: str
    goal: str | None = None
    duration: float | None = None
    # Calendar dates (YYYY-MM-DD) — the sprint's timebox range, declared by the PM
    # alongside the sprint (docs/03-agent-design.md §4). Optional: a sprint without
    # dates falls back to the timeline's unscheduled rendering.
    start_date: str | None = None
    end_date: str | None = None
    # Optional: activate or complete this sprint (VALID_SPRINT_STATUSES). Lets the
    # PM actually move sprints forward through a ```map block — previously the
    # only way to activate/complete a sprint was the owner's manual PATCH in the
    # UI, so the PM had no path to do it itself.
    status: str | None = None


@dataclass
class TicketUpdateDraft:
    ticket_key: str
    status: str | None = None
    priority: str | None = None
    assignee: str | None = None
    sprint: str | None = None
    duration: float | None = None


@dataclass
class ArtifactDraft:
    path: str
    group: str
    description: str = ""


@dataclass
class ArtifactUpdateDraft:
    op: str
    group: str = ""
    to: str = ""
    from_group: str = ""
    into: str = ""
    file: str = ""


@dataclass
class CommentDraft:
    ticket_key: str
    body: str


VALID_CHOICE_TYPES = frozenset({"single", "multiple"})


@dataclass
class ChoicesDraft:
    """A quick-pick question for the chat owner (frontend/lib/parse-choices.ts
    renders it as pills). Declared as a normal YAML field — NOT as literal
    ~~~choices text embedded in `summary`, which requires the agent to keep
    exact-matching indentation inside a YAML block scalar across several lines
    and reliably breaks in practice (confirmed: a dedented nested block makes
    the whole ```map block invalid YAML). The orchestrator formats this into
    the ~~~choices text and appends it to `summary` server-side instead."""

    type: str
    options: list[str]


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
    sprints: list[SprintDraft] = field(default_factory=list)
    sprints_dropped: bool = False
    sprints_dropped_reason: str | None = None
    updates: list[TicketUpdateDraft] = field(default_factory=list)
    updates_dropped: bool = False
    updates_dropped_reason: str | None = None
    artifacts: list[ArtifactDraft] = field(default_factory=list)
    artifacts_dropped: bool = False
    artifacts_dropped_reason: str | None = None
    artifact_updates: list[ArtifactUpdateDraft] = field(default_factory=list)
    artifact_updates_dropped: bool = False
    artifact_updates_dropped_reason: str | None = None
    comments: list[CommentDraft] = field(default_factory=list)
    comments_dropped: bool = False
    comments_dropped_reason: str | None = None
    memories: list[str] = field(default_factory=list)
    memories_dropped: bool = False
    memories_dropped_reason: str | None = None
    merge_branch: str | None = None
    choices: ChoicesDraft | None = None
    choices_dropped: bool = False
    choices_dropped_reason: str | None = None

    def dropped_notes(self) -> list[str]:
        """Every non-empty `*_dropped_reason` — callers must surface these (a system
        comment/message) or the agent's own `summary` is the only account of the run,
        which silently misrepresents what actually happened."""
        return [
            reason
            for reason in (
                self.tickets_dropped_reason,
                self.sprints_dropped_reason,
                self.updates_dropped_reason,
                self.artifacts_dropped_reason,
                self.artifact_updates_dropped_reason,
                self.comments_dropped_reason,
                self.memories_dropped_reason,
                self.choices_dropped_reason,
            )
            if reason
        ]


def _invalid(reason: str) -> ParseResult:
    return ParseResult(ok=False, reason=reason)


def _not_a_list_reason(field_name: str, raw: object) -> str:
    # A `str` here is the fingerprint of the most common agent mistake: writing
    # `field: |` (YAML literal block scalar) instead of `field:` followed by a
    # plain list. `|` swallows the indented list items into one string instead
    # of parsing them, so this happens a lot — name it so the agent's next
    # attempt (or whoever's debugging) doesn't have to guess.
    hint = (
        f" — you probably wrote '{field_name}: |' (a literal block) when it must be "
        f"'{field_name}:' followed directly by list items ('- ...') with NO '|'"
        if isinstance(raw, str)
        else ""
    )
    return f"'{field_name}' must be a list; dropped{hint}"


def _parse_duration(raw: object) -> float | None:
    """`duration` must be a plain number (no unit — the prompt's `_UNIT_LABELS`
    already tells the agent which unit applies). Agents sometimes write "2 minggu"/
    "3 days" anyway; `float()` on that raises ValueError and previously crashed the
    whole parse instead of just dropping this one field — never let one bad
    `duration` value take down an otherwise-valid report."""
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_report(
    text: str,
    actor_role: str,
    valid_agent_names: set[str],
    actor_name: str | None = None,
    *,
    ticket_approved: bool = True,
    sprint_creator_roles: set[str] | None = None,
    no_ticket_mode: bool = False,
    valid_roles: set[str] | None = None,
    may_declare_tickets: bool = False,
    may_manage_artifacts: bool = False,
    is_pm: bool = False,
    max_tickets_per_report: int | None = None,
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

    if no_ticket_mode:
        # No-ticket runs (routine runs, chat runs) have no ticket to transition:
        # status/mention are meaningless and must not be applied anywhere. Rejecting
        # the whole report (run failed, not blocked) keeps the contract strict — a
        # routine/chat that declares status is a prompt bug.
        if data.get("status") is not None:
            return _invalid(
                "a run with no ticket must not declare 'status' — it may only carry "
                "actions (comments/tickets/updates/memory/artifacts)"
            )
        if data.get("mention") is not None:
            return _invalid(
                "a run with no ticket must not declare 'mention' — it may only carry "
                "actions (comments/tickets/updates/memory/artifacts)"
            )
    else:
        status = data.get("status")
        if not isinstance(status, str) or not status:
            return _invalid("```map block missing required 'status'")

        if status not in STATUSES:
            return _invalid(f"unknown status '{status}'")

        # Role keys are dynamic (global `role` table). The parser still rejects
        # roles the caller doesn't vouch for — the caller (orchestrator/API layer)
        # passes the set of known role keys loaded from the DB.
        if valid_roles is not None and actor_role not in valid_roles:
            return _invalid(f"unknown role '{actor_role}'")

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return _invalid("```map block missing required non-empty 'summary'")

    choices: ChoicesDraft | None = None
    choices_dropped = False
    choices_dropped_reason: str | None = None
    choices_raw = data.get("choices")
    if choices_raw is not None:
        if not isinstance(choices_raw, dict):
            choices_dropped = True
            choices_dropped_reason = "'choices' must be a mapping (type/options); dropped"
        else:
            c_type = choices_raw.get("type") if choices_raw.get("type") in VALID_CHOICE_TYPES else "single"
            c_options_raw = choices_raw.get("options")
            if not isinstance(c_options_raw, list) or not c_options_raw:
                choices_dropped = True
                choices_dropped_reason = "'choices.options' must be a non-empty list; dropped"
            else:
                c_options = [str(o).strip() for o in c_options_raw if str(o).strip()]
                if not c_options:
                    choices_dropped = True
                    choices_dropped_reason = "'choices.options' had no usable entries; dropped"
                else:
                    choices = ChoicesDraft(type=c_type, options=c_options)

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
        if not may_declare_tickets:
            tickets_dropped = True
            tickets_dropped_reason = (
                f"role '{actor_role}' is not allowed to declare tickets[] "
                f"(may_declare_tickets=false); dropped"
            )
        elif is_pm and not ticket_approved:
            # Explorative gate (owner chat): a PM may not create tickets[] before the
            # owner explicitly approves the plan (docs/03-agent-design.md §4). The
            # report itself is still accepted — the PM may keep asking questions or
            # refine the plan — but the breakdown is dropped until approval.
            tickets_dropped = True
            tickets_dropped_reason = (
                "the owner has not approved the PM's plan yet; tickets[] ignored until "
                "they approve (reply with an approval word in the chat)"
            )
        elif not isinstance(tickets_raw, list):
            tickets_dropped = True
            tickets_dropped_reason = _not_a_list_reason("tickets", tickets_raw)
        else:
            for item in tickets_raw:
                if not isinstance(item, dict) or not item.get("title"):
                    continue  # skip malformed entries, don't fail whole parse
                raw_category = item.get("category")
                tickets.append(
                    TicketDraft(
                        title=str(item["title"]),
                        description=str(item.get("description") or ""),
                        assignee=item.get("assignee"),
                        priority=str(item.get("priority") or "medium"),
                        category=raw_category if raw_category in VALID_CATEGORIES else None,
                        sprint=str(item["sprint"]) if item.get("sprint") else None,
                        duration=_parse_duration(item.get("duration")),
                        epic=str(item["epic"]) if item.get("epic") else None,
                    )
                )

    # Guardrail `max_tickets_per_report` (CLAUDE.md "Guardrails are the only brakes
    # left"): a report is still accepted and its first N tickets still created — the
    # guardrail bounds one runaway report's blast radius, it doesn't fail the whole
    # run — but excess entries are dropped with a named reason so it's never silent.
    if (
        max_tickets_per_report is not None
        and not tickets_dropped
        and len(tickets) > max_tickets_per_report
    ):
        dropped_count = len(tickets) - max_tickets_per_report
        tickets = tickets[:max_tickets_per_report]
        tickets_dropped_reason = (
            f"Guardrail max_tickets_per_report exceeded: {dropped_count} extra "
            f"ticket(s) beyond the limit ({max_tickets_per_report}) were dropped"
        )

    # `sprints:` is a top-level companion to `tickets[]` (docs/03-agent-design.md §4):
    # declares sprint focus/timeline alongside the breakdown. Which roles may declare it
    # is a per-workspace setting (`sprint_creator_roles`, Settings page pill picker,
    # ADR-015 `.cempala/settings.yaml`) — default PM-only. Same owner-approval gate as
    # tickets[] since it's only meaningful together with tickets[].
    allowed_sprint_roles = sprint_creator_roles or {"pm"}
    sprints: list[SprintDraft] = []
    sprints_dropped = False
    sprints_dropped_reason = None
    sprints_raw = data.get("sprints")
    if sprints_raw:
        if actor_role not in allowed_sprint_roles:
            sprints_dropped = True
            sprints_dropped_reason = (
                f"role '{actor_role}' is not allowed to declare sprints[] "
                f"(allowed: {sorted(allowed_sprint_roles)}); dropped"
            )
        elif is_pm and not ticket_approved:
            sprints_dropped = True
            sprints_dropped_reason = (
                "the owner has not approved the PM's plan yet; sprints[] ignored until "
                "they approve (reply with an approval word in the chat)"
            )
        elif not isinstance(sprints_raw, list):
            sprints_dropped = True
            sprints_dropped_reason = _not_a_list_reason("sprints", sprints_raw)
        else:
            malformed_entries: list[str] = []
            for item in sprints_raw:
                if not isinstance(item, dict) or not item.get("name"):
                    malformed_entries.append(str(item))
                    continue
                raw_status = item.get("status")
                sprints.append(
                    SprintDraft(
                        name=str(item["name"]),
                        goal=str(item["goal"]) if item.get("goal") else None,
                        duration=_parse_duration(item.get("duration")),
                        start_date=str(item["start_date"]) if item.get("start_date") else None,
                        end_date=str(item["end_date"]) if item.get("end_date") else None,
                        status=raw_status if raw_status in VALID_SPRINT_STATUSES else None,
                    )
                )
            if malformed_entries:
                sprints_dropped = True
                sprints_dropped_reason = (
                    f"some sprints[] entries were malformed/dropped: {malformed_entries}; "
                    "every sprints[] entry MUST be a mapping with a 'name' field (string), "
                    "e.g. sprints: - name: \"Sprint 1\""
                )

    updates: list[TicketUpdateDraft] = []
    updates_dropped = False
    updates_dropped_reason = None
    updates_raw = data.get("updates")
    if updates_raw:
        if not may_declare_tickets:
            updates_dropped = True
            updates_dropped_reason = (
                f"role '{actor_role}' is not allowed to declare updates[] "
                f"(may_declare_tickets=false); dropped"
            )
        elif not isinstance(updates_raw, list):
            updates_dropped = True
            updates_dropped_reason = _not_a_list_reason("updates", updates_raw)
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
                        sprint=str(item["sprint"]) if item.get("sprint") else None,
                        duration=_parse_duration(item.get("duration")),
                    )
                )

    # `artifacts:` — file(s) the agent produced in the repo it wants published to the
    # Artifacts menu (docs/03-agent-design.md §3). Unlike tickets:/updates:, available to every
    # role: any of them might produce a document worth surfacing. Malformed entries (missing
    # path/group) are skipped rather than failing the whole parse, same tolerance as tickets:.
    # Path safety (must stay inside repo_path) is enforced by the orchestrator, which is the
    # only place that actually touches the filesystem — this module stays filesystem-free.
    artifacts: list[ArtifactDraft] = []
    artifacts_dropped = False
    artifacts_dropped_reason = None
    artifacts_raw = data.get("artifacts")
    if artifacts_raw:
        if not isinstance(artifacts_raw, list):
            artifacts_dropped = True
            artifacts_dropped_reason = _not_a_list_reason("artifacts", artifacts_raw)
        else:
            for item in artifacts_raw:
                if not isinstance(item, dict) or not item.get("path") or not item.get("group"):
                    continue  # skip malformed entries, don't fail whole parse
                artifacts.append(
                    ArtifactDraft(
                        path=str(item["path"]),
                        group=str(item["group"]),
                        description=str(item.get("description") or ""),
                    )
                )

    # `artifact_updates:` — PM-only organization of the Artifacts menu (docs/03-agent-design.md
    # §3): rename/merge/move/delete operations on artifact groups. Same gate as tickets:/
    # updates: (role enforced here, not trusted to the prompt). Malformed entries are skipped
    # rather than failing the whole parse; execution happens in the orchestrator.
    artifact_updates: list[ArtifactUpdateDraft] = []
    artifact_updates_dropped = False
    artifact_updates_dropped_reason = None
    artifact_updates_raw = data.get("artifact_updates")
    if artifact_updates_raw:
        if not may_manage_artifacts:
            artifact_updates_dropped = True
            artifact_updates_dropped_reason = (
                f"role '{actor_role}' is not allowed to declare artifact_updates[] "
                f"(may_manage_artifacts=false); dropped"
            )
        elif not isinstance(artifact_updates_raw, list):
            artifact_updates_dropped = True
            artifact_updates_dropped_reason = _not_a_list_reason(
                "artifact_updates", artifact_updates_raw
            )
        else:
            for item in artifact_updates_raw:
                if not isinstance(item, dict) or not item.get("op"):
                    continue  # skip malformed entries, don't fail whole parse
                op = str(item["op"])
                if op not in ("rename", "merge", "move", "delete"):
                    continue  # unknown ops are skipped here, noted by the orchestrator
                artifact_updates.append(
                    ArtifactUpdateDraft(
                        op=op,
                        group=str(item.get("group") or ""),
                        to=str(item.get("to") or ""),
                        from_group=str(item.get("from") or ""),
                        into=str(item.get("into") or ""),
                        file=str(item.get("file") or ""),
                    )
                )

    # `comments:` — no-ticket-mode-only (routine runs and chat runs): comment on OTHER
    # tickets (docs/03-agent-design.md §Routine, §4 chat two-way). Rejected in normal
    # ticket runs (a ticket run already has its own summary comment). Malformed entries
    # are skipped, same tolerance as tickets:.
    comments: list[CommentDraft] = []
    comments_dropped = False
    comments_dropped_reason = None
    comments_raw = data.get("comments")
    if comments_raw:
        if not no_ticket_mode:
            comments_dropped = True
            comments_dropped_reason = (
                "'comments' is only valid on runs with no ticket (routine/chat); dropped"
            )
        elif not isinstance(comments_raw, list):
            comments_dropped = True
            comments_dropped_reason = _not_a_list_reason("comments", comments_raw)
        else:
            for item in comments_raw:
                if not isinstance(item, dict) or not item.get("ticket") or not item.get("body"):
                    continue  # skip malformed entries, don't fail whole parse
                comments.append(
                    CommentDraft(
                        ticket_key=str(item["ticket"]),
                        body=str(item["body"]),
                    )
                )

    # `memory:` — freeform notes an agent wants to remember across tickets, so future runs
    # of the same agent don't repeat a past mistake/failure (docs/03-agent-design.md §3).
    # Open to every role, same as `artifacts:` — no gate. Non-string/empty entries are
    # skipped rather than failing the whole parse, and each note is truncated so one run
    # can't flood agent_memory with a giant blob.
    memories: list[str] = []
    memories_dropped = False
    memories_dropped_reason = None
    memories_raw = data.get("memory")
    if memories_raw:
        if isinstance(memories_raw, str):
            memories_raw = [memories_raw]
        if not isinstance(memories_raw, list):
            memories_dropped = True
            memories_dropped_reason = "'memory' must be a list of strings; dropped"
        else:
            for item in memories_raw:
                if not isinstance(item, str) or not item.strip():
                    continue  # skip malformed entries, don't fail whole parse
                memories.append(item.strip()[:MAX_MEMORY_NOTE_LEN])

    return ParseResult(
        ok=True,
        status=status if not no_ticket_mode else None,
        mention=mention,
        valid_mentions=valid_mentions,
        unknown_mentions=unknown_mentions,
        summary=summary,
        tickets=tickets,
        tickets_dropped=tickets_dropped,
        tickets_dropped_reason=tickets_dropped_reason,
        sprints=sprints,
        sprints_dropped=sprints_dropped,
        sprints_dropped_reason=sprints_dropped_reason,
        updates=updates,
        updates_dropped=updates_dropped,
        updates_dropped_reason=updates_dropped_reason,
        artifacts=artifacts,
        artifacts_dropped=artifacts_dropped,
        artifacts_dropped_reason=artifacts_dropped_reason,
        artifact_updates=artifact_updates,
        artifact_updates_dropped=artifact_updates_dropped,
        artifact_updates_dropped_reason=artifact_updates_dropped_reason,
        comments=comments,
        comments_dropped=comments_dropped,
        comments_dropped_reason=comments_dropped_reason,
        memories=memories,
        memories_dropped=memories_dropped,
        memories_dropped_reason=memories_dropped_reason,
        merge_branch=data.get("merge_branch") if isinstance(data.get("merge_branch"), str) else None,
        choices=choices,
        choices_dropped=choices_dropped,
        choices_dropped_reason=choices_dropped_reason,
    )
