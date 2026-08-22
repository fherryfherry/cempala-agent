# PRD — Multi-Agent Portal (MAP)

Versi 0.2 · MVP · 2026-08-22

> **Perubahan v0.2.** Portal tidak membangun coding agent sendiri. Setiap agent adalah satu proses
> `opencode` yang menerima prompt dan mengembalikan hasil lewat blok ```map. Yang kita bangun:
> tiket, orkestrasi, guardrail, visibilitas. ([06-adr.md](06-adr.md) ADR-006)

## 1. Problem

Menjalankan beberapa agent AI untuk satu projek hari ini berarti: banyak terminal terpisah,
tiap agent tidak tahu agent lain sedang apa, tidak ada backlog bersama, dan tidak ada jejak
keputusan. Hasilnya kerja tumpang tindih, konteks hilang, dan tidak ada cara memantau progres
selain membaca scrollback.

## 2. Goal

Satu portal lokal di mana tim agent AI dengan peran berbeda mengerjakan backlog tiket secara
kolaboratif dan otonom di dalam repo kode nyata, dengan visibilitas real-time penuh bagi owner.

### Success criteria MVP

- Owner bisa membuat workspace yang menunjuk ke folder repo lokal, menambah ≥4 agent dengan
  role & model berbeda, membuat 1 tiket epic, menekan "Run", lalu **tanpa intervensi lagi**
  epic itu terpecah jadi sub-tiket, dikerjakan, direview, dan berakhir `done` atau `blocked`.
- Selama itu berlangsung, owner melihat siapa mengerjakan apa secara live.
- Owner bisa menghentikan semuanya dalam 1 klik.

### Non-goal MVP

Kualitas hasil kode belum jadi target — itu urusan opencode dan model yang kamu pilih.
Yang diuji: alurnya jalan, terpantau, dan bisa dihentikan.

## 3. Persona

**Owner** (satu orang, pemilik mesin). Developer. Ingin mendelegasikan pekerjaan ke tim agent
dan memantau, bukan mengetik prompt satu per satu. Menjalankan aplikasi di laptopnya sendiri.

**Agent roles** (bukan pengguna, tapi aktor sistem):

| Role | Tanggung jawab | Boleh banyak? |
|---|---|---|
| PM | Breakdown epic jadi sub-tiket, prioritas, assign, tutup tiket | Tidak (1 per workspace) |
| Lead Engineer | Review hasil Engineer, arahan teknis, approve/reject | Tidak (1 per workspace) |
| Engineer | Implementasi kode | Ya |
| Designer | Spec UI, asset, markdown desain | Ya |
| QA | Tulis & jalankan test, laporkan bug | Ya |
| Pentester | Audit keamanan, laporan temuan | Ya |

## 4. Scope MVP — Epic & User Story

### E1 — Workspace

- **US-1.1** Sebagai owner saya bisa membuat workspace dengan nama, key tiket (mis. `MAP`),
  dan `repo_path` ke folder lokal.
  - AC: path divalidasi ada & merupakan direktori; kalau tidak, form menolak dengan pesan jelas.
  - AC: key tiket unik, 2–5 huruf kapital.
- **US-1.2** Saya bisa punya banyak workspace dan berpindah di antaranya.
  - AC: switcher di header; data tiket/agent/feed terfilter per workspace.
- **US-1.3** Saya bisa mengedit dan menghapus workspace.
  - AC: hapus workspace menghapus agent, tiket, run, event miliknya (cascade). Folder repo di
    disk **tidak** disentuh.

### E2 — Setup Agent

- **US-2.1** Saya bisa menambah agent: nama, role, model, coding tool, system prompt (opsional override).
  - AC: daftar model diambil dari `opencode models` (format `provider/model`); kalau gagal,
    tampilkan error yang menyarankan `opencode auth login` + field teks manual.
  - AC: coding tool = `opencode` (aktif), `claude` | `agy` | `codex` (tampil disabled dengan
    label "coming soon").
  - AC: system prompt kosong → pakai default per role dari [03-agent-design.md](03-agent-design.md).
- **US-2.2** Saya bisa edit / hapus / nonaktifkan (`enabled: false`) agent.
  - AC: agent nonaktif tidak pernah dipilih oleh orchestrator dan tidak bisa di-assign.
  - AC: hapus agent yang punya run aktif ditolak; harus di-stop dulu.
- **US-2.3** Saya bisa melihat status tiap agent: `idle` / `working` / `error` / `disabled`,
  beserta tiket yang sedang dikerjakan.

### E3 — Ticketing

- **US-3.1** Saya bisa membuat tiket dengan judul, deskripsi (markdown), prioritas, assignee.
  - AC: key otomatis `<WORKSPACE_KEY>-<n>` berurutan, tidak pernah dipakai ulang.
- **US-3.2** Tiket bisa punya parent (epic → sub-tiket), satu level saja di MVP.
- **US-3.3** Saya bisa melampirkan file ke tiket.
  - AC: disimpan di `storage/attachments/<ticket_id>/`, bukan di dalam repo workspace.
  - AC: batas ukuran 25 MB/file; nama file disanitasi.
- **US-3.4** Saya bisa melihat board kanban per status dan drag tiket antar kolom.
- **US-3.5** Tiket punya riwayat perubahan status yang terlihat di detail tiket.

### E4 — Kolaborasi

- **US-4.1** Saya (atau agent) bisa berkomentar di tiket.
- **US-4.2** Komentar bisa mention agent lain dengan `@nama-agent`.
  - AC: autocomplete di composer dari daftar agent workspace itu.
  - AC: mention menyimpan `agent_id`, bukan sekadar teks.
- **US-4.3** Mention memicu run untuk agent yang di-mention.
  - AC: kena guardrail depth & budget seperti run lainnya (lihat E6).
  - AC: agent tidak bisa memicu dirinya sendiri.

### E5 — Agent Runtime

- **US-5.1** Saya bisa menjalankan agent pada tiket secara manual ("Run" di detail tiket).
- **US-5.2** Satu run = satu proses `opencode` di `repo_path`; seluruh output-nya jadi event.
  - AC: prompt dirakit dari system prompt role + isi tiket + komentar + hasil run sebelumnya.
  - AC: attachment tiket ikut dilewatkan ke opencode.
  - AC: agent yang kembali ke tiket yang sama melanjutkan session opencode sebelumnya.
- **US-5.3** Agent melapor balik lewat blok ```map di akhir jawabannya (`status`, `mention`,
  `summary`, `tickets[]`), dan portal mengeksekusinya.
  - AC: `summary` jadi komentar tiket atas nama agent itu.
  - AC: `status` divalidasi state machine **dan** hak role; ilegal → ditolak.
  - AC: hanya PM, QA, Pentester yang boleh membuat tiket lewat `tickets[]`.
  - AC: blok hilang atau rusak → tiket `blocked` + komentar sistem berisi potongan output asli
    agent. Tidak pernah ditebak, tidak pernah diam.
- **US-5.4** Alur otonom: tiket epic → PM breakdown & assign → Engineer/Designer kerja →
  Lead review → QA → Pentester → PM tutup.
  - AC: transisi hanya sesuai state machine di [03-agent-design.md](03-agent-design.md).
- **US-5.5** Run yang gagal (exit non-zero, timeout, budget habis, blok map rusak) menandai tiket
  `blocked` dan menulis komentar berisi alasan. Tidak diam-diam mati.
- **US-5.6** Agent bertool `claude`/`agy`/`codex` menghasilkan run `failed` dengan pesan
  "adapter belum tersedia", bukan error 500.

### E6 — Guardrail & Kill Switch

- **US-6.1** Global kill switch per workspace: satu klik menghentikan semua run aktif.
  - AC: run dibatalkan, **proses opencode benar-benar mati** (diverifikasi `ps`, bukan sekadar
    status di DB), status agent kembali `idle`.
  - AC: workspace masuk mode `paused`; tidak ada run baru sampai di-resume.
- **US-6.2** Batas per run: timeout dan budget biaya.
- **US-6.3** Batas per tiket: budget biaya total dan max depth handoff.
- **US-6.4** Loop detector: tiket yang berpindah bolak-balik antar dua agent lebih dari N kali
  → tiket `blocked` + komentar sistem.
- **US-6.5** Saya bisa melihat & mengubah nilai guardrail per workspace di halaman setting.
- **US-6.6** Batas run bersamaan per workspace (default 3) — tiap run adalah proses opencode penuh.

### E7 — Realtime Monitoring

- **US-7.1** Activity feed live per workspace: siapa, tiket apa, sedang apa.
- **US-7.2** Saya bisa membuka satu run dan melihat streaming output opencode + tool call-nya,
  plus blok ```map hasil parse (atau alasan kenapa gagal di-parse).
- **US-7.3** Feed bertahan setelah refresh (dibaca ulang dari tabel `event`).
- **US-7.4** Saya bisa stop satu run tertentu tanpa mematikan yang lain.

## 5. Out of scope MVP

Coding agent buatan sendiri (loop tool-calling, tool filesystem) · Adapter claude/agy/codex ·
Sandbox/container untuk opencode · MCP server berisi tool ticketing · Auth, multi-user, RBAC ·
Operasi git (branch, commit, PR) · Integrasi GitHub/Slack/Linear · Sub-tiket lebih dari 1 level ·
Deployment/CI · Mobile view · Notifikasi · Search full-text.

## 6. Requirement non-fungsional

| Aspek | Target |
|---|---|
| Deployment | Lokal, satu mesin, dua proses (`uvicorn` + `next dev`) lewat `make dev` |
| Prasyarat | Binary `opencode` terpasang dan sudah `opencode auth login`. Portal memeriksa ini di `/api/health`. |
| Skala | ≤5 workspace, ≤3 run bersamaan per workspace, ≤5.000 tiket |
| Realtime | Event tampil di UI <1 detik sejak terjadi |
| Persistensi | Semua state di SQLite. Restart backend tidak menghilangkan tiket/komentar/event. Run aktif saat restart ditandai `interrupted`. |
| Keamanan | Backend bind `127.0.0.1`, tidak boleh terjangkau jaringan. Portal tidak menyimpan kredensial LLM. Attachment disimpan di luar `repo_path`. **Filesystem agent tidak di-sandbox** — lihat §7 dan [02-tsd.md](02-tsd.md) §7. |
| Observability | Setiap run bisa direplay penuh dari tabel `event`, termasuk prompt yang dikirim |

## 7. Risiko

| Risiko | Mitigasi |
|---|---|
| **Model tidak patuh format blok ```map** — risiko terbesar MVP | Format dibuat sesederhana mungkin, kontrak diulang di akhir tiap prompt, kegagalan parse selalu memblokir tiket dengan output aslinya. Tingkat kepatuhan diukur di dogfood (MAP-033); kalau buruk → pindah ke MCP server ([06-adr.md](06-adr.md) ADR-009) |
| **Agent bisa menjalankan perintah apa pun di mesinmu** (`--auto`, tanpa sandbox) | Diterima sadar ([06-adr.md](06-adr.md) ADR-010). Backend localhost-only, peringatan eksplisit di README + halaman settings, kill switch yang benar-benar mematikan proses |
| Agent otonom saling lempar tiket tanpa henti | Loop detector + max depth + budget biaya per tiket (E6) |
| Biaya membengkak diam-diam | Budget per run & per tiket dari data biaya opencode, terlihat di UI; `max_concurrent_runs` default 3 |
| Tergantung penuh pada satu binary eksternal | Konsekuensi ADR-006 yang diterima. `/api/health` melaporkan versi opencode; perubahan format `--format json` akan terlihat sebagai kegagalan parsing, bukan sebagai data yang salah |
| Model menghasilkan kode buruk | Di luar scope MVP; itu tugas Lead/QA di iterasi berikutnya |
