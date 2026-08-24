# TSD — Multi-Agent Portal (MAP)

Versi 0.2 · MVP · 2026-08-22
Pendamping: [01-prd.md](01-prd.md) · [03-agent-design.md](03-agent-design.md) · [06-adr.md](06-adr.md)

> **Perubahan v0.2.** Coding agent tidak lagi dibangun sendiri. Tidak ada `self` tool, tidak ada
> loop tool-calling, tidak ada tool filesystem. Portal ini **hanya** merakit prompt, melempar ke
> coding tool eksternal (opencode), dan menerima hasilnya. Yang kita bangun: tiket, orkestrasi,
> guardrail, dan visibilitas. ([ADR-006](06-adr.md))

## 1. Arsitektur

```
┌──────────────────────────────┐
│ Next.js (App Router)         │  :3000
│ React Query · Tailwind       │
│ shadcn/ui · EventSource      │
└──────────┬───────────────────┘
           │ REST + SSE (http://localhost:8000)
┌──────────▼───────────────────┐
│ FastAPI                      │  :8000
│  api/      routers           │
│  core/     orchestrator      │──► asyncio.Task per run
│  agents/   prompt + adapter  │
│  db/       SQLAlchemy        │
└──────────┬───────────────────┘
           │ subprocess
    ┌──────▼──────────────────────────┐
    │ opencode run --format json      │──► LLM (provider diatur opencode)
    │   --dir <repo_path> --auto      │──► membaca/menulis file di repo
    └─────────────────────────────────┘
```

Portal **tidak pernah** menyentuh file di dalam `repo_path`. Semua akses kode adalah urusan
opencode. Yang kita simpan hanyalah `map.db` dan `storage/attachments/`.

### Layout repo

```
backend/
  app/
    main.py              # FastAPI app, CORS, lifespan
    config.py            # pydantic-settings
    db/models.py  db/session.py
    api/
      workspaces.py  agents.py  tickets.py  comments.py
      attachments.py  runs.py  events.py  models.py
    core/
      orchestrator.py    # scheduler run, antrean per agent
      events.py          # EventBus + persist
      state_machine.py   # transisi status + izin per role
      guardrails.py      # budget, depth, loop detector, kill switch
      report.py          # parser blok ```map  ← kontrak balik dari agent
    agents/
      base.py            # AgentTool protocol
      opencode_tool.py   # satu-satunya adapter aktif
      stub_tool.py       # claude/agy/codex → error terstruktur
      prompts.py         # default system prompt per role + perakit konteks
  alembic/
  pyproject.toml
frontend/  (sama seperti sebelumnya)
storage/attachments/
docs/
```

Tidak ada `llm/`. Portal tidak bicara ke LLM mana pun secara langsung.

## 2. Data model

SQLite + SQLAlchemy 2.0 + Alembic. Semua id `TEXT` (uuid4 hex), timestamp UTC ISO.

```
workspace
  id, name, key (unique, 2-5 huruf kapital), repo_path,
  paused (bool, default false),
  guardrails (JSON, §6),
  ticket_counter (int),
  created_at

agent
  id, workspace_id → workspace.id (cascade),
  name (unique per workspace, slug untuk @mention),
  role (enum: pm|lead|engineer|designer|qa|pentester),
  model (str, format "provider/model" — dari `opencode models`),
  tool_kind (enum: opencode|claude|agy|codex),
  system_prompt (nullable → default per role),
  enabled (bool), status (enum: idle|working|error|disabled),
  created_at

agent_memory
  id, agent_id → agent.id (cascade),
  note (text), origin (enum: agent|owner),
  source_ticket_key (nullable, terisi hanya untuk origin=agent),
  created_at
  -- catatan lintas tiket per agent (bukan per tiket), MAP-035. origin=agent dari blok
  -- ```map `memory:` (§4.3); origin=owner dari POST manual /agents/{id}/memory.

ticket
  id, workspace_id (cascade), key ("MAP-001"),
  title, description (markdown),
  status (enum, §5), priority (enum: low|medium|high|urgent),
  assignee_id → agent.id (nullable, SET NULL),
  parent_id → ticket.id (nullable di DB untuk 1 level nesting, tapi API `POST` mewajibkan
    salah satu dari `parent_id` atau `is_new_epic: true` — tidak ada tiket lepas tanpa epic).
    "Epic" = tiket dengan parent_id NULL — bukan entity terpisah (ADR-012). Sengaja
    **reusable**: satu epic dipakai berkali-kali sebagai parent tiket baru (feature/story/
    bug/enhancement) ke depannya, bukan container sekali pakai per request — lihat §4.3
    field `tickets[].epic` dan §3 [03-agent-design.md](03-agent-design.md) untuk mekanisme
    reuse-nya,
  cost_used (float, default 0),           -- akumulasi biaya dari opencode
  handoff_depth (int, default 0),
  created_at, updated_at

artifact_group
  id, workspace_id (cascade), name, created_at
  -- get-or-create by (workspace_id, name) case-insensitive, dibuat agent lewat blok ```map
  -- `artifacts:` (§4.3). Tidak ada endpoint create/rename manual.

attachment
  id, ticket_id (cascade), filename, content_type, size_bytes,
  path (relatif ke storage/),
  origin (enum: upload|agent, default upload),  -- upload = lampiran manual owner,
                                                  -- agent = dipublikasikan dari `artifacts:`
  group_id → artifact_group.id (nullable, SET NULL),
  description (nullable, dari `artifacts:` — selalu NULL untuk origin=upload),
  created_at

comment
  id, ticket_id (cascade), author_agent_id (nullable = owner),
  is_system (bool), body (markdown), created_at

comment_mention
  id, comment_id (cascade), agent_id (cascade)

run
  id, ticket_id (cascade), agent_id (cascade),
  status (enum: queued|running|done|failed|cancelled|interrupted),
  trigger (enum: manual|mention|handoff|auto),
  parent_run_id → run.id (nullable),
  tool_kind, model,
  session_id (nullable),                  -- session opencode, untuk lanjutan
  tokens_in, tokens_out, cost (float),
  report (JSON, nullable),                -- hasil parse blok ```map
  error (text, nullable),
  started_at, ended_at

event
  id, run_id (cascade), workspace_id (denormal, filter SSE),
  seq (int, per run),
  type (enum: run_started|assistant_text|reasoning|tool_call|tool_result|
        status_change|comment|handoff|error|run_ended),
  payload (JSON), created_at
```

Index: `event(workspace_id, id)`, `event(run_id, seq)`, `ticket(workspace_id, status)`, `run(status)`.

Tabel `event` adalah sumber tunggal aktivitas: feed live dan replay setelah refresh membaca
tabel yang sama ([ADR-008](06-adr.md)).

## 3. API contract

Base `http://localhost:8000/api`. Error seragam `{"error": {"code": "...", "message": "..."}}`.

### Workspace
```
GET    /workspaces
POST   /workspaces               {name, key, repo_path}
GET    /workspaces/{id}
PATCH  /workspaces/{id}          {name?, repo_path?, guardrails?}
DELETE /workspaces/{id}
POST   /workspaces/{id}/pause
POST   /workspaces/{id}/resume
```
`repo_path` divalidasi: absolut, ada, direktori. (Validasi ini untuk kenyamanan — bukan sandbox;
lihat §7.)

### Agent
```
GET    /workspaces/{id}/agents
POST   /workspaces/{id}/agents   {name, role, model, tool_kind, system_prompt?}
PATCH  /agents/{id}
DELETE /agents/{id}              → 409 kalau punya run aktif
```

### Agent memory (MAP-035)
```
GET    /agents/{id}/memory       → terbaru dulu
POST   /agents/{id}/memory       {note}   -- origin=owner, catatan manual
DELETE /agent-memory/{memory_id}
```
Catatan `origin=agent` hanya dibuat orchestrator dari blok ```map `memory:` (§4.3) — tidak ada
endpoint POST untuk itu selain lewat laporan agent sendiri.

### Ticket
```
GET    /workspaces/{id}/tickets   ?status=&assignee_id=&parent_id=
POST   /workspaces/{id}/tickets   {title, description, priority?, assignee_id?, parent_id?,
                                    is_new_epic?}
                                   -- wajib salah satu dari parent_id atau is_new_epic=true
                                   (422 epic_required kalau keduanya kosong, 422
                                   invalid_epic_flag kalau keduanya diisi)
GET    /tickets/{key}             → tiket + comments + attachments + runs + children + parent
PATCH  /tickets/{key}
DELETE /tickets/{key}
```
Key: `UPDATE workspace SET ticket_counter = ticket_counter + 1 RETURNING ticket_counter`
dalam transaksi yang sama dengan insert. Nomor tidak pernah dipakai ulang.

### Attachment
```
POST   /tickets/{key}/attachments   multipart, max 25 MB
GET    /attachments/{id}
DELETE /attachments/{id}
```
Disimpan di `storage/attachments/<ticket_id>/<uuid>-<nama_sanitasi>`, di luar `repo_path`.
Attachment disertakan ke opencode lewat flag `-f` (§4.2).

### Artifact groups
```
GET   /workspaces/{id}/artifacts   → attachment origin=agent, dikelompokkan per ArtifactGroup
```
Read-only — grup dan attachment-nya dibuat lewat blok ```map `artifacts:` (§4.3), tidak ada
endpoint create/update/delete manual. Menu Artifacts di frontend (§8) memakai endpoint ini.

### Rutinitas (scheduled agent tasks)
```
GET    /workspaces/{id}/routines
POST   /workspaces/{id}/routines   {name, prompt, interval_minutes, mode, agent_id?}
PATCH  /routines/{id}              {name?, prompt?, interval_minutes?, mode?, agent_id?, status?}
DELETE /routines/{id}
POST   /routines/{id}/run          → trigger manual (tombol "Run now")
```
Rutinitas = tugas terjadwal yang menjalankan agent **tanpa tiket** (`Run.ticket_id = NULL`,
`trigger = "routine"`, `routine_id` menautkan ke `routine`). Status rutinitas:
`idle` (menunggu interval) → `waiting` (run terjadwal/antre) → `running` (run jalan) →
`idle`; `disabled` = dimatikan owner. Scheduler in-process (`core/routine_scheduler.py`)
tick tiap 60 detik; mode `idle_only` melewati tick kalau agent sibuk (dan memajukan
`last_run_at`), mode `consistent` mengantre di belakang run agent yang sedang berjalan.
Workspace `paused` → semua rutinitas dilewati. Guardrail `max_concurrent_runs` tetap
berlaku (rutinitas ikut dihitung); `max_cost_per_ticket`/`max_handoff_depth` tidak relevan
(tanpa tiket).

### Comment
```
GET    /tickets/{key}/comments
POST   /tickets/{key}/comments   {body, author_agent_id?}
```
Server mem-parse `@nama-agent`, mengisi `comment_mention`, dan memicu run untuk tiap agent
yang di-mention (kecuali penulisnya sendiri), `trigger=mention`.

### Run
```
POST   /tickets/{key}/run        {agent_id?}
POST   /runs/{id}/stop
POST   /runs/{id}/retry           -- hanya untuk run berstatus failed/interrupted
GET    /runs/{id}                → metadata + event (paginated)
GET    /workspaces/{id}/runs     ?status=
```
`retry` (MAP-036) menjadwalkan ulang agent+tiket yang sama (`trigger=manual`) — secara
mekanis identik dengan klik Run lagi. Lookup `session_id` di `execute()` sudah bersifat
status-agnostic (§4.5), jadi otomatis melanjutkan session opencode lama kalau run yang
di-retry sempat mendapat `session_id` sebelum gagal. Kalau tiket sedang `blocked` saat
retry, endpoint ini membersihkan block itu dulu (`blocked_reason=None`, `loop_reset_at`,
`handoff_depth=0`) — pola yang sama dengan `PATCH /tickets/{key}` saat status dipindah
keluar dari `blocked` — supaya histori sebelum kegagalan tidak langsung memicu ulang
guardrail yang sama. 409 `not_retryable` kalau run bukan `failed`/`interrupted`.

### Events (SSE)
```
GET /workspaces/{id}/events/stream?since_event_id=
```
`text/event-stream`, `id: <event.id>` per pesan, replay dari DB saat reconnect via
`Last-Event-ID`, heartbeat `: ping` tiap 15 detik.

### Models
```
GET /models   → ["opencode/big-pickle", "ollama/qwen3-coder:480b-cloud", ...]
```
Menjalankan `opencode models` (satu `provider/model` per baris), cache 5 menit di memori.
Satu sumber kebenaran: yang muncul di dropdown pasti dikenali opencode.
Kalau daftarnya kosong atau perintahnya gagal → 503 dengan pesan yang menyarankan
`opencode auth login`. Model Ollama Cloud muncul di sini setelah provider `ollama`
dikonfigurasi di opencode oleh owner — portal tidak menyimpan API key LLM sama sekali.

### MCP ticket server (ADR-011)

Setiap run opencode mendapat MCP server lokal (`app/mcp_server.py`, stdio subprocess) lewat
config `opencode.json` per run (`OPENCODE_CONFIG` env, dihapus setelah run selesai). Server
memproksi ke backend HTTP (`MAP_API_BASE`, default `127.0.0.1:8000/api`) dengan scope
workspace/agent dari env per run. Tools:

```
list_tickets      → daftar tiket workspace; tiket top-level (epic) ditandai [EPIC]
get_ticket(key)   → detail: deskripsi, komentar, status, assignee, sub-tiket
post_comment      → komentar ke tiket (author = agent berjalan, tidak memicu run)
create_ticket(epic?) → tiket backlog baru, tidak auto-schedule. Tanpa `epic`: jadi epic
                        top-level baru. Dengan `epic` (key epic yang sudah ada, ADR-012):
                        nempel sebagai anak epic itu — ditolak kalau key bukan epic
                        top-level.
update_ticket     → ubah status/priority (actor = agent berjalan, state machine backend)
list_artifacts    → kelompok + file artifact (menu Artifacts)
read_artifact     → isi artifact (markdown/teks; dipotong 8.000 karakter)
get_memory        → catatan memory agent ini
create_memory     → simpan catatan memory (max 500 karakter)
update_memory     → perbarui catatan memory yang ada
```

Semua validasi (state machine, role gate, approval PM) tetap di backend — server ini proksi
tipis, bukan duplikasi logika. Tidak ada TCP: server hanya stdio subprocess yang di-spawn
backend. Matikan dengan `MAP_MCP_ENABLED=false` (config backend) untuk run tanpa tool tiket.

## 4. Agent runtime

Satu run = satu proses `opencode`. Tidak ada loop, tidak ada tool call dari sisi kita.

```
prompt (dirakit) ──► opencode subprocess ──► stream JSON ──► Event ──► SSE + DB
                                          └► teks akhir ──► parse blok ```map ──► aksi tiket
```

### 4.1 Adapter

```python
# agents/base.py
class AgentTool(Protocol):
    async def run(self, ctx: RunContext) -> AsyncIterator[Event]: ...
```

`RunContext`: `run_id`, `workspace`, `agent`, `ticket`, `repo_path`, `prompt`, `attachments`,
`prev_session_id`, `guardrails`, `cancel_event`.

`TOOLS = {"opencode": OpenCodeTool}`. `claude`/`agy`/`codex` dipetakan ke `StubTool` yang
langsung mengembalikan event `error` "adapter belum tersedia" dan menandai run `failed`.

### 4.2 OpenCodeTool

```
opencode run --format json --dir <repo_path> -m <provider/model> --auto \
  [-s <prev_session_id>] [-f <attachment_path> ...] "<prompt>"
```

- `--format json` → stream JSON event per baris di stdout. Adapter memetakannya ke `Event`:
  teks asisten → `assistant_text`, penggunaan tool oleh opencode → `tool_call`/`tool_result`,
  reasoning → `reasoning`, error → `error`. Baris yang bukan JSON valid **dilewati**, tidak
  mematikan run. stderr ditangkap ke `run.error` bila exit code ≠ 0.
- `--auto` wajib: tidak ada manusia yang menyetujui permission dialog. Konsekuensinya di §7.
- `session_id` dari event pertama disimpan di `run.session_id`. Bila agent yang sama kembali ke
  tiket yang sama, run berikutnya memakai `-s <session_id>` supaya konteks kerjanya tersambung.
- Attachment tiket dilewatkan lewat `-f`.
- Token dan biaya dibaca dari event opencode dan diakumulasi ke `run` dan `ticket.cost_used`.
- Cancel: `process.terminate()`, tunggu 5 detik, `kill()`.
- Binary tidak ada di PATH → run `failed` dengan pesan jelas, backend tidak crash.

### 4.3 Kontrak balik: blok ```map

opencode adalah kotak hitam — ia tidak bisa memanggil API tiket kita. Karena itu setiap prompt
diakhiri instruksi untuk menutup jawaban dengan satu blok berpagar:

````
```map
status: review              # status tujuan tiket ini
mention: [lead-1]           # agent yang harus lanjut (nama, bukan role)
summary: |                  # jadi komentar di tiket
  Menambah validasi email di form login.
  File: src/auth/login.tsx, src/auth/validate.ts
  Bukti: npm test → 12 passed
tickets:                    # opsional; dipakai PM untuk breakdown, QA/Pentester untuk bug
  - title: Endpoint POST /auth/login
    description: |
      ...
    assignee: eng-1
    priority: high
    epic: AUTH-001            # opsional; key epic tujuan — WAJIB isi kalau ada yang relevan
artifacts:                  # opsional; file yang dihasilkan agent, tampil di menu Artifacts
  - path: docs/PRD.md       # relatif ke repo_path
    group: Dokumen Teknis   # WAJIB dari daftar kelompok yang ada di prompt (lihat aturan di bawah)
    description: initial PRD
memory:                     # opsional; catatan lintas tiket, lihat aturan di bawah
  - Jangan lupa jalankan migrasi sebelum lapor done.
```
````

Aturan parser (`core/report.py`):

- Ambil blok ```map **terakhir** dari teks asisten terakhir. Parse sebagai YAML (`yaml.safe_load`).
- `status` divalidasi state machine (§5). Ilegal → tiket `blocked` + komentar sistem yang menyebut
  transisi yang diminta.
- `mention` dicocokkan ke nama agent di workspace. Nama tak dikenal → dicatat di komentar sistem,
  tidak memicu run.
- `summary` **wajib** → jadi komentar tiket dengan `author_agent_id` agent tersebut.
- `tickets[]` opsional. Di-assign, status `todo`. Hanya PM, QA, dan Pentester yang boleh mengisi
  ini (ditegakkan per role, bukan dipercayakan ke model). Parent (`parent_id`) diresolusi
  orchestrator, **bukan** selalu "anak dari tiket saat ini" (lihat `epic:` di bawah, ADR-012).
- **`epic:` per item `tickets[]`** (ADR-012) — key epic (tiket top-level) tujuan, opsional.
  Kontrak menyertakan katalog epic yang sudah ada (top-level tickets, ~100 terbaru diupdate,
  pola sama seperti katalog `artifacts:` di bawah) dengan aturan WAJIB reuse kalau relevan.
  Resolusi `parent_id` orchestrator: (1) `epic:` valid → id epic itu; (2) `epic:` tak
  dikenal/bukan epic top-level → di-skip dengan catatan di komentar sistem, lanjut ke (3);
  (3) tanpa `epic:`, tiket saat ini **sudah punya parent** → pakai parent itu (sibling di
  bawah epic yang sama — menjaga flat 1-level, menambal bug lama di mana jalur agent tidak
  menegakkan ini seperti jalur API manual); (4) tanpa `epic:` dan tiket saat ini tidak punya
  parent → tiket saat ini sendiri jadi parent (behavior asli, tiket ini jadi epic baru).
- `sprints[]` opsional, companion dari `tickets[]` (dieksekusi hanya saat report juga membawa
  `tickets[]`). Role yang boleh mendeklarasikannya diatur per workspace lewat setting
  `sprint_creator_roles` (halaman Settings, pill picker; default `["pm"]`) — ditegakkan di
  parser, bukan dipercayakan ke prompt. Gate persetujuan owner (PM belum approve) tetap
  berlaku untuk role pm. **Sprint murni timebox** — kontrak menyertakan katalog nama sprint
  yang sudah ada dengan aturan WAJIB reuse (nama persis) kalau timebox-nya masih relevan, dan
  instruksi tegas: jangan taruh nama fitur/scope di nama sprint (itu urusan `epic:` di atas).
- `artifacts[]` opsional, tersedia untuk semua role (tidak seperti `tickets[]`). Tiap entri
  (`path`, `group`, `description?`) diproses orchestrator (bukan parser ini — parser tetap
  bebas filesystem): `path` diresolusi relatif ke `repo_path` dan **wajib tetap di dalam**
  `repo_path` (entri yang keluar lewat `..`/path absolut diabaikan + dicatat di komentar
  sistem, sama seperti `updates:` yang gagal), lalu isinya disalin ke `storage/attachments/`
  sebagai `Attachment` (`origin=agent`, `group_id` dari `ArtifactGroup` get-or-create by name
  per workspace, case-insensitive — pola sama seperti sprint). Ini satu-satunya tempat
  orchestrator membaca file di dalam `repo_path`, dan hanya path eksplisit yang dideklarasikan
  agent sendiri — bukan scan folder, bukan tool filesystem baru untuk agent (lihat catatan di
  [ADR-006](06-adr.md)).
- **Nama kelompok tidak lagi bebas.** Blok ```map di prompt menyertakan daftar kelompok yang
  sudah ada di workspace (daftar `ArtifactGroup` saat prompt disusun); agent wajib memilih salah
  satu yang relevan (mencocokkan tujuan, bukan ejaan persis) dan hanya boleh membuat nama baru
  kalau tidak ada yang cocok. Mencegah duplikat/ambigu seperti "Dokumen Teknis" vs "Dokumen
  Teknikal". Dedup case-insensitive get-or-create tetap berlaku sebagai jaring pengaman terakhir.
- **Katalog artifacts di prompt.** Setiap prompt menyertakan daftar artifacts yang sudah
  dipublikasikan di workspace (paling baru ~100, format `[kelompok] filename (KEY) —
  deskripsi`) supaya semua agent bisa membaca/mencari apa yang sudah ada sebelum membuat file
  baru — mencegah duplikasi kerja dan file yang menumpuk.
- `artifact_updates[]` opsional, **HANYA PM** (ditegakkan di parser, sama seperti `tickets[]`).
  Merapikan menu Artifacts: `rename` (group→to; kalau `to` sudah ada, otomatis jadi merge),
  `merge` (from→into, sumber dihapus), `move` (satu file antar kelompok), `delete` (hanya
  kelompok kosong; yang masih berisi file ditolak). Dieksekusi orchestrator **setelah**
  `_publish_artifacts` pada report yang sama, jadi artifacts baru di blok yang sama ikut
  terorganisir. Kelompok/file tak ditemukan atau op tak dikenal → dicatat di komentar sistem,
  tidak menggagalkan report (toleransi sama seperti `updates:`/`tickets:`).
- `memory[]` opsional (MAP-035), tersedia untuk semua role seperti `artifacts[]`. Tiap entri
  string dipersist sebagai baris `agent_memory` baru (`origin=agent`, `source_ticket_key` dari
  tiket ini) — beda dari `artifacts[]`, tidak ada filesystem yang tersentuh. Entri kosong/bukan
  string dibuang tanpa gagal, dan tiap catatan dipotong ke 500 karakter (lihat §4.4 soal
  bagaimana catatan ini dipakai lagi di prompt berikutnya).
- **Run rutinitas** (`trigger="routine"`, tanpa tiket) memakai kontrak ```map yang berbeda:
  `status`/`mention` **ditolak** (parse error → run `failed`, bukan block). Yang diizinkan:
  `summary` (wajib), `comments[]` (komen ke tiket lain — hanya valid di run rutinitas),
  `tickets[]` (jadi tiket backlog `todo`, **tidak** auto-schedule), `updates[]`, `memory[]`,
  `artifact_updates[]` (PM). `artifacts[]` ditolak (butuh tiket untuk FK/folder storage).
  Aksi dieksekusi orchestrator; tidak ada transisi status tiket apa pun.
- **Blok hilang atau YAML rusak** → run `failed`, tiket `blocked`, komentar sistem berisi 2.000
  karakter terakhir output agent supaya kamu bisa melihat apa yang sebenarnya ia tulis.
  Tidak ada tebakan, tidak ada kegagalan diam. ([ADR-009](06-adr.md))

Seluruh output asisten tetap tersimpan penuh sebagai event — blok ```map hanya menentukan
aksi, bukan menggantikan jejak.

### 4.4 Perakit prompt

`agents/prompts.py` merakit, berurutan:

1. **BASE** — identitas, `repo_path`, aturan kerja, daftar rekan tim
   ([03-agent-design.md](03-agent-design.md) §2).
2. **Blok role** — default per role, atau `agent.system_prompt` bila diisi.
3. **Memory agent** (MAP-035) — catatan `agent_memory` milik agent ini sendiri, lintas tiket
   (paling baru ~20 entri, terurut kronologis), kalau ada. Muncul sebelum konteks tiket karena
   sifatnya lintas-tiket, bukan spesifik tiket saat ini.
4. **Konteks tiket** — key, judul, status, prioritas, deskripsi, daftar attachment,
   5 komentar terakhir, ringkasan `report.summary` dari run-run sebelumnya di tiket ini.
5. **Katalog artifacts** — daftar artifacts workspace (paling baru ~100) supaya agent bisa
   membaca/mencari apa yang sudah dipublikasikan sebelum membuat file baru.
6. **Konteks anti-loop** — bila ini review ke-n, ringkasan review sebelumnya
   ([03-agent-design.md](03-agent-design.md) §7).

7. **Kontrak blok ```map** — status yang legal untuk role ini, nama-nama agent yang bisa
   di-mention, apakah `tickets[]` diizinkan, plus (untuk role yang boleh `tickets[]`) katalog
   epic yang sudah ada (`existing_epics`, ADR-012) dan katalog sprint (`existing_sprints`) yang
   di-query live oleh orchestrator tiap run — bukan disimpan sebagai teks statis, karena
   harus selalu mencerminkan tiket/sprint terbaru di workspace.

Prompt final disimpan di event `run_started` supaya bisa diperiksa saat sesuatu berjalan aneh.

### 4.5 Orchestrator

```python
async def schedule(ticket, agent, trigger, parent_run=None):
    if workspace.paused: return reject("workspace paused")
    guard = check_guardrails(ticket, agent, parent_run)
    if not guard.ok: return block_ticket(ticket, guard.reason)
    run = create_run(...)
    RUNNING[run.id] = asyncio.create_task(execute(run))
```

- Satu agent = satu run aktif; sisanya antre FIFO per agent.
- Tiap event dari adapter → `EventBus.publish()` → insert ke `event` **lalu** push ke subscriber SSE.
- Run selesai → parse blok ```map → terapkan aksi tiket → tentukan agent berikutnya dari
  `mention` → `schedule()`.
- Startup: run berstatus `running`/`queued` di DB ditandai `interrupted`, agent di-reset `idle`,
  komentar sistem ditulis. ([ADR-004](06-adr.md))

## 5. State machine tiket

```
backlog → todo → in_progress → review → qa → security → done
                     ↑            │      │      │
                     └────────────┴──────┴──────┘   (reject/bug → in_progress)
   any → blocked   (guardrail, error, blok map rusak, atau agent minta)
   blocked → todo  (hanya owner)
```

Matriks izin per role ada di [03-agent-design.md](03-agent-design.md) §4, ditegakkan di
`core/state_machine.py`. Berlaku sama untuk `PATCH /tickets/{key}` maupun `status` dari blok ```map.
Setiap transisi menulis event `status_change` + komentar sistem.

## 6. Guardrail

Per workspace, di `workspace.guardrails` (JSON), bisa diedit di halaman settings.

```json
{
  "run_timeout_sec": 1800,
  "max_cost_per_run": 2.0,
  "max_cost_per_ticket": 20.0,
  "max_handoff_depth": 12,
  "loop_threshold": 3,
  "max_concurrent_runs": 3
}
```

- **run_timeout_sec** — `asyncio.wait_for` di sekitar subprocess; lewat → terminate + run `failed`.
- **max_cost_per_run** — dipantau dari event biaya opencode selagi berjalan; lewat → terminate.
- **max_cost_per_ticket** — akumulasi `ticket.cost_used`; lewat → tiket `blocked`.
- **max_handoff_depth** — panjang rantai `parent_run_id`; lewat → `blocked`. Tidak berlaku untuk
  run yang dipicu chat owner (`trigger="mention"`) — lihat [03-agent-design.md](03-agent-design.md)
  §6.
- **loop_threshold** — pasangan agent yang ping-pong (A→B→A→B) melebihi ambang → `blocked` +
  komentar sistem yang menuliskan siklusnya.
- **max_concurrent_runs** — semaphore per workspace. Default rendah (3) karena tiap run adalah
  proses opencode penuh, bukan sekadar panggilan HTTP.
- **Kill switch** — `POST /workspaces/{id}/pause`: set `paused=true`, set semua `cancel_event`,
  terminate seluruh subprocess, tandai run `cancelled`, agent `idle`, tolak schedule baru.
- **ticket_not_in_active_sprint** — bukan bagian dari dict di atas (selalu aktif, tidak ada toggle
  di Settings). Ticket yang belum masuk sprint manapun (backlog) atau sprint-nya bukan yang
  `active` tidak bisa dijalankan agent → `blocked` + komentar sistem. Dikecualikan: role apa pun
  yang ada di `workspace.sprint_creator_roles` (default hanya PM) — role itu perlu selalu bisa
  merespon ticket apa pun (termasuk backlog) untuk melakukan triage/pemindahan ke sprint. Owner
  memindahkan ticket ke sprint aktif atau menukar sprint mana yang aktif lewat mekanisme yang
  sudah ada (`PATCH /tickets/{key}` `sprint_id`, `PATCH /sprints/{id}` `status`).

Setiap guardrail yang memblokir **selalu** menulis komentar sistem yang menyebut guardrail mana.
Tidak ada kegagalan diam.

Perhatikan: tidak ada lagi guardrail step/token per iterasi — kita tidak mengendalikan loop di
dalam opencode. Rem yang tersisa adalah waktu, biaya, dan topologi handoff.

## 7. Keamanan — apa yang dijamin dan apa yang tidak

**Tidak dijamin.** `opencode --auto` menyetujui semua permission. Agent bisa menjalankan perintah
apa pun dengan hak akses user yang menjalankan backend. `--dir <repo_path>` menetapkan working
directory, **bukan sandbox** — tidak ada yang mencegahnya menyentuh file di luar folder itu.
Ini konsekuensi yang diterima sadar ([ADR-010](06-adr.md)).

**Karena itu:**
- Backend bind ke `127.0.0.1`. Portal ini tidak boleh terjangkau jaringan.
- README dan halaman settings memuat peringatan eksplisit dengan kalimat ini, bukan disembunyikan.
- Jalankan hanya pada repo yang kamu percayai, di mesin yang kamu kendalikan.
- Jangan menaruh secret produksi di dalam `repo_path`.

**Yang tetap dijamin portal:**
- API key LLM tidak pernah disimpan atau disentuh portal (urusan `opencode auth`).
- Attachment disimpan di luar `repo_path` dan namanya disanitasi.
- Kill switch benar-benar mematikan proses anak, bukan sekadar menandai status di DB.

## 8. Frontend

| Route | Isi |
|---|---|
| `/` | Daftar workspace + form buat baru |
| `/w/[key]/board` | Kanban per status, drag & drop, badge agent yang sedang kerja |
| `/w/[key]/ticket/[ticketKey]` | Detail: deskripsi, attachment, komentar + mention composer, daftar run, Run/Stop; link ke epic induk (kalau ada) dan daftar sub-tiket (kalau epic) |
| `/w/[key]/agents` | Setup agent: role, model (dropdown `/models`), tool_kind, system prompt; tombol "Memory" per agent membuka dialog catatan lintas tiket (MAP-035) |
| `/w/[key]/activity` | Feed live; klik run → panel output opencode + tool call + blok map hasil parse; tombol "Retry" pada run `failed`/`interrupted` (daftar run maupun panel detail, MAP-036) |
| `/w/[key]/artifacts` | Read-only: attachment `origin=agent`, dikelompokkan per ArtifactGroup, link balik ke tiket asal |
| `/w/[key]/settings` | `repo_path`, guardrail, Pause/Resume, peringatan keamanan §7 |

Realtime: satu `EventSource` per workspace di React context; event masuk → update feed +
invalidate query terkait.

## 9. Konfigurasi

`.env` di `backend/`:
```
DATABASE_URL=sqlite+aiosqlite:///./map.db
STORAGE_DIR=../storage
CORS_ORIGINS=http://localhost:3000
OPENCODE_BIN=opencode
```
Tidak ada kredensial LLM. Itu milik opencode.

## 10. Testing

Wajib punya test otomatis (pytest):

1. **Parser blok ```map** — blok valid, blok hilang, YAML rusak, blok ganda (ambil terakhir),
   `status` ilegal, `tickets[]` dari role yang tidak berhak.
2. **State machine** — matriks transisi legal/ilegal per role.
3. **Loop detector** — A→B→A→B→A memicu `blocked`; A→B→C→A tidak.
4. **Penomoran tiket** — 100 insert paralel → 100 key unik berurutan.
5. **Guardrail biaya** — melewati `max_cost_per_ticket` → `blocked` + komentar sistem.
6. **Adapter opencode** — dengan binary palsu (skrip yang mencetak JSON contoh): parsing event,
   baris rusak dilewati, exit non-zero → `failed`, cancel mematikan proses anak.

Sisanya diuji manual sesuai definition of done di [05-roadmap.md](05-roadmap.md).
