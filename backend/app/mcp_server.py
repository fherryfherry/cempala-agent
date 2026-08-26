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
    return f"Gagal {what}: {res['error']['message']}"


def _is_error(res: Any) -> bool:
    return isinstance(res, dict) and isinstance(res.get("error"), dict)


def _api_raw(path: str) -> bytes:
    """Raw fetch for binary/file responses (attachment download)."""
    return _http.get(f"{API_BASE}{path}").content


def create_server() -> MCPServer:
    server = MCPServer(
        name="map-tickets",
        title="Map portal tickets",
        version="0.1.0",
    )

    @server.tool(description="Daftar semua tiket di workspace (menu Board), diurutkan dari yang paling baru diupdate. Setiap entri: key, status, prioritas, assignee, waktu update, judul. Tiket top-level (epic — area fitur besar, reusable) ditandai [EPIC]. Pakai tool ini untuk menemukan tiket yang macet/tidak bergerak, dan untuk cek epic yang sudah ada sebelum create_ticket — sumber kebenaran status tiket, bukan repo.")
    async def list_tickets(limit: int = 100, offset: int = 0) -> str:
        tickets = await _api(f"/workspaces/{WORKSPACE_ID}/tickets?limit={limit}&offset={offset}")
        if _is_error(tickets):
            return _error(tickets, "mengambil tiket")
        if not tickets:
            return "Tidak ada tiket di workspace ini."
        return "Daftar tiket (terbaru dulu):\n" + "\n".join(f"- {_ticket_row(t)}" for t in tickets)

    @server.tool(description="Detail satu tiket: deskripsi, komentar (termasuk sistem), status, assignee, sub-tiket. key = kode tiket seperti MAP-002.")
    async def get_ticket(key: str) -> str:
        detail = await _api(f"/tickets/{key}")
        if _is_error(detail):
            return _error(detail, "mengambil tiket")
        lines = [
            f"{detail['key']} — {detail['title']}",
            f"Status: {detail['status']} | Prioritas: {detail.get('priority')} | "
            f"Assignee: {detail.get('assignee_id') or '-'}",
            f"Deskripsi: {detail.get('description') or '-'}",
        ]
        if detail.get("blocked_reason"):
            lines.append(f"Alasan blocked: {detail['blocked_reason']}")
        comments = detail.get("comments") or []
        if comments:
            lines.append("Komentar:")
            for c in comments:
                who = "system" if c.get("is_system") else (c.get("author_agent_id") or "owner")
                lines.append(f"  - [{who}] {c['body'][:300]}")
        children = detail.get("children") or []
        if children:
            lines.append("Sub-tiket: " + ", ".join(f"{c['key']} [{c['status']}]" for c in children))
        return "\n".join(lines)

    @server.tool(description="Daftar artifacts di workspace (menu Artifacts): kelompok, nama file, tiket asal, deskripsi. Semua artifact yang pernah dipublikasikan agent.")
    async def list_artifacts() -> str:
        groups = await _api(f"/workspaces/{WORKSPACE_ID}/artifacts")
        if _is_error(groups):
            return _error(groups, "mengambil artifacts")
        if not groups:
            return "Belum ada artifact di workspace ini."
        out = []
        for g in groups:
            out.append(f"## {g['name']}")
            for a in g["attachments"]:
                desc = f" — {a['description']}" if a.get("description") else ""
                out.append(f"- {a['filename']} ({a['ticket_key']}){desc}")
        return "\n".join(out)

    @server.tool(description="Baca isi artifact (file yang dipublikasikan agent). attachment_id = id artifact dari list_artifacts. Untuk teks/markdown dikembalikan isinya; untuk gambar/PDF dikembalikan teks mentah atau info.")
    async def read_artifact(attachment_id: str) -> str:
        try:
            raw = await _api_raw(f"/attachments/{attachment_id}?inline=1")
        except Exception as exc:
            return f"Gagal membaca artifact: {exc}"
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
            if len(text) > 8000:
                text = text[:8000] + "\n...(dipotong)"
            return text
        return str(raw)

    @server.tool(description="Kirim komentar follow-up ke sebuah tiket. Tulis komentar yang jelas: apa yang perlu dicek, siapa yang harus lanjut (sebut nama agent), dan kenapa.")
    async def post_comment(key: str, body: str) -> str:
        if not AGENT_ID:
            # Fail loud, never fall back to "owner" authorship: an agent-authored
            # comment without an author would be treated as human-written, trigger
            # mention runs, and duplicate every report (MAP-048).
            return "Error: tidak ada MAP_AGENT_ID — komentar tidak dikirim (server MCP kehilangan identitas agent)."
        payload: dict = {"body": body, "author_agent_id": AGENT_ID}
        res = await _api(f"/tickets/{key}/comments", method="POST", body=payload)
        if _is_error(res):
            return _error(res, "mengirim komentar")
        return f"Komentar terkirim ke {key}."

    @server.tool(description="Daftar komentar sebuah tiket (terbaru dulu, paling banyak 50). Pakai untuk melihat riwayat follow-up sebelum menulis komentar baru.")
    async def list_comments(key: str, limit: int = 50) -> str:
        comments = await _api(f"/tickets/{key}/comments?limit={limit}")
        if _is_error(comments):
            return _error(comments, "mengambil komentar")
        if not comments:
            return "Belum ada komentar di tiket ini."
        out = ["Komentar (terbaru dulu):"]
        for c in comments:
            who = "system" if c.get("is_system") else (c.get("author_agent_id") or "owner")
            out.append(f"- [{who}] {c['body'][:300]}")
        return "\n".join(out)

    @server.tool(description="Buat tiket baru di workspace. Isi `epic` dengan key epic yang SUDAH ADA (lihat tanda [EPIC] di list_tickets) kalau tiket ini bagian dari area fitur besar yang sudah ada — WAJIB reuse kalau relevan. Kosongkan `epic` HANYA untuk area fitur besar yang benar-benar baru (tiket ini sendiri akan jadi epic baru). Tidak otomatis dijalankan.")
    async def create_ticket(
        title: str, description: str = "", priority: str = "medium", epic: str | None = None
    ) -> str:
        body: dict = {"title": title, "description": description, "priority": priority}
        if epic:
            epic_detail = await _api(f"/tickets/{epic}")
            if _is_error(epic_detail):
                return _error(epic_detail, f"menempel ke epic '{epic}'")
            if epic_detail.get("parent_id"):
                return (
                    f"'{epic}' bukan epic top-level (punya parent sendiri) — tidak bisa "
                    "dipakai sebagai epic. Tiket tidak dibuat."
                )
            body["parent_id"] = epic_detail["id"]
        else:
            body["is_new_epic"] = True
        res = await _api(f"/workspaces/{WORKSPACE_ID}/tickets", "POST", body)
        if _is_error(res):
            return _error(res, "membuat tiket")
        return f"Tiket dibuat: {res['key']} — {res['title']} (status {res['status']})"

    @server.tool(description="Ubah status/prioritas tiket. Status legal: backlog, todo, in_progress, review, qa, security, done, blocked. Backend menegakkan state machine.")
    async def update_ticket(key: str, status: str | None = None, priority: str | None = None) -> str:
        body: dict = {}
        if status:
            body["status"] = status
        if priority:
            body["priority"] = priority
        if not body:
            return "Tidak ada field yang diubah."
        if AGENT_ID:
            body["actor_agent_id"] = AGENT_ID
        res = await _api(f"/tickets/{key}", "PATCH", body)
        if _is_error(res):
            return _error(res, "update tiket")
        return f"Tiket {key} diperbarui: status={res.get('status')}, priority={res.get('priority')}"

    @server.tool(description="HAPUS tiket secara permanen (beserta komentar/attachments/runs-nya). HANYA untuk PM, dan HANYA untuk tiket yang benar-benar tidak diperlukan (duplikat, salah buat, eksperimen). Jangan hapus tiket yang sedang dikerjakan, punya sub-tiket aktif, atau menjadi acuan deliverable yang sudah dipublikasikan — lebih baik tiket macet dibiarkan atau di-block dengan penjelasan. Backend hanya mengizinkan PM.")
    async def delete_ticket(key: str) -> str:
        if not AGENT_ID:
            return "Error: tidak ada MAP_AGENT_ID — penghapusan ditolak (server MCP kehilangan identitas agent)."
        res = await _api(f"/tickets/{key}?actor_agent_id={AGENT_ID}", method="DELETE")
        if _is_error(res):
            return _error(res, "menghapus tiket")
        return f"Tiket {key} dihapus."

    @server.tool(description="Lihat catatan memory agent ini (lintas tiket). Setiap entri: id, isi, asal.")
    async def get_memory() -> str:
        if not AGENT_ID:
            return "Tidak ada agent yang sedang berjalan."
        notes = await _api(f"/agents/{AGENT_ID}/memory")
        if _is_error(notes):
            return _error(notes, "mengambil memory")
        if not notes:
            return "Belum ada catatan memory."
        return "Catatan memory:\n" + "\n".join(
            f"- [{m['id']}] ({m['origin']}) {m['note']}" for m in notes
        )

    @server.tool(description="Simpan catatan memory baru untuk agent ini (lintas tiket) — hal yang jangan diulang lagi, keputusan penting, atau konteks yang harus diingat di run berikutnya. Maksimal 500 karakter.")
    async def create_memory(note: str) -> str:
        if not AGENT_ID:
            return "Tidak ada agent yang sedang berjalan."
        res = await _api(f"/agents/{AGENT_ID}/memory", method="POST", body={"note": note})
        if _is_error(res):
            return _error(res, "menyimpan memory")
        return f"Memory disimpan ({res['id']})."

    @server.tool(description="Perbarui isi catatan memory yang sudah ada. memory_id dari get_memory.")
    async def update_memory(memory_id: str, note: str) -> str:
        res = await _api(f"/agent-memory/{memory_id}", method="PATCH", body={"note": note})
        if _is_error(res):
            return _error(res, "update memory")
        return f"Memory {memory_id} diperbarui."

    return server


def main() -> None:
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
