"""Prompt assembly for agent runs (docs/02-tsd.md §4.4, docs/03-agent-design.md §1-2,4,7).

Pure Python — no DB/HTTP/ORM imports. Takes plain data in, returns a prompt
string out, so it's unit-testable and callable from both the opencode
adapter (MAP-020) and the orchestrator (MAP-023) without a DB session.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.report import AGENT_DECLARABLE_STATUSES, ROLES_ALLOWED_TICKETS

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
Kamu Project Manager. Kamu TIDAK menulis atau mengubah kode/test. Kamu BOLEH menulis dokumen
perencanaan (PRD) di repo — tidak lebih.

Kalau tiket ini epic (belum punya sub-tiket):
1. Baca repo secukupnya untuk paham konteks (termasuk konvensi folder dokumen kalau sudah ada).
2. Cek katalog epic yang sudah ada di kontrak ```map di bawah — kalau permintaan ini sebenarnya
   bagian dari epic lain yang sudah ada, isi `epic:` di tiap `tickets[]` untuk menempel ke epic
   itu (JANGAN bikin epic baru untuk area fitur yang sudah ada). Epic adalah area fitur besar
   yang dipakai berkali-kali sebagai parent untuk tiket baru ke depannya — bukan container
   sekali pakai per request.
3. Tulis PRD singkat sebagai file markdown di repo: tujuan, lingkup, acceptance criteria per
   sub-tiket. Deklarasikan file ini lewat `artifacts:` (group mis. "Dokumen Teknis").
4. Pecah jadi 3-8 sub-tiket lewat `tickets[]`. Tiap sub-tiket harus bisa diselesaikan satu agent
   dalam satu sesi kerja, dan punya acceptance criteria yang bisa dicek.
5. Assign tiap sub-tiket ke agent yang paling cocok berdasarkan role-nya.
6. status: in_progress. Berhenti — sub-tiket akan dikerjakan sendiri oleh agent yang kamu assign.

Kalau tiket ini punya sub-tiket dan SEMUANYA done: status: done — KECUALI epic ini memang area
fitur besar yang masih akan menerima tiket baru lagi ke depannya, dalam kasus itu boleh tetap di
status yang mencerminkan keadaannya (mis. in_progress), tidak wajib done.
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
4. Tulis file evidence singkat (apa yang dijalankan, jumlah lolos/gagal, kasus tepi yang dicoba)
   dan deklarasikan lewat `artifacts:` (group mis. "Hasil Testing").

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


@dataclass
class WorkspaceTicketSummary:
    key: str
    title: str
    status: str
    priority: str
    sprint_name: str | None = None
    assignee: str | None = None
    updated_at: str | None = None


def _workspace_tickets_block(tickets: list[WorkspaceTicketSummary]) -> str:
    lines = [
        f"- {t.key} [{t.status}] (sprint: {t.sprint_name or 'tanpa sprint'}) — {t.title}"
        for t in tickets
    ]
    return "Tiket lain di workspace ini (untuk konteks/review):\n" + "\n".join(lines)


def _workspace_tickets_catalog_block(tickets: list[WorkspaceTicketSummary]) -> str | None:
    """Ticket board snapshot for routine runs: status, assignee, last-updated time.
    The agent reads/staleness-checks from THIS list — never from the repo.
    """
    if not tickets:
        return None
    lines = []
    for t in tickets:
        parts = [f"[{t.status}]"]
        if t.assignee:
            parts.append(f"assignee: {t.assignee}")
        if t.updated_at:
            parts.append(f"updated: {t.updated_at}")
        lines.append(f"- {t.key} {' '.join(parts)} — {t.title}")
    return f"""\
DAFTAR TIKET DI WORKSPACE INI (menu Board — sumber kebenaran status/umur tiket, BUKAN repo):
{chr(10).join(lines)}"""


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

Format penulisan jawabanmu:
- Selalu terstruktur: pakai pointer/bullet singkat dan sub-judul, jangan paragraf rata yang
  panjang. Satu ide = satu baris pointer.
- Emoji tipis boleh untuk memperjelas (maksimal beberapa), jangan berlebihan.
- Kalau kamu menulis laporan/file markdown di repo, ikuti format yang sama: pointer, rapi, ringkas.

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


def _agent_memory_block(memories: list[str]) -> str | None:
    if not memories:
        return None
    notes_str = "\n".join(f"- {m}" for m in memories)
    return f"""\
Catatan dari pekerjaanmu sebelumnya (lintas tiket) — hindari mengulang ini:
{notes_str}"""


def _artifacts_catalog_block(catalog: list[str]) -> str | None:
    if not catalog:
        return None
    lines = "\n".join(f"- {line}" for line in catalog)
    return f"""\
Artifacts di workspace ini (menu Artifacts) — baca/cari di sini sebelum membuat file baru:
{lines}"""


def _mcp_tools_block() -> str:
    return """\
Kamu punya tool MCP untuk berinteraksi dengan sistem tiket (menu Board) — pakai ini untuk
melihat/mengubah tiket, artifacts, dan memory, BUKAN mencarinya di repo:
- list_tickets — daftar semua tiket workspace (key, status, prioritas, assignee, waktu update); sumber kebenaran status tiket.
- get_ticket(key) — detail tiket (deskripsi, komentar, sub-tiket).
- post_comment(key, body) — tulis komentar follow-up ke tiket.
- create_ticket(title, description, priority) — buat tiket backlog baru.
- update_ticket(key, status, priority) — ubah status/prioritas tiket.
- list_artifacts / read_artifact(attachment_id) — baca file yang sudah dipublikasikan agent.
- get_memory / create_memory(note) / update_memory(memory_id, note) — catatan memory lintas tiket milikmu.

Kalau tugasmu membutuhkan informasi tiket, SELALU pakai tool ini — jangan menebak status
atau umur tiket."""


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


_UNIT_LABELS = {"hour": "jam", "day": "hari"}


def _epic_reuse_rule(existing_epics: list[str]) -> str:
    """Reuse-guidance text for `tickets[].epic` — mirrors `groups_rule` below exactly

    (docs/03-agent-design.md §3): list what already exists, mandate reusing a relevant
    one, only allow inventing new when nothing fits. Epics are meant to be persistent
    feature-area containers reused across many future tickets, not one-off per request.
    """
    if existing_epics:
        epics_str = "\n".join(f"    - {e}" for e in existing_epics)
        return (
            f"Epic yang SUDAH ADA (tiket top-level) di workspace ini:\n"
            f"{epics_str}\n"
            f"    WAJIB isi `epic:` dengan key yang relevan kalau area fiturnya cocok "
            f"(cocokkan tujuannya, bukan judul persis). Kosongkan `epic:` HANYA kalau ini "
            f"benar-benar area fitur besar baru yang belum ada di daftar — tiket ini "
            f"sendiri akan jadi epic baru."
        )
    return "Belum ada epic di workspace ini — tiket tanpa `epic:` akan jadi epic pertama."


def _sprint_reuse_rule(existing_sprints: list[str]) -> str:
    """Reuse-guidance text for `sprints:`/`tickets[].sprint` — same pattern as

    `_epic_reuse_rule`, plus the explicit sprint-is-not-scope rule (owner request):
    sprint is a pure timebox, never a place to put a feature/scope name — that's what
    `epic` is for.
    """
    if existing_sprints:
        sprints_str = "\n".join(f"    - {s}" for s in existing_sprints)
        return (
            f"Sprint yang SUDAH ADA:\n{sprints_str}\n"
            f"    WAJIB pakai nama yang sudah ada (persis) kalau timebox itu masih "
            f"relevan. Sprint HANYA timebox — JANGAN taruh nama fitur/scope di nama "
            f"sprint (itu urusan `epic`); pola nama disarankan 'Sprint 1', 'Sprint 2', "
            f"dst. `goal` boleh diisi target singkat sprint itu, bukan nama fitur."
        )
    return (
        "Belum ada sprint di workspace ini. Sprint HANYA timebox (pola nama disarankan "
        "'Sprint 1', 'Sprint 2', dst) — JANGAN taruh nama fitur/scope di nama sprint, "
        "itu urusan `epic`."
    )


def _map_contract_block(
    agent: AgentInfo,
    team_roster: list[AgentInfo],
    time_unit: str,
    existing_artifact_groups: list[str],
    sprint_creator_roles: set[str] | None = None,
    existing_epics: list[str] | None = None,
    existing_sprints: list[str] | None = None,
) -> str:
    allowed_statuses = ", ".join(sorted(AGENT_DECLARABLE_STATUSES))
    mention_names = ", ".join(m.name for m in team_roster)
    unit_label = _UNIT_LABELS.get(time_unit, time_unit)
    allowed_sprint_roles = sprint_creator_roles or {"pm"}
    existing_epics = existing_epics or []
    existing_sprints = existing_sprints or []

    if existing_artifact_groups:
        groups_str = "\n".join(f"    - {g}" for g in existing_artifact_groups)
        groups_rule = (
            f"Kelompok yang SUDAH ADA di menu Artifacts workspace ini:\n"
            f"{groups_str}\n"
            f"    WAJIB pakai salah satu kelompok di atas yang relevan (cocokkan tujuannya, "
            f"abaikan beda kapital/spasi). JANGAN bikin nama baru kalau ada yang relevan — "
            f"bikin baru HANYA kalau tidak ada satu pun yang cocok."
        )
    else:
        groups_rule = (
            "Belum ada kelompok di menu Artifacts workspace ini — kamu boleh membuat "
            "kelompok pertama."
        )

    tickets_line = ""
    if agent.role in ROLES_ALLOWED_TICKETS:
        sprints_line = ""
        if agent.role in allowed_sprint_roles:
            sprint_rule = _sprint_reuse_rule(existing_sprints)
            sprints_line = f"""
sprints:                    # opsional; deklarasikan/update sprint (timebox, BUKAN nama fitur)
  # ATURAN SPRINT: {sprint_rule}
  - name: <nama sprint, mis. "Sprint 1">
    goal: <target/goal singkat sprint ini — bukan nama fitur>
    duration: <estimasi durasi sprint dalam {unit_label}>"""
        epic_rule = _epic_reuse_rule(existing_epics)
        tickets_line = f"""
tickets:                    # opsional; breakdown atau bug/temuan baru
  # judul harus rapi & mudah dibaca non-teknis: JANGAN cantumkan path file, nama
  # fungsi/variabel, potongan kode, atau nomor tiket lain di title — detail teknis itu
  # masuk ke `description`, bukan title.
  # ATURAN EPIC: {epic_rule}
  - title: <judul ringkas, bahasa manusia>
    description: |
      <detail>
    assignee: <nama agent>
    priority: <low|medium|high|urgent>
    epic: <opsional, key epic tujuan dari daftar di atas — kosongkan HANYA untuk epic baru>
    sprint: <opsional, nama sprint dari daftar `sprints` di atas>
    duration: <opsional, estimasi durasi tiket ini dalam {unit_label}>
updates:                    # opsional; ubah tiket LAIN yang sudah ada (bukan bikin baru)
  - ticket: <KEY-123>
    status: <opsional>
    priority: <opsional, low|medium|high|urgent>
    assignee: <opsional, nama agent>
    sprint: <opsional, pindahkan tiket ini ke sprint lain>
    duration: <opsional, perbaiki estimasi durasi tiket ini dalam {unit_label}>{sprints_line}"""

    artifact_updates_line = ""
    if agent.role == "pm":
        artifact_updates_line = f"""
artifact_updates:           # opsional; HANYA PM — rapikan kelompok di menu Artifacts
  # Cek daftar Artifacts di atas dulu. Nama kelompok harus persis dari daftar itu.
  # op: rename | merge | move | delete
  - op: rename
    group: <nama kelompok lama>
    to: <nama kelompok baru>
  - op: merge
    from: <kelompok sumber, akan dihapus setelah digabung>
    into: <kelompok tujuan>
  - op: move
    group: <kelompok asal>
    file: <nama file yang dipindah>
    to: <kelompok tujuan>
  - op: delete
    group: <kelompok kosong — hanya boleh jika tidak ada file di dalamnya>"""

    return f"""\
Akhiri jawabanmu dengan TEPAT SATU blok berikut. Tanpa blok ini pekerjaanmu dianggap gagal
dan tiket akan diblokir.

```map
status: <salah satu dari: {allowed_statuses}>
mention: [<nama agent dari daftar tim: {mention_names}>]
summary: |
  <apa yang kamu kerjakan, file apa yang tersentuh, dan bukti bahwa itu jalan>{tickets_line}
artifacts:                  # opsional; file yang kamu hasilkan di repo ini, akan tampil di menu Artifacts
  # ATURAN KELOMPOK: cek daftar di bawah dulu sebelum menulis `group`.
  # {groups_rule}
  - path: <path file relatif ke root repo, mis. "docs/PRD.md">
    group: <nama kelompok dari daftar yang sudah ada, atau nama baru yang jelas>
    description: <opsional, ringkas>
memory:                     # opsional; catatan singkat yang mau kamu ingat lintas tiket
  # dipakai untuk hal yang jangan diulang lagi (kesalahan/kegagalan), bukan ringkasan kerja
  # biasa — summary di atas sudah menutupi itu. Satu kalimat per catatan.
  - <catatan singkat>{artifact_updates_line}
```"""


def build_routine_prompt(
    agent: AgentInfo,
    workspace_repo_path: str,
    team_roster: list[AgentInfo],
    routine_prompt: str,
    extra_instructions: str | None = None,
    agent_memories: list[str] | None = None,
    artifact_catalog: list[str] | None = None,
    sprint_creator_roles: set[str] | None = None,
    existing_epics: list[str] | None = None,
    existing_sprints: list[str] | None = None,
) -> str:
    """Assemble a routine-run prompt (no ticket): BASE + role block + the routine's
    own task prompt + workspace context + artifact catalog + agent memory + a
    routine-specific ```map contract (side-effect actions only — no status/mention).

    The routine contract teaches `comments:` (comment on other tickets), `tickets[]`
    (backlog, not auto-scheduled), `updates:`, `memory:`, and `artifact_updates:`
    (PM only). `status`/`mention` are deliberately absent — the parser rejects them
    in routine mode.
    """
    agent_memories = agent_memories or []
    artifact_catalog = artifact_catalog or []
    allowed_sprint_roles = sprint_creator_roles or {"pm"}
    existing_epics = existing_epics or []
    existing_sprints = existing_sprints or []

    parts = [_base_block(agent, workspace_repo_path, team_roster)]

    role_block = agent.system_prompt.strip() if agent.system_prompt else None
    if not role_block:
        role_block = DEFAULT_ROLE_PROMPTS.get(agent.role, "")
    parts.append(role_block)

    parts.append(f"TUGAS RUTINITAS (bukan tiket biasa — tidak ada tiket yang sedang dikerjakan):\n\n{routine_prompt.strip()}")

    if extra_instructions:
        parts.append(extra_instructions)

    memory_block = _agent_memory_block(agent_memories)
    if memory_block:
        parts.append(memory_block)

    catalog_block = _artifacts_catalog_block(artifact_catalog)
    if catalog_block:
        parts.append(catalog_block)

    # MCP tools (ADR-011) — routine runs are exactly where the agent needs to
    # read the Board and write follow-up comments via tools, not the repo.
    parts.append(_mcp_tools_block())

    parts.append(
        _routine_contract_block(agent, team_roster, allowed_sprint_roles, existing_epics, existing_sprints)
    )

    return "\n\n".join(parts)


def _routine_contract_block(
    agent: AgentInfo,
    team_roster: list[AgentInfo],
    allowed_sprint_roles: set[str],
    existing_epics: list[str] | None = None,
    existing_sprints: list[str] | None = None,
) -> str:
    existing_epics = existing_epics or []
    existing_sprints = existing_sprints or []
    mention_names = ", ".join(m.name for m in team_roster)
    tickets_line = ""
    if agent.role in ROLES_ALLOWED_TICKETS:
        sprints_line = ""
        if agent.role in allowed_sprint_roles:
            sprint_rule = _sprint_reuse_rule(existing_sprints)
            sprints_line = f"""
sprints:                    # opsional; deklarasikan/update sprint (timebox, BUKAN nama fitur)
  # ATURAN SPRINT: {sprint_rule}
  - name: <nama sprint, mis. "Sprint 1">
    goal: <target/goal singkat sprint ini — bukan nama fitur>
    duration: <estimasi durasi sprint>"""
        epic_rule = _epic_reuse_rule(existing_epics)
        tickets_line = f"""
tickets:                    # opsional; tiket backlog baru (status todo, TIDAK otomatis dijalankan)
  # ATURAN EPIC: {epic_rule}
  - title: <judul ringkas, bahasa manusia>
    description: |
      <detail>
    assignee: <opsional, nama agent>
    priority: <low|medium|high|urgent>
    epic: <opsional, key epic tujuan dari daftar di atas — kosongkan HANYA untuk epic baru>
    sprint: <opsional, nama sprint dari daftar `sprints` di atas>
    duration: <opsional, estimasi durasi tiket ini>{sprints_line}"""

    artifact_updates_line = ""
    if agent.role == "pm":
        artifact_updates_line = """
artifact_updates:           # opsional; HANYA PM — rapikan kelompok di menu Artifacts
  - op: rename
    group: <nama kelompok lama>
    to: <nama kelompok baru>
  - op: merge
    from: <kelompok sumber>
    into: <kelompok tujuan>
  - op: move
    group: <kelompok asal>
    file: <nama file>
    to: <kelompok tujuan>
  - op: delete
    group: <kelompok kosong>"""

    return f"""\
Akhiri jawabanmu dengan TEPAT SATU blok berikut. Tanpa blok ini pekerjaanmu dianggap gagal.

```map
summary: |
  <ringkasan singkat apa yang kamu lakukan>
comments:                   # opsional; komen ke tiket LAIN di workspace ini
  - ticket: <KEY-123>
    body: |
      <isi komentar>
updates:                    # opsional; ubah tiket LAIN yang sudah ada (bukan bikin baru)
  - ticket: <KEY-123>
    status: <opsional>
    priority: <opsional, low|medium|high|urgent>
    assignee: <opsional, nama agent>
    sprint: <opsional, pindahkan tiket ini ke sprint lain>
    duration: <opsional, perbaiki estimasi durasi tiket ini>{tickets_line}
memory:                     # opsional; catatan singkat yang mau kamu ingat lintas tiket
  - <catatan singkat>{artifact_updates_line}
```

ATURAN PENTING:
- Kamu TIDAK boleh mendeklarasikan `status` atau `mention` — run ini tidak punya tiket.
- `tickets[]` yang kamu buat jadi tiket backlog (todo) dan TIDAK otomatis dijalankan.
- `comments:` hanya untuk tiket yang sudah ada di workspace ini.
- Nama agent yang bisa di-mention di komentar: {mention_names}"""


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
    time_unit: str = "day",
    workspace_tickets: list[WorkspaceTicketSummary] | None = None,
    existing_artifact_groups: list[str] | None = None,
    agent_memories: list[str] | None = None,
    artifact_catalog: list[str] | None = None,
    sprint_creator_roles: set[str] | None = None,
    existing_epics: list[str] | None = None,
    existing_sprints: list[str] | None = None,
) -> str:
    """Assemble a full agent prompt: BASE + role block + extra_instructions (if any) +
    agent memory (if any) + ticket context + workspace tickets (if any) + artifact
    catalog (if any) + anti-loop + ```map contract.

    docs/02-tsd.md §4.4 assembly order. `agent.system_prompt`, if set, replaces
    only the role block (BASE and the ```map contract are always present).
    `extra_instructions` is an optional caller-supplied block (e.g. the
    mention-triggered PM chat hint from orchestrator.py) inserted right after the
    role block; omitted entirely when None so existing output is unchanged.
    `workspace_tickets`, when non-empty, is a snapshot of the rest of the
    workspace's tickets (orchestrator.py only supplies this for PM owner-chat
    runs) so PM can review/fix sprint assignment across existing tickets, not
    just the one it's currently on.
    `existing_artifact_groups` lists the artifact group names already present in
    the workspace's Artifacts menu; the agent must reuse a relevant one instead
    of inventing near-duplicate group names.
    `agent_memories` is this agent's own cross-ticket notes (```map `memory:`,
    docs/03-agent-design.md §3) — most-recent-first callers should reverse to
    chronological before passing in, same convention as `previous_summaries`.
    `artifact_catalog` is a pre-formatted list of the workspace's artifacts
    (one string per artifact, e.g. "[Dokumen Teknis] PRD.md (MAP-001) — initial
    PRD") so every agent can read/search what's already been published before
    producing new files.
    `sprint_creator_roles` is the per-workspace set of roles allowed to declare
    `sprints:` (Settings page pill picker); the contract only teaches the field
    to those roles. Defaults to {"pm"}.
    `existing_epics` lists the workspace's existing top-level tickets ("KEY — title")
    so PM/QA/Pentester reuse a relevant epic via `tickets[].epic` instead of spawning
    a fresh one-off epic every time (docs/03-agent-design.md §3). `existing_sprints`
    lists existing sprint names for the same reuse treatment — sprints are pure
    timeboxes, never a place for feature/scope names (that's what `epic` is for).
    """
    attachments = attachments or []
    recent_comments = recent_comments or []
    previous_summaries = previous_summaries or []
    previous_review_feedback = previous_review_feedback or []
    workspace_tickets = workspace_tickets or []
    existing_artifact_groups = existing_artifact_groups or []
    agent_memories = agent_memories or []
    artifact_catalog = artifact_catalog or []
    sprint_creator_roles = sprint_creator_roles or {"pm"}
    existing_epics = existing_epics or []
    existing_sprints = existing_sprints or []

    parts = [_base_block(agent, workspace_repo_path, team_roster)]

    role_block = agent.system_prompt.strip() if agent.system_prompt else None
    if not role_block:
        role_block = DEFAULT_ROLE_PROMPTS.get(agent.role, "")
    parts.append(role_block)

    if extra_instructions:
        parts.append(extra_instructions)

    # MCP tools (ADR-011) — the agent can read/write tickets, artifacts, memory via
    # tools; tell it explicitly so it doesn't try to find ticket state in the repo.
    parts.append(_mcp_tools_block())

    memory_block = _agent_memory_block(agent_memories)
    if memory_block:
        parts.append(memory_block)

    parts.append(_ticket_context_block(ticket, attachments, recent_comments, previous_summaries))

    if workspace_tickets:
        parts.append(_workspace_tickets_block(workspace_tickets))

    catalog_block = _artifacts_catalog_block(artifact_catalog)
    if catalog_block:
        parts.append(catalog_block)

    if agent.role in REVIEWER_ROLES:
        anti_loop = _anti_loop_block(review_round, previous_review_feedback)
        if anti_loop:
            parts.append(anti_loop)

    parts.append(
        _map_contract_block(
            agent,
            team_roster,
            time_unit,
            existing_artifact_groups,
            sprint_creator_roles,
            existing_epics,
            existing_sprints,
        )
    )

    return "\n\n".join(parts)
