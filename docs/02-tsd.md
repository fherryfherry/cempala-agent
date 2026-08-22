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

ticket
  id, workspace_id (cascade), key ("MAP-001"),
  title, description (markdown),
  status (enum, §5), priority (enum: low|medium|high|urgent),
  assignee_id → agent.id (nullable, SET NULL),
  parent_id → ticket.id (nullable, 1 level),
  cost_used (float, default 0),           -- akumulasi biaya dari opencode
  handoff_depth (int, default 0),
  created_at, updated_at

attachment
  id, ticket_id (cascade), filename, content_type, size_bytes,
  path (relatif ke storage/), created_at

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

### Ticket
```
GET    /workspaces/{id}/tickets   ?status=&assignee_id=&parent_id=
POST   /workspaces/{id}/tickets   {title, description, priority?, assignee_id?, parent_id?}
GET    /tickets/{key}             → tiket + comments + attachments + runs + children
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
GET    /runs/{id}                → metadata + event (paginated)
GET    /workspaces/{id}/runs     ?status=
```

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
```
````

Aturan parser (`core/report.py`):

- Ambil blok ```map **terakhir** dari teks asisten terakhir. Parse sebagai YAML (`yaml.safe_load`).
- `status` divalidasi state machine (§5). Ilegal → tiket `blocked` + komentar sistem yang menyebut
  transisi yang diminta.
- `mention` dicocokkan ke nama agent di workspace. Nama tak dikenal → dicatat di komentar sistem,
  tidak memicu run.
- `summary` **wajib** → jadi komentar tiket dengan `author_agent_id` agent tersebut.
- `tickets[]` opsional. Dibuat sebagai anak dari tiket saat ini, di-assign, status `todo`.
  Hanya PM, QA, dan Pentester yang boleh mengisi ini (ditegakkan per role, bukan dipercayakan
  ke model).
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
3. **Konteks tiket** — key, judul, status, prioritas, deskripsi, daftar attachment,
   5 komentar terakhir, ringkasan `report.summary` dari run-run sebelumnya di tiket ini.
4. **Konteks anti-loop** — bila ini review ke-n, ringkasan review sebelumnya
   ([03-agent-design.md](03-agent-design.md) §7).
5. **Kontrak blok ```map** — status yang legal untuk role ini, nama-nama agent yang bisa
   di-mention, dan apakah `tickets[]` diizinkan.

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
- **max_handoff_depth** — panjang rantai `parent_run_id`; lewat → `blocked`.
- **loop_threshold** — pasangan agent yang ping-pong (A→B→A→B) melebihi ambang → `blocked` +
  komentar sistem yang menuliskan siklusnya.
- **max_concurrent_runs** — semaphore per workspace. Default rendah (3) karena tiap run adalah
  proses opencode penuh, bukan sekadar panggilan HTTP.
- **Kill switch** — `POST /workspaces/{id}/pause`: set `paused=true`, set semua `cancel_event`,
  terminate seluruh subprocess, tandai run `cancelled`, agent `idle`, tolak schedule baru.

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
| `/w/[key]/ticket/[ticketKey]` | Detail: deskripsi, attachment, komentar + mention composer, daftar run, Run/Stop |
| `/w/[key]/agents` | Setup agent: role, model (dropdown `/models`), tool_kind, system prompt |
| `/w/[key]/activity` | Feed live; klik run → panel output opencode + tool call + blok map hasil parse |
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
