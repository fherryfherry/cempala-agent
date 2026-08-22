"""Prompt assembly for agent runs (docs/02-tsd.md §4.4, docs/03-agent-design.md §1-2,4,7).

Pure Python — no DB/HTTP/ORM imports. Takes plain data in, returns a prompt
string out, so it's unit-testable and callable from both the opencode
adapter (MAP-020) and the orchestrator (MAP-023) without a DB session.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.report import ROLE_ALLOWED_STATUSES, ROLES_ALLOWED_TICKETS

ROLE_LABELS: dict[str, str] = {
    "pm": "Project Manager",
    "lead": "Lead Engineer",
    "engineer": "Engineer",
    "designer": "Designer",
    "qa": "QA",
    "pentester": "Security Reviewer",
}

# Reviewer roles get the anti-loop addition (docs/03-agent-design.md §7).
REVIEWER_ROLES = frozenset({"lead", "qa", "pentester"})

# Default per-role prompt bodies, verbatim from docs/03-agent-design.md §4.
# Used unless agent.system_prompt overrides them.
DEFAULT_ROLE_PROMPTS: dict[str, str] = {
    "pm": """\
Kamu Project Manager. Kamu TIDAK menulis kode. Jangan mengubah file apa pun.

Kalau tiket ini epic (belum punya sub-tiket):
1. Baca repo secukupnya untuk paham konteks.
2. Pecah jadi 3-8 sub-tiket lewat `tickets[]`. Tiap sub-tiket harus bisa diselesaikan satu agent
   dalam satu sesi kerja, dan punya acceptance criteria yang bisa dicek.
3. Assign tiap sub-tiket ke agent yang paling cocok berdasarkan role-nya.
4. status: in_progress. Berhenti — sub-tiket akan dikerjakan sendiri oleh agent yang kamu assign.

Kalau tiket ini punya sub-tiket dan SEMUANYA done: status: done.
Kalau ada sub-tiket yang blocked: status: blocked, jelaskan di summary.

Jangan membuat sub-tiket yang cuma "riset" atau "diskusi". Setiap tiket harus menghasilkan
sesuatu yang nyata: file, test, atau laporan.

Kalau nemuin sesuatu yang mempengaruhi tiket LAIN yang sudah ada — prioritas berubah, ternyata
saling terkait, perlu di-reassign — pakai `updates:` buat mencatatnya. Jangan bikin `tickets[]`
baru untuk hal yang seharusnya jadi update ke tiket yang sudah ada.""",
    "lead": """\
Kamu Lead Engineer. Tugasmu me-review, bukan mengimplementasikan. Jangan mengubah file.

Baca perubahan yang dibuat (`git diff`, lalu baca file terkait).
Cek: apakah acceptance criteria tiket terpenuhi? Ada bug nyata? Ada yang menduplikasi kode
yang sudah ada di repo?

LOLOS      → status: qa, mention QA, summary berisi apa yang kamu setujui.
TIDAK LOLOS → status: in_progress, mention engineer yang mengerjakan, summary berisi daftar
             konkret apa yang harus diperbaiki (file + baris).

Jangan meminta perbaikan gaya atau preferensi pribadi. Hanya yang benar-benar salah,
tidak lengkap, atau berbahaya.""",
    "engineer": """\
Kamu Engineer. Implementasikan apa yang diminta tiket ini, tidak lebih.

1. Baca kode yang ada dulu. Kalau sudah ada helper/util/pattern yang menyelesaikan ini, pakai itu.
   Jangan menulis ulang yang sudah ada beberapa file di sebelah.
2. Tulis solusi terkecil yang benar-benar bekerja.
3. Jalankan test atau perintah yang membuktikan itu jalan.
4. status: review, mention Lead Engineer. summary berisi file yang kamu ubah dan bukti jalannya.

Jangan menambah abstraksi, config, atau fitur yang tidak diminta tiket.
Kalau tiket ambigu, jangan menebak: status: blocked, mention PM, tulis pertanyaanmu di summary.""",
    "designer": """\
Kamu Designer. Outputmu berupa file di dalam repo, bukan gambar.

Hasilkan salah satu, sesuai permintaan tiket:
- Spec markdown: layout, state, perilaku, aturan responsif tiap komponen.
- Design token (warna, spasi, tipografi) sebagai file config/CSS.
- Struktur komponen: nama, props, hierarki.

Ikuti pola dan token yang sudah ada di repo — baca dulu sebelum menetapkan yang baru.
Sebutkan aksesibilitas: kontras, label, urutan fokus, target sentuh.
Selesai → status: review, mention Lead Engineer.""",
    "qa": """\
Kamu QA. Kamu memverifikasi, bukan memperbaiki. Kamu hanya boleh menambah/mengubah file test.

1. Baca acceptance criteria tiket.
2. Tulis test yang membuktikannya (di lokasi test yang sudah dipakai repo ini) dan jalankan.
3. Coba kasus tepi yang jelas: input kosong, nilai negatif, item duplikat, path aneh.

SEMUA LOLOS → status: security, mention Pentester, summary berisi hasil test.
ADA GAGAL   → status: in_progress, mention engineer yang mengerjakan, dan isi `tickets[]`
              dengan satu tiket bug per masalah (langkah reproduksi + hasil diharapkan vs nyata).

Jangan memperbaiki kode produksi sendiri.""",
    "pentester": """\
Kamu Security Reviewer. Audit HANYA perubahan pada tiket ini, di dalam repo ini.
Kamu tidak boleh memindai, menguji, atau menyerang sistem apa pun di luar repo ini.
Jangan mengubah file.

Cari: input yang tidak divalidasi di batas kepercayaan, injeksi (SQL/command/path traversal),
secret yang ter-hardcode, authz yang hilang, error yang membocorkan informasi, dependency baru
yang mencurigakan.

Tiap temuan: severity (low/medium/high), file:baris, dampak konkret, perbaikan yang disarankan.

BERSIH (tak ada high/medium) → status: done, mention PM, summary berisi hasil audit.
ADA TEMUAN                    → status: in_progress, mention engineer, isi `tickets[]` satu per
                                temuan high/medium. Temuan low cukup di summary.""",
}


@dataclass
class AgentInfo:
    name: str
    role: str
    system_prompt: str | None = None


@dataclass
class TicketInfo:
    key: str
    title: str
    status: str
    priority: str
    description: str = ""


@dataclass
class CommentInfo:
    author: str
    body: str
    created_at: str


def _base_block(agent: AgentInfo, workspace_repo_path: str, team_roster: list[AgentInfo]) -> str:
    roster_lines = "\n".join(
        f"- {member.name} ({ROLE_LABELS.get(member.role, member.role)})" for member in team_roster
    )
    return f"""\
Kamu adalah {agent.name}, seorang {ROLE_LABELS.get(agent.role, agent.role)} di tim software \
yang bekerja di repo pada {workspace_repo_path}.

Kamu bekerja lewat sistem tiket. Aturan yang tidak bisa ditawar:
- Kerjakan HANYA tiket yang diberikan padamu. Jangan mengambil pekerjaan lain.
- Kalau kamu butuh orang lain, sebut mereka di `mention`. Jangan mengerjakan bagian mereka.
- Kalau kamu terjebak atau kekurangan informasi, gunakan status `blocked` dan jelaskan apa yang
  kamu butuhkan. Jangan menebak lalu melanjutkan.
- Ringkas. `summary` bukan esai.
- Kalau kamu selesai, berhenti. Jangan mencari pekerjaan tambahan.

Anggota tim di workspace ini:
{roster_lines}"""


def _ticket_context_block(
    ticket: TicketInfo,
    attachments: list[str],
    recent_comments: list[CommentInfo],
    previous_summaries: list[str],
) -> str:
    attachments_str = ", ".join(attachments) if attachments else "(tidak ada)"

    if recent_comments:
        comments_str = "\n".join(
            f"- {c.author} ({c.created_at}): {c.body}" for c in recent_comments[-5:]
        )
    else:
        comments_str = "(belum ada komentar)"

    if previous_summaries:
        summaries_str = "\n".join(f"- {s}" for s in previous_summaries)
    else:
        summaries_str = "(belum ada run sebelumnya)"

    return f"""\
Tiket saat ini:
{ticket.key} — {ticket.title}
Status: {ticket.status} | Prioritas: {ticket.priority}
{ticket.description}

Lampiran: {attachments_str}

Komentar terakhir:
{comments_str}

Hasil kerja sebelumnya di tiket ini:
{summaries_str}"""


def _anti_loop_block(review_round: int, previous_review_feedback: list[str]) -> str | None:
    if review_round < 1:
        return None
    feedback_str = (
        "\n".join(f"- {f}" for f in previous_review_feedback)
        if previous_review_feedback
        else "(tidak ada ringkasan)"
    )
    return f"""\
Ini review ke-{review_round} untuk tiket ini. Review sebelumnya:
{feedback_str}

Kalau masalah yang sama masih ada setelah dua kali diminta perbaiki, JANGAN meminta lagi.
status: blocked, dan jelaskan kenapa perbaikannya tidak berhasil."""


def _map_contract_block(agent: AgentInfo, team_roster: list[AgentInfo]) -> str:
    allowed_statuses = ", ".join(sorted(ROLE_ALLOWED_STATUSES.get(agent.role, frozenset())))
    mention_names = ", ".join(m.name for m in team_roster)

    tickets_line = ""
    if agent.role in ROLES_ALLOWED_TICKETS:
        tickets_line = """
tickets:                    # opsional; breakdown atau bug/temuan baru
  - title: <judul>
    description: |
      <detail>
    assignee: <nama agent>
    priority: <low|medium|high|urgent>
updates:                    # opsional; ubah tiket LAIN yang sudah ada (bukan bikin baru)
  - ticket: <KEY-123>
    status: <opsional>
    priority: <opsional, low|medium|high|urgent>
    assignee: <opsional, nama agent>"""

    return f"""\
Akhiri jawabanmu dengan TEPAT SATU blok berikut. Tanpa blok ini pekerjaanmu dianggap gagal
dan tiket akan diblokir.

```map
status: <salah satu dari: {allowed_statuses}>
mention: [<nama agent dari daftar tim: {mention_names}>]
summary: |
  <apa yang kamu kerjakan, file apa yang tersentuh, dan bukti bahwa itu jalan>{tickets_line}
```"""


def build_prompt(
    agent: AgentInfo,
    workspace_repo_path: str,
    team_roster: list[AgentInfo],
    ticket: TicketInfo,
    attachments: list[str] | None = None,
    recent_comments: list[CommentInfo] | None = None,
    previous_summaries: list[str] | None = None,
    review_round: int = 0,
    previous_review_feedback: list[str] | None = None,
    extra_instructions: str | None = None,
) -> str:
    """Assemble a full agent prompt: BASE + role block + extra_instructions (if any) +
    ticket context + anti-loop + ```map contract.

    docs/02-tsd.md §4.4 assembly order. `agent.system_prompt`, if set, replaces
    only the role block (BASE and the ```map contract are always present).
    `extra_instructions` is an optional caller-supplied block (e.g. the
    mention-triggered PM chat hint from orchestrator.py) inserted right after the
    role block; omitted entirely when None so existing output is unchanged.
    """
    attachments = attachments or []
    recent_comments = recent_comments or []
    previous_summaries = previous_summaries or []
    previous_review_feedback = previous_review_feedback or []

    parts = [_base_block(agent, workspace_repo_path, team_roster)]

    role_block = agent.system_prompt.strip() if agent.system_prompt else None
    if not role_block:
        role_block = DEFAULT_ROLE_PROMPTS.get(agent.role, "")
    parts.append(role_block)

    if extra_instructions:
        parts.append(extra_instructions)

    parts.append(_ticket_context_block(ticket, attachments, recent_comments, previous_summaries))

    if agent.role in REVIEWER_ROLES:
        anti_loop = _anti_loop_block(review_round, previous_review_feedback)
        if anti_loop:
            parts.append(anti_loop)

    parts.append(_map_contract_block(agent, team_roster))

    return "\n\n".join(parts)
