# Task Breakdown — MAP-001 … MAP-033

Versi 0.2 · MVP
Estimasi: **S** ≤ ½ hari · **M** ~1 hari · **L** ~2 hari
Dependency selalu menunjuk nomor lebih kecil (tidak ada siklus).
Milestone: lihat [05-roadmap.md](05-roadmap.md).

> **Perubahan v0.2.** 8 tiket dihapus karena kita tidak membangun coding agent sendiri:
> klien Ollama, loop tool-calling, tool filesystem, tool ticketing, path jail, command allowlist.
> 2 tiket baru: parser blok ```map dan adapter opencode yang naik ke M2.
> Total turun dari 41 → 33 tiket, ~35 hari → ~26 hari.

## Ringkasan

| Milestone | Tiket | Estimasi total |
|---|---|---|
| M0 Skeleton | MAP-001 … MAP-005 | ~3 hari |
| M1 Ticketing | MAP-006 … MAP-016 | ~9 hari |
| M2 Agent Runtime | MAP-017 … MAP-026 | ~8 hari |
| M3 Otonomi | MAP-027 … MAP-033 | ~6 hari |

---

## M0 — Skeleton

### MAP-001 · Inisialisasi repo & struktur folder · S · Lead
Git repo, `.gitignore`, `README.md`, struktur `backend/`, `frontend/`, `storage/`, `docs/`
sesuai [02-tsd.md](02-tsd.md) §1. README memuat peringatan keamanan `--auto`
([02-tsd.md](02-tsd.md) §7) dan langkah `opencode auth login`.
**Dep:** —
**AC:** `git status` bersih setelah commit awal; `storage/` ter-ignore kecuali `.gitkeep`;
peringatan keamanan ada di README, bukan cuma di docs.

### MAP-002 · Bootstrap backend FastAPI · S · Engineer
FastAPI + CORS + `config.py` (pydantic-settings: `DATABASE_URL`, `STORAGE_DIR`, `CORS_ORIGINS`,
`OPENCODE_BIN`). Bind `127.0.0.1`. Endpoint `GET /api/health` yang juga melaporkan apakah binary
opencode ditemukan.
**Dep:** MAP-001
**AC:** `uvicorn app.main:app` jalan dan hanya menerima koneksi dari localhost;
`/api/health` → `{"status":"ok","opencode":"1.x.x"}` atau `"opencode": null` bila tak ditemukan.
Tidak ada variabel kredensial LLM di config.

### MAP-003 · Skema DB & Alembic · M · Engineer
Seluruh model dari [02-tsd.md](02-tsd.md) §2 + index. Alembic + migrasi awal.
**Dep:** MAP-002
**AC:** `alembic upgrade head` membuat semua tabel, `downgrade base` bersih;
cascade delete workspace terverifikasi lewat test.

### MAP-004 · Bootstrap frontend Next.js · M · Engineer
Next.js App Router + TS + Tailwind + shadcn/ui, `lib/api.ts`, React Query provider, layout+header.
**Dep:** MAP-001
**AC:** `next dev` jalan di :3000; halaman root memanggil `/api/health` dan menampilkan status
backend **dan** status opencode.

### MAP-005 · Dev runner satu perintah · S · Engineer
`Makefile` menjalankan backend + frontend, plus `make migrate` dan `make test`.
**Dep:** MAP-002, MAP-004
**AC:** `make dev` menyalakan keduanya; README: setup dari nol ≤5 langkah, termasuk instalasi
dan autentikasi opencode.

---

## M1 — Ticketing

### MAP-006 · API Workspace CRUD · M · Engineer
CRUD sesuai [02-tsd.md](02-tsd.md) §3, termasuk validasi `repo_path` (absolut, ada, direktori).
**Dep:** MAP-003
**AC:** repo_path tidak valid → 422 dengan alasannya; key duplikat → 409;
delete workspace menghapus turunannya tapi tidak menyentuh folder di disk (diverifikasi test).

### MAP-007 · `GET /api/models` dari `opencode models` · S · Engineer
Jalankan `opencode models`, parse satu `provider/model` per baris, cache 5 menit di memori.
**Dep:** MAP-002
**AC:** perintah gagal / daftar kosong → 503 dengan pesan yang menyarankan `opencode auth login`;
timeout 30 detik; backend tidak menyimpan kredensial LLM apa pun.

### MAP-008 · API Agent CRUD · M · Engineer
CRUD agent per workspace: name (slug unik per workspace), role, model, tool_kind, system_prompt
opsional, enabled, status.
**Dep:** MAP-006
**AC:** nama duplikat dalam satu workspace → 409; role/tool_kind di luar enum → 422;
`DELETE` agent dengan run aktif → 409.

### MAP-009 · API Ticket CRUD & penomoran key · M · Engineer
CRUD + key `<KEY>-<n>` lewat `ticket_counter` dalam satu transaksi. `GET /tickets/{key}`
mengembalikan tiket + comments + attachments + runs + children. Filter query sesuai TSD.
**Dep:** MAP-006, MAP-008
**AC:** nomor selalu naik dan tak pernah dipakai ulang meski tiket dihapus;
`parent_id` ke tiket yang sudah punya parent → 422 (maks 1 level).

### MAP-010 · API Comment & parsing mention · M · Engineer
`GET/POST /api/tickets/{key}/comments`, parsing `@nama-agent` → `comment_mention`.
Pemicuan run belum di sini (MAP-029).
**Dep:** MAP-009
**AC:** `@tidak-ada` tidak membuat mention dan tidak error; mention diri sendiri dibuang;
nama yang sama dua kali → satu baris mention.

### MAP-011 · API Attachment · S · Engineer
Upload multipart (max 25 MB), download, delete ke
`storage/attachments/<ticket_id>/<uuid>-<nama_sanitasi>`.
**Dep:** MAP-009
**AC:** nama `../../etc/passwd` tersanitasi jadi nama datar; >25 MB → 413;
file tersimpan di luar `repo_path` (diverifikasi test).

### MAP-012 · State machine tiket · M · Engineer
`core/state_machine.py`: enum status + matriks transisi legal + izin per role
([03-agent-design.md](03-agent-design.md) §5). Di-enforce di `PATCH /tickets/{key}`.
Tiap transisi menulis komentar sistem.
**Dep:** MAP-009, MAP-010
**AC:** transisi ilegal → 422 menyebut transisi yang diminta; owner (tanpa agent) boleh transisi
apa pun termasuk `blocked → todo`; API-nya dipakai ulang oleh parser blok map (MAP-018).

### MAP-013 · UI Workspace + Setup Agent · L · Engineer
Halaman `/` (daftar + form workspace), switcher di header, dan `/w/[key]/agents`
(daftar + form agent). Dropdown model dari `/api/models`; tool_kind menampilkan `opencode` aktif
dan `claude`/`agy`/`codex` disabled berlabel "coming soon".
**Dep:** MAP-004, MAP-006, MAP-007, MAP-008
**AC:** error validasi `repo_path` tampil di field yang tepat; workspace aktif ada di URL;
`/api/models` gagal → dropdown berubah jadi input teks bebas + pesan error yang menyebut
`opencode auth login`.

### MAP-014 · UI Kanban board · L · Engineer
`/w/[key]/board`: kolom per status, kartu tiket (key, judul, assignee, prioritas), drag & drop
memanggil `PATCH /tickets/{key}`.
**Dep:** MAP-013, MAP-012
**AC:** drop ke kolom yang transisinya ilegal ditolak, kartu kembali ke posisi semula dengan
toast berisi pesan backend.

### MAP-015 · UI Detail tiket · L · Engineer
`/w/[key]/ticket/[ticketKey]`: deskripsi markdown, sub-tiket, attachment, komentar, composer
dengan autocomplete `@agent`, tombol Run (placeholder sampai MAP-023).
**Dep:** MAP-014, MAP-010, MAP-011
**AC:** autocomplete hanya menampilkan agent workspace itu; komentar sistem tampil berbeda dari
komentar agent; attachment terunduh dengan nama aslinya.

### MAP-016 · Test: penomoran & state machine · S · QA
pytest: 100 pembuatan tiket paralel → 100 key unik berurutan; matriks transisi legal/ilegal per role.
**Dep:** MAP-009, MAP-012
**AC:** `make test` hijau; kedua test gagal bila logika terkait sengaja dirusak.

---

## M2 — Agent Runtime

### MAP-017 · EventBus & persistensi event · M · Engineer
`core/events.py`: `publish()` → insert ke tabel `event` (dengan `seq` per run) **lalu** push ke
subscriber. Subscribe/unsubscribe per workspace.
**Dep:** MAP-003
**AC:** subscriber lambat tidak memblokir publisher (antrean bounded, drop tertua + tandai
`overflow`); event tetap lengkap di DB meski subscriber overflow.

### MAP-018 · Parser blok ```map · M · Lead
`core/report.py`: ambil blok ```map terakhir, `yaml.safe_load`, validasi `status` terhadap state
machine + hak role, cocokkan `mention` ke nama agent, wajibkan `summary`, terima `tickets[]`
hanya dari PM/QA/Pentester ([02-tsd.md](02-tsd.md) §4.3).
**Dep:** MAP-012
**AC:** blok hilang atau YAML rusak → hasil `invalid` berisi alasan (bukan exception);
blok ganda → yang terakhir dipakai; `tickets[]` dari Engineer diabaikan + dicatat;
`status` ilegal untuk role tersebut ditolak dengan alasan yang menyebut role dan transisinya.
Semua kasus di [02-tsd.md](02-tsd.md) §10.1 punya test.

### MAP-019 · Perakit prompt & default prompt per role · M · Lead
`agents/prompts.py`: BASE + blok role ([03-agent-design.md](03-agent-design.md) §4) + konteks
tiket + konteks anti-loop + kontrak ```map yang menyebut status legal & nama agent yang bisa
di-mention untuk role itu.
**Dep:** MAP-018
**AC:** `agent.system_prompt` yang diisi menggantikan blok role (BASE + kontrak ```map tetap ada);
prompt Engineer tidak pernah memuat instruksi `tickets[]` (diverifikasi test);
prompt final tersimpan di event `run_started`.

### MAP-020 · Adapter OpenCode · L · Engineer
`agents/opencode_tool.py`: subprocess
`opencode run --format json --dir <repo> -m <model> --auto [-s <session>] [-f <att>] "<prompt>"`,
parse stdout JSON per baris → `Event`, simpan `session_id`, akumulasi token & biaya,
terminate→kill saat cancel.
**Dep:** MAP-017, MAP-019
**AC:** binary tak ada → run `failed` dengan pesan jelas, backend tidak crash;
baris stdout bukan-JSON dilewati tanpa mematikan run; exit code ≠ 0 → `failed` dengan stderr
tersimpan di `run.error`; cancel benar-benar mematikan proses anak (dicek `ps`).
Diuji dengan binary palsu berupa skrip yang mencetak JSON contoh — tanpa memanggil LLM sungguhan.

### MAP-021 · Stub adapter claude/agy/codex · S · Engineer
`agents/stub_tool.py`: langsung emit event `error` "adapter belum tersedia" dan tandai run `failed`.
**Dep:** MAP-020
**AC:** menyimpan agent dengan tool_kind tersebut tetap boleh; menjalankannya menghasilkan run
`failed` dengan pesan yang bisa dibaca, bukan 500.

### MAP-022 · Endpoint SSE · M · Engineer
`GET /api/workspaces/{id}/events/stream?since_event_id=`, `id:` per event, replay dari DB saat
reconnect, heartbeat 15 detik, cleanup subscriber saat disconnect.
**Dep:** MAP-017
**AC:** putus lalu reconnect dengan `Last-Event-ID` tidak kehilangan maupun menduplikasi event;
menutup tab melepaskan subscriber.

### MAP-023 · API Run & orchestrator dasar · L · Lead
`POST /tickets/{key}/run`, `POST /runs/{id}/stop`, `GET /runs/{id}`, `GET /workspaces/{id}/runs`.
`core/orchestrator.py`: `schedule()`, `execute()`, registry run aktif, antrean FIFO per agent,
status agent, penerapan hasil parser blok map ke tiket (status + komentar + `tickets[]`).
Handoff otomatis **belum** di sini (MAP-029).
**Dep:** MAP-020, MAP-018, MAP-022
**AC:** satu agent tidak pernah punya dua run `running`; run yang melempar exception → tiket
`blocked` + komentar sistem berisi error; blok map hilang/rusak → tiket `blocked` + komentar
sistem berisi 2.000 karakter terakhir output agent; agent kembali `idle` dalam kondisi apa pun.

### MAP-024 · Frontend SSE context · M · Engineer
React context dengan satu `EventSource` per workspace, auto-reconnect, event masuk memperbarui
feed + invalidate query React Query terkait.
**Dep:** MAP-022, MAP-013
**AC:** hanya satu koneksi SSE meski banyak komponen memakainya; pindah workspace menutup
koneksi lama.

### MAP-025 · UI Activity feed & panel run · L · Engineer
`/w/[key]/activity`: feed live (agent, tiket, jenis event, waktu) dengan filter; klik run → panel
berisi output opencode, daftar tool call, blok ```map hasil parse, biaya, tombol Stop.
Plus indikator status agent (`idle`/`working`/`error`/`disabled`) di header, board, halaman agent.
**Dep:** MAP-024, MAP-023
**AC:** output muncul <1 detik sejak diterima backend; refresh tidak menghilangkan riwayat;
feed 1.000+ event tetap responsif; Stop mengubah status run jadi `cancelled`;
blok map yang gagal di-parse ditampilkan mencolok beserta alasannya.

### MAP-026 · Pemulihan run saat restart · S · Engineer
Saat startup, run `running`/`queued` ditandai `interrupted`, agent di-reset `idle`, komentar
sistem ditulis di tiket terkait.
**Dep:** MAP-023
**AC:** mematikan backend di tengah run lalu menyalakannya lagi meninggalkan nol run `running`
dan nol agent `working`.

---

## M3 — Otonomi

### MAP-027 · Modul guardrail · M · Lead
`core/guardrails.py`: JSON default di workspace, `check_guardrails()` sebelum schedule,
pemantauan saat berjalan (timeout, `max_cost_per_run`), akumulasi `ticket.cost_used`,
`max_cost_per_ticket`, `max_handoff_depth`, `max_concurrent_runs`.
Setiap blokir → tiket `blocked` + komentar sistem yang menyebut guardrail mana.
**Dep:** MAP-023
**AC:** menurunkan `run_timeout_sec` ke 5 membuat run berhenti dengan komentar sistem yang
menyebutkan itu; tidak ada jalur guardrail yang gagal tanpa komentar.

### MAP-028 · Loop detector · M · Engineer
Deteksi ping-pong dua agent pada satu tiket melebihi `loop_threshold` → `blocked` + komentar
sistem yang menuliskan siklusnya.
**Dep:** MAP-027
**AC:** rangkaian run A→B→A→B→A memicu blocked pada threshold 2; A→B→C→A tidak memicu.

### MAP-029 · Handoff engine · M · Lead
`mention` dari blok map (dan komentar manual owner) → schedule run untuk agent tujuan
(`trigger=handoff`/`mention`, `parent_run_id` terisi). Resolusi bila model menulis role, bukan
nama. Mention ke agent disabled atau nama tak dikenal → `blocked`/komentar sistem sesuai
[03-agent-design.md](03-agent-design.md) §6.
**Dep:** MAP-023, MAP-027
**AC:** rantai mention menaikkan `handoff_depth` dan berhenti di `max_handoff_depth`;
mention diri sendiri tidak memicu apa pun; status non-final tanpa mention valid → tiket `blocked`,
tidak menggantung.

### MAP-030 · Alur otonom penuh · M · Lead
`tickets[]` dari PM/QA/Pentester langsung terjadwal untuk assignee-nya. PM menutup epic saat
semua anak `done`. Run lanjutan untuk agent+tiket yang sama memakai `-s <session_id>`.
**Dep:** MAP-029, MAP-012
**AC:** satu epic yang dijalankan sekali berjalan sampai `done` atau `blocked` tanpa intervensi;
tidak ada tiket berstatus non-final tanpa run aktif dan tanpa antrean;
Engineer yang kembali ke tiket yang sama melanjutkan session opencode sebelumnya (diverifikasi
dari `run.session_id`).

### MAP-031 · Kill switch · S · Engineer
`POST /workspaces/{id}/pause` dan `/resume`. Pause: cancel semua run, terminate subprocess,
tandai `cancelled`, agent `idle`, `paused=true`, tolak schedule baru.
**Dep:** MAP-027
**AC:** pause saat 3 run berjalan menyisakan nol proses opencode dalam ≤5 detik (dicek `ps`);
schedule saat paused ditolak dengan pesan jelas, bukan diam-diam antre.

### MAP-032 · UI Settings workspace · M · Engineer
`/w/[key]/settings`: edit `repo_path`, nilai guardrail, tombol Pause/Resume (merah, dengan
konfirmasi), banner global saat paused, dan peringatan keamanan `--auto`
([02-tsd.md](02-tsd.md) §7) yang selalu terlihat.
**Dep:** MAP-031, MAP-013
**AC:** banner paused tampil di semua halaman workspace itu; ubah guardrail berlaku untuk run
berikutnya tanpa restart; peringatan keamanan tidak bisa disembunyikan.

### MAP-033 · Dogfood end-to-end · M · QA
Buat workspace ke repo contoh, 6 agent (PM, Lead, 2 Engineer, QA, Pentester), satu epic,
klik Run sekali, biarkan berjalan sampai selesai.
**Dep:** MAP-030, MAP-032, MAP-021
**AC:** epic mencapai `done` atau `blocked` dengan alasan jelas; feed memuat seluruh jejak;
Pause menghentikan semuanya di tengah jalan; restart backend tidak meninggalkan run menggantung;
tingkat kepatuhan blok ```map dicatat (berapa run gagal karena format) dan ditulis sebagai
laporan di `docs/07-dogfood-report.md`.
