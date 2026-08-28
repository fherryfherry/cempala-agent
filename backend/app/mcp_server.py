"""MCP server exposing ticket operations to opencode agents (ADR-011).

Runs as a local stdio subprocess (spawned per opencode run via a per-run
opencode.json `mcp` block) and talks to the portal backend over HTTP
(MAP_API_BASE, default http://127.0.0.1:8000/api) — every validation (state
machine, role gates, mention resolution) stays in the backend, this server is
just a thin proxy. It binds nothing: stdio transport only, no TCP socket.

Env:
  MAP_API_BASE       base URL of the backend API (default http://127.0.0.1:8000/api)
  MAP_WORKSPACE_ID   workspace whose tickets these tools operate on
  MAP_AGENT_ID       agent on whose behalf tools write (comments/memory/ticket edits)

The workspace/agent ids are ALSO accepted as CLI flags (`--workspace-id`,
`--agent-id`) as a fallback: opencode's MCP launcher may not forward the `env`
block of a local config to the subprocess, and without the agent id every MCP
comment would be attributed to the owner (and, being "human-authored", would
trigger mention runs — a real incident, see MAP-048).

No auth (ADR-005): the backend binds 127.0.0.1 and this server is only ever
spawned by the backend itself for a run it already authorizes.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

API_BASE = os.environ.get("MAP_API_BASE", "http://127.0.0.1:8000/api")


def _ids_from_env_or_args() -> tuple[str, str]:
    """(workspace_id, agent_id) from env, falling back to CLI flags.

    The env block in the per-run opencode.json MCP config is the primary channel;
    CLI flags cover the case where opencode drops the env block when spawning the
    subprocess (observed: MCP comments landing as owner-authored, MAP-048).
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace-id", dest="workspace_id")
    parser.add_argument("--agent-id", dest="agent_id")
    args, _ = parser.parse_known_args()
    return (
        args.workspace_id or os.environ.get("MAP_WORKSPACE_ID", ""),
        args.agent_id or os.environ.get("MAP_AGENT_ID", ""),
    )


WORKSPACE_ID, AGENT_ID = _ids_from_env_or_args()

# Module-level async client so tests can swap in an httpx transport (e.g.
# ASGITransport over the real FastAPI app).
_http: httpx.AsyncClient = httpx.AsyncClient(timeout=30)


async def _api(path: str, method: str = "GET", body: Any = None) -> Any:
    """HTTP call to the backend API (this server runs inside opencode's subprocess)."""
    res = await _http.request(method, f"{API_BASE}{path}", json=body)
    try:
        return res.json()
    except ValueError:
        return res.content


async def _api_raw(path: str) -> bytes:
    """Raw fetch for binary/file responses (attachment download)."""
    return (await _http.get(f"{API_BASE}{path}")).content


def _ticket_row(t: dict) -> str:
    assignee = t.get("assignee_id") or "-"
    epic_tag = " [EPIC]" if not t.get("parent_id") else ""
    return (
        f"{t['key']}{epic_tag} [{t['status']}] prio={t.get('priority')} assignee={assignee} "
        f"updated={t.get('updated_at', '')[:19]} — {t['title']}"
    )


def _error(res: dict, what: str) -> str:
    return f"Failed to {what}: {res['error']['message']}"


def _is_error(res: Any) -> bool:
    return isinstance(res, dict) and isinstance(res.get("error"), dict)


def _api_raw(path: str) -> bytes:
    """Raw fetch for binary/file responses (attachment download)."""
    return _http.get(f"{API_BASE}{path}").content


# Role permission flags, fetched once per run (this process is spawned per run, so they
# can't change under us). Keyed by agent id so tests that rebind AGENT_ID re-resolve
# instead of inheriting the previous agent's permissions.
_ROLE_FLAGS: dict[str, dict[str, bool]] = {}


async def _may(flag: str) -> bool:
    """Whether this run's agent holds a role permission flag (`may_declare_tickets`,
    `may_manage_artifacts`, `is_reviewer`).

    The `map` block parser (app/core/report.py) enforces these same flags on
    `tickets:`/`updates:`/`artifact_updates:`; without the check here the equivalent MCP
    write tools were a way around it — CLAUDE.md's rule is that role permissions are
    enforced in the parser, not trusted to the prompt. Both sides read the same source:
    the `role` table, via the API.

    Fails CLOSED: if the role can't be resolved, the write is refused.
    """
    if AGENT_ID not in _ROLE_FLAGS:
        flags: dict[str, bool] = {}
        if AGENT_ID:
            agents = await _api(f"/workspaces/{WORKSPACE_ID}/agents")
            roles = await _api("/roles")
            if isinstance(agents, list) and isinstance(roles, list):
                me = next((a for a in agents if a.get("id") == AGENT_ID), None)
                if me is not None:
                    role = next((r for r in roles if r.get("key") == me.get("role")), None)
                    if role is not None:
                        flags = {
                            k: bool(role.get(k))
                            for k in ("may_declare_tickets", "may_manage_artifacts", "is_reviewer")
                        }
        _ROLE_FLAGS[AGENT_ID] = flags
    return _ROLE_FLAGS[AGENT_ID].get(flag, False)


def _refused(flag: str, what: str) -> str:
    return (
        f"Refused to {what}: your role does not have the '{flag}' permission. "
        f"The same gate applies to the closing `map` block, so declaring it there won't "
        f"work either — hand off to a role that may do this instead."
    )


def create_server() -> MCPServer:
    server = MCPServer(
        name="map-tickets",
        title="Map portal tickets",
        version="0.1.0",
    )

    @server.tool(description="List all tickets in the workspace (Board menu), most-recently-updated first. Each entry: key, status, priority, assignee, update time, title. Top-level tickets (epics — large reusable feature areas) are tagged [EPIC]. Use this to find stuck/stalled tickets, and to check existing epics before create_ticket — the source of truth for ticket status, not the repo.")
    async def list_tickets(limit: int = 100, offset: int = 0) -> str:
        tickets = await _api(f"/workspaces/{WORKSPACE_ID}/tickets?limit={limit}&offset={offset}")
        if _is_error(tickets):
            return _error(tickets, "fetch tickets")
        if not tickets:
            return "No tickets in this workspace."
        return "Ticket list (most recent first):\n" + "\n".join(f"- {_ticket_row(t)}" for t in tickets)

    @server.tool(description="Detail of one ticket: description, comments (including system), status, assignee, sub-tickets. key = ticket code like MAP-002.")
    async def get_ticket(key: str) -> str:
        detail = await _api(f"/tickets/{key}")
        if _is_error(detail):
            return _error(detail, "fetch ticket")
        lines = [
            f"{detail['key']} — {detail['title']}",
            f"Status: {detail['status']} | Priority: {detail.get('priority')} | "
            f"Assignee: {detail.get('assignee_id') or '-'}",
            f"Description: {detail.get('description') or '-'}",
        ]
        if detail.get("blocked_reason"):
            lines.append(f"Blocked reason: {detail['blocked_reason']}")
        comments = detail.get("comments") or []
        if comments:
            lines.append("Comments:")
            for c in comments:
                who = "system" if c.get("is_system") else (c.get("author_agent_id") or "owner")
                lines.append(f"  - [{who}] {c['body'][:300]}")
        children = detail.get("children") or []
        if children:
            lines.append("Sub-tickets: " + ", ".join(f"{c['key']} [{c['status']}]" for c in children))
        return "\n".join(lines)

    @server.tool(description="List artifacts in the workspace (Artifacts menu): group, filename, source ticket, description. Every artifact ever published by an agent. Fill in `filename` to CHECK WHETHER A FILENAME ALREADY EXISTS (substring, case-insensitive) — call this BEFORE declaring `artifacts:` in the ```map block so you don't publish the same filename twice.")
    async def list_artifacts(filename: str | None = None) -> str:
        groups = await _api(f"/workspaces/{WORKSPACE_ID}/artifacts")
        if _is_error(groups):
            return _error(groups, "fetch artifacts")
        if not groups:
            return "No artifacts in this workspace yet."
        needle = filename.strip().lower() if filename else None
        out = []
        for g in groups:
            attachments = g["attachments"]
            if needle:
                attachments = [a for a in attachments if needle in a["filename"].lower()]
                if not attachments:
                    continue
            out.append(f"## {g['name']}")
            for a in attachments:
                desc = f" — {a['description']}" if a.get("description") else ""
                out.append(f"- {a['filename']} ({a['ticket_key']}){desc}")
        if needle and not out:
            return f"No artifact with a filename containing '{filename}' — safe to publish."
        return "\n".join(out)

    @server.tool(description="Read the content of an artifact (a file published by an agent). attachment_id = artifact id from list_artifacts. For text/markdown the content is returned; for images/PDFs raw text or info is returned instead.")
    async def read_artifact(attachment_id: str) -> str:
        try:
            raw = await _api_raw(f"/attachments/{attachment_id}?inline=1")
        except Exception as exc:
            return f"Failed to read artifact: {exc}"
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
            if len(text) > 8000:
                text = text[:8000] + "\n...(truncated)"
            return text
        return str(raw)

    @server.tool(description="Post a follow-up comment on a ticket. Write a clear comment: what needs to be checked, who should follow up (name the agent), and why.")
    async def post_comment(key: str, body: str) -> str:
        if not AGENT_ID:
            # Fail loud, never fall back to "owner" authorship: an agent-authored
            # comment without an author would be treated as human-written, trigger
            # mention runs, and duplicate every report (MAP-048).
            return "Error: no MAP_AGENT_ID — comment not sent (MCP server lost the agent identity)."
        payload: dict = {"body": body, "author_agent_id": AGENT_ID}
        res = await _api(f"/tickets/{key}/comments", method="POST", body=payload)
        if _is_error(res):
            return _error(res, "post comment")
        return f"Comment posted to {key}."

    @server.tool(description="List a ticket's comments (most recent first, up to 50). Use this to see the follow-up history before writing a new comment.")
    async def list_comments(key: str, limit: int = 50) -> str:
        comments = await _api(f"/tickets/{key}/comments?limit={limit}")
        if _is_error(comments):
            return _error(comments, "fetch comments")
        if not comments:
            return "No comments on this ticket yet."
        out = ["Comments (most recent first):"]
        for c in comments:
            who = "system" if c.get("is_system") else (c.get("author_agent_id") or "owner")
            out.append(f"- [{who}] {c['body'][:300]}")
        return "\n".join(out)

    @server.tool(description="Create a new ticket in the workspace. Fill `epic` with an epic key that ALREADY EXISTS (see the [EPIC] tag in list_tickets) if this ticket belongs to an existing large feature area — reuse it if relevant, MANDATORY. Leave `epic` empty ONLY for a genuinely new large feature area (this ticket itself becomes the new epic). Not run automatically.")
    async def create_ticket(
        title: str, description: str = "", priority: str = "medium", epic: str | None = None
    ) -> str:
        # ponytail: role gate only. The PM owner-approval gate report.py applies to
        # `tickets:` in chat runs can't be checked here — the MCP config passes no run id,
        # so this server can't tell it's inside an unapproved chat. Pass the run id in
        # mcp_config.py if that gap matters.
        if not await _may("may_declare_tickets"):
            return _refused("may_declare_tickets", "create a ticket")
        body: dict = {"title": title, "description": description, "priority": priority}
        if epic:
            epic_detail = await _api(f"/tickets/{epic}")
            if _is_error(epic_detail):
                return _error(epic_detail, f"attach to epic '{epic}'")
            if epic_detail.get("parent_id"):
                return (
                    f"'{epic}' is not a top-level epic (it has its own parent) — cannot be "
                    "used as an epic. Ticket not created."
                )
            body["parent_id"] = epic_detail["id"]
        else:
            body["is_new_epic"] = True
        res = await _api(f"/workspaces/{WORKSPACE_ID}/tickets", "POST", body)
        if _is_error(res):
            return _error(res, "create ticket")
        return f"Ticket created: {res['key']} — {res['title']} (status {res['status']})"

    @server.tool(description="Change a ticket's status/priority. Legal statuses: backlog, todo, in_progress, review, qa, security, done, blocked. The backend enforces the state machine.")
    async def update_ticket(key: str, status: str | None = None, priority: str | None = None) -> str:
        # Same gate report.py puts on `updates:` — see `_may`.
        if not await _may("may_declare_tickets"):
            return _refused("may_declare_tickets", "update a ticket")
        body: dict = {}
        if status:
            body["status"] = status
        if priority:
            body["priority"] = priority
        if not body:
            return "No field changed."
        if AGENT_ID:
            body["actor_agent_id"] = AGENT_ID
        res = await _api(f"/tickets/{key}", "PATCH", body)
        if _is_error(res):
            return _error(res, "update ticket")
        return f"Ticket {key} updated: status={res.get('status')}, priority={res.get('priority')}"

    @server.tool(description="PERMANENTLY delete a ticket (along with its comments/attachments/runs). PM ONLY, and ONLY for tickets that are genuinely not needed (duplicate, created by mistake, experiment). Do NOT delete a ticket that's being worked, has active sub-tickets, or is referenced by an already-published deliverable — better to leave a stuck ticket alone or block it with an explanation. The backend only permits PM.")
    async def delete_ticket(key: str) -> str:
        if not AGENT_ID:
            return "Error: no MAP_AGENT_ID — deletion refused (MCP server lost the agent identity)."
        res = await _api(f"/tickets/{key}?actor_agent_id={AGENT_ID}", method="DELETE")
        if _is_error(res):
            return _error(res, "delete ticket")
        return f"Ticket {key} deleted."

    @server.tool(description="View this agent's memory notes (across tickets). Each entry: id, content, origin.")
    async def get_memory() -> str:
        if not AGENT_ID:
            return "No agent is currently running."
        notes = await _api(f"/agents/{AGENT_ID}/memory")
        if _is_error(notes):
            return _error(notes, "fetch memory")
        if not notes:
            return "No memory notes yet."
        return "Memory notes:\n" + "\n".join(
            f"- [{m['id']}] ({m['origin']}) {m['note']}" for m in notes
        )

    @server.tool(description="Save a new memory note for this agent (across tickets) — something not to repeat, an important decision, or context to remember on the next run. Max 500 characters.")
    async def create_memory(note: str) -> str:
        if not AGENT_ID:
            return "No agent is currently running."
        res = await _api(f"/agents/{AGENT_ID}/memory", method="POST", body={"note": note})
        if _is_error(res):
            return _error(res, "save memory")
        return f"Memory saved ({res['id']})."

    @server.tool(description="Update the content of an existing memory note. memory_id from get_memory.")
    async def update_memory(memory_id: str, note: str) -> str:
        res = await _api(f"/agent-memory/{memory_id}", method="PATCH", body={"note": note})
        if _is_error(res):
            return _error(res, "update memory")
        return f"Memory {memory_id} updated."

    return server


def main() -> None:
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
