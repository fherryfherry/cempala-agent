# ADR — Architecture Decision Records

Format: keputusan · konteks · konsekuensi · kapan ditinjau ulang.
Semua ADR berstatus **accepted** per 2026-08-22 kecuali disebutkan lain.

---

## ADR-001 · Next.js (frontend) + FastAPI (backend), dua service

**Keputusan.** Frontend Next.js App Router terpisah dari backend FastAPI Python.

**Konteks.** Alternatifnya Next.js fullstack. Ditolak karena inti backend adalah orkestrasi:
manajemen subprocess, streaming, `asyncio`, dan state machine. Python nyaman untuk itu.

**Konsekuensi.** Dua proses (ditutup MAP-005 `make dev`), CORS harus diurus, tipe API tidak
dibagi otomatis. Yang terakhir diterima apa adanya — tidak ada codegen OpenAPI→TS sampai
terbukti mengganggu.

**Catatan v0.2.** Argumen "Python nyaman untuk loop agent" melemah setelah [ADR-006](#adr-006):
kita tidak lagi menulis loop agent. Yang tersisa adalah manajemen subprocess dan orkestrasi —
masih nyaman di Python, tapi bukan lagi alasan yang menentukan. Keputusan dipertahankan karena
mengubahnya sekarang tidak menghasilkan apa pun.

**Tinjau ulang bila.** Backend mengecil sampai hanya CRUD + spawn subprocess, dan kamu lelah
menjalankan dua proses.

---

## ADR-002 · SQLite sebagai satu-satunya penyimpanan

**Keputusan.** SQLite lewat SQLAlchemy + Alembic. Tanpa Postgres, tanpa Redis.

**Konteks.** Aplikasi lokal, satu pengguna, ≤3 run bersamaan, ≤5.000 tiket. Satu file `map.db`
mudah di-backup, di-inspect, dan dihapus saat eksperimen gagal.

**Konsekuensi.** Concurrent write terbatas — mitigasi: WAL mode dan transaksi pendek. Volume
event menumpuk; butuh strategi retensi kalau DB membengkak. Alembic dipakai sejak awal supaya
migrasi ke Postgres nanti bukan penulisan ulang.

**Tinjau ulang bila.** Portal keluar dari laptop, atau tabel `event` melewati beberapa GB.

---

## ADR-003 · SSE, bukan WebSocket

**Keputusan.** Realtime lewat Server-Sent Events satu arah per workspace.

**Konteks.** Semua realtime di sini server→client (output opencode, tool call, perubahan status).
Aksi client sudah tercakup REST. SSE punya reconnect otomatis dan `Last-Event-ID` bawaan —
persis yang dibutuhkan untuk melanjutkan feed setelah putus.

**Konsekuensi.** Tidak bisa push dari client lewat kanal yang sama (tidak dibutuhkan). Batas 6
koneksi per domain di HTTP/1.1 — karena itu **satu** EventSource per workspace di React context,
bukan satu per komponen (MAP-024).

**Tinjau ulang bila.** Muncul kebutuhan bidirectional, mis. owner mengintervensi run berjalan.

---

## ADR-004 · Orchestrator in-process (`asyncio.Task`), bukan Celery/Redis

**Keputusan.** Tiap run adalah `asyncio.Task` yang mengelola satu subprocess opencode, di dalam
proses FastAPI yang sama.

**Konteks.** Run bersifat I/O-bound (menunggu subprocess). Antrean terdistribusi berarti menambah
broker, worker, serialisasi state, dan satu kelas bug baru — untuk ≤3 run bersamaan di satu laptop.

**Konsekuensi.** Restart backend membunuh semua run berjalan — ditutup MAP-026 yang menandainya
`interrupted` dan meninggalkan komentar sistem. Tidak bisa menskala ke banyak mesin. Seluruh
jalur wajib `async`, dan subprocess wajib `asyncio.create_subprocess_exec`.

**Tinjau ulang bila.** Butuh >20 run bersamaan, atau run harus selamat dari restart.

---

## ADR-005 · Tanpa auth, single user

**Keputusan.** Tidak ada login, user, atau RBAC di MVP.

**Konteks.** Berjalan di `localhost` milik satu orang. Auth adalah pekerjaan nyata yang tidak
menambah satu pun kemampuan yang diminta.

**Konsekuensi.** Backend bind ke `127.0.0.1` dan **tidak boleh** di-expose ke jaringan.
Konsekuensi ini jauh lebih tajam setelah [ADR-010](#adr-010): endpoint tanpa auth yang bisa
menjalankan perintah arbitrer di mesinmu. README menyatakan ini eksplisit. Semua "siapa yang
melakukan" saat ini `agent_id` atau NULL (= owner); menambah `user_id` nanti berarti migrasi,
bukan penulisan ulang.

**Tinjau ulang bila.** Ada orang kedua yang memakainya, atau portal di-deploy ke mesin yang
bisa dijangkau orang lain.

---

<a id="adr-006"></a>
## ADR-006 · Tidak membangun coding agent sendiri — lempar ke opencode

**Keputusan.** Portal tidak punya loop tool-calling, tool filesystem, atau klien LLM. Setiap run
adalah satu proses `opencode` yang menerima prompt dan mengembalikan hasil. `AgentTool` punya
satu implementasi aktif: `OpenCodeTool`. (Menggantikan rencana v0.1 yang punya `SelfTool`.)

**Konteks.** v0.1 merencanakan coding agent sendiri: loop tool-calling ke Ollama, `read_file`,
`write_file`, `edit_file`, `search`, `run_command` beserta allowlist dan path jail. Itu adalah
membangun ulang opencode — proyek tersendiri, dengan permukaan bug dan permukaan keamanan
tersendiri — di dalam proyek yang sebenarnya soal orkestrasi, bukan soal coding agent.

Yang membuat portal ini bernilai adalah tiket, handoff, guardrail, dan visibilitas. Bukan
kualitas loop agent-nya. Loop agent yang bagus sudah ada dan gratis.

**Konsekuensi.**
- 8 tiket hilang (klien Ollama, loop tool-calling, tool filesystem, tool ticketing, path jail,
  command allowlist). ~9 hari kerja hilang dari MVP.
- Kita kehilangan kendali atas apa yang terjadi di dalam run: tidak ada step cap, tidak ada
  kontrol per tool. Guardrail yang tersisa adalah waktu, biaya, dan topologi handoff
  ([02-tsd.md](02-tsd.md) §6).
- Kita kehilangan path jail. Sandbox jadi urusan opencode, dan opencode tidak menyediakannya
  ([ADR-010](#adr-010)).
- Agent jadi kotak hitam yang tidak bisa memanggil API tiket kita → butuh kontrak balik
  ([ADR-009](#adr-009)).
- Portal jadi tergantung penuh pada satu binary eksternal beserta cara autentikasinya.
- Kualitas hasil coding naik, tanpa usaha dari kita.

**Tinjau ulang bila.** Muncul kebutuhan kendali halus di dalam run (mis. menolak tool tertentu
per role) yang tidak bisa dicapai lewat prompt. Itu sinyal untuk MCP server, bukan untuk
membangun ulang loop agent.

---

<a id="adr-007"></a>
## ADR-007 · Adapter pattern dipertahankan meski hanya satu implementasi aktif

**Keputusan.** Protokol `AgentTool.run(ctx) -> AsyncIterator[Event]` tetap ada, dengan
`OpenCodeTool` sebagai satu-satunya implementasi nyata dan `StubTool` untuk
`claude`/`agy`/`codex`.

**Konteks.** Abstraksi dengan satu implementasi biasanya over-engineering. Di sini ia dibayar
oleh dua hal konkret: (a) user secara eksplisit meminta pilihan tool per agent, jadi enum-nya
tetap ada di UI apa pun yang terjadi; (b) `StubTool` harus gagal dengan rapi lewat jalur yang
sama, bukan lewat `if` khusus di orchestrator.

Kalau bukan karena itu, `OpenCodeTool` cukup dipanggil langsung.

**Konsekuensi.** UI menampilkan tool yang belum tersedia sebagai disabled, bukan diam-diam gagal
(MAP-021). Bentuk `Event` saat ini dibentuk oleh format JSON opencode; adapter kedua kemungkinan
memaksa protokolnya berubah — itu wajar dan murah selagi pemakainya sedikit.

**Tinjau ulang bila.** Adapter ketiga masuk, atau setahun berlalu tanpa adapter kedua (saat itu
hapus abstraksinya).

---

## ADR-008 · Tabel `event` sebagai satu-satunya sumber aktivitas

**Keputusan.** Setiap kejadian dipersist ke `event` **sebelum** disiarkan ke subscriber SSE.
Feed live dan replay setelah refresh membaca tabel yang sama.

**Konteks.** Alternatif yang lebih murah: siarkan dari memori, simpan ringkasannya saja. Itu
berarti apa yang kamu lihat live berbeda dari apa yang bisa kamu baca ulang — tepat saat sesuatu
berjalan aneh dan kamu paling butuh jejaknya. Dengan agent sebagai kotak hitam
([ADR-006](#adr-006)), jejak ini adalah satu-satunya cara memahami kenapa sebuah run gagal.

**Konsekuensi.** Volume tulis tinggi dari output opencode. Mitigasi MVP: batch insert per ~100 ms.
Butuh strategi retensi nanti ([ADR-002](#adr-002)). Imbalannya: setiap run bisa direplay penuh.

**Tinjau ulang bila.** Beban tulis mengganggu responsivitas — opsi pertama: berhenti mem-persist
event teks mentah, tetap persist `tool_call`/`status_change`/`comment`/`error`.

---

<a id="adr-009"></a>
## ADR-009 · Kontrak balik lewat blok ```map, bukan MCP server

**Keputusan.** Agent melapor balik dengan menutup jawabannya memakai satu blok ```map berisi YAML
(`status`, `mention`, `summary`, `tickets[]`). Orchestrator mem-parse dan mengeksekusinya.

**Konteks.** opencode adalah kotak hitam ([ADR-006](#adr-006)) — ia tidak bisa memanggil API tiket
kita. Tiga opsi: (a) blok terstruktur di akhir output, (b) MCP server berisi tool ticketing yang
dikonfigurasi ke opencode, (c) pass kedua ke LLM untuk menyimpulkan hasil.

(c) ditolak: menambah biaya dan satu lapisan lagi yang bisa salah tafsir, untuk masalah yang bisa
diselesaikan dengan format. (b) lebih rapi dan menghilangkan risiko format sepenuhnya, tapi
menambah server MCP, konfigurasi opencode per run, dan permukaan debugging baru — sebelum kita
tahu apakah alur otonomnya sendiri berfungsi. (a) tidak menambah infrastruktur sama sekali.

**Konsekuensi.**
- **Ini risiko teknis terbesar di MVP.** Kalau model tidak patuh format, alurnya berhenti.
  Mitigasi: format sesederhana mungkin, kontraknya diulang di akhir tiap prompt, dan kegagalan
  parse **selalu** memblokir tiket dengan potongan output aslinya — tidak pernah ditebak, tidak
  pernah diam ([02-tsd.md](02-tsd.md) §4.3).
- Agent hanya bisa melapor **di akhir**, bukan di tengah kerja. PM tidak bisa membuat tiket
  sambil berpikir; ia harus mengumpulkan semuanya ke satu blok penutup.
- MAP-033 mengukur tingkat kepatuhan format sebagai angka. Angka itu yang memutuskan apakah
  MCP naik prioritas.

**Tinjau ulang bila.** Kepatuhan format di dogfood buruk, atau muncul kebutuhan agent melapor
di tengah run. Keduanya mengarah ke opsi (b).

---

<a id="adr-010"></a>
## ADR-010 · Menerima `--auto` tanpa sandbox

**Keputusan.** opencode dijalankan dengan `--auto` (menyetujui semua permission) dan `--dir
<repo_path>`. Tidak ada container, tidak ada sandbox. Risikonya didokumentasikan, bukan dimitigasi.

**Konteks.** Tidak ada manusia yang menyetujui permission dialog di alur otonom, jadi `--auto`
wajib. Alternatifnya menjalankan tiap run di dalam Docker dengan repo di-mount — aman, tapi
menambah build image, autentikasi opencode di dalam container, dan debugging yang jauh lebih
repot, untuk aplikasi yang berjalan di laptop pemiliknya sendiri pada repo miliknya sendiri.

**Konsekuensi — sebutkan terus terang.**
- Agent bisa menjalankan perintah apa pun dengan hak akses user yang menjalankan backend.
- `--dir` menetapkan working directory, **bukan** batas. Tidak ada yang mencegah agent menyentuh
  file di luar `repo_path`. Validasi `repo_path` di API adalah kenyamanan, bukan keamanan.
- Karena itu: backend bind `127.0.0.1` ([ADR-005](#adr-005)); peringatan eksplisit di README
  (MAP-001) dan di halaman settings yang tidak bisa disembunyikan (MAP-032); jangan menaruh
  secret produksi di dalam `repo_path`; jalankan hanya pada repo yang kamu percayai.
- Kill switch karenanya bukan fitur kenyamanan melainkan kontrol keamanan utama, dan harus
  benar-benar mematikan proses anak — diverifikasi dengan `ps`, bukan dengan status di DB (MAP-031).

**Tinjau ulang bila.** Portal dipakai pada repo pihak ketiga, di mesin bersama, atau oleh orang
selain pemiliknya. Ketiganya langsung membuat sandbox jadi wajib.

---

<a id="adr-011"></a>
## ADR-011 · MCP server untuk akses tiket/artifacts/memory, bukan untuk coding

**Keputusan.** Setiap run opencode diberi satu MCP server lokal (`app/mcp_server.py`) lewat
config `opencode.json` per run (`OPENCODE_CONFIG`), yang membuka tool baca/tulis tiket,
artifacts, dan memory agent ke agent — diproksi ke backend HTTP (`MAP_API_BASE`, default
`127.0.0.1:8000/api`). Tool yang disediakan: `list_tickets`, `get_ticket`, `post_comment`,
`create_ticket`, `update_ticket`, `list_artifacts`, `read_artifact`, `get_memory`,
`create_memory`, `update_memory`.

**Konteks.** Run rutinitas sebelumnya bergantung sepenuhnya pada prompt: agent tidak punya cara
membaca status tiket (Board) atau menulis komentar follow-up, jadi rutinitas "cek tiket macet
lalu follow up" gagal — agent menolak menebak status. ADR-009 memilih blok ```map daripada MCP
karena infrastruktur MCP belum terbukti perlu; kegagalan dogfood membuktikan kebutuhannya.

**Konsekuensi.**
- MCP server hanya STDIO subprocess per run (bukan TCP) — tidak menambah permukaan jaringan.
  Server di-spawn opencode sebagai child process dengan env `MAP_WORKSPACE_ID`/`MAP_AGENT_ID`,
  jadi tiap tool otomatis ter-scope ke workspace dan agent yang sedang berjalan.
- Semua validasi tetap di backend (state machine, role gate, mention, approval PM). MCP server
  hanyalah proksi HTTP tipis; tidak ada duplikasi logika bisnis.
- Tidak ada auth di MCP (ADR-005): MCP server tidak bisa diakses dari luar, hanya bisa di-spawn
  oleh backend sendiri.
- `update_ticket`/`post_comment` otomatis dikaitkan ke agent berjalan (`actor_agent_id`/
  `author_agent_id`) sehingga aktivitas tetap tercatat per agent.
- `create_ticket` membuat tiket backlog tanpa auto-schedule — agent bebas bikin backlog tanpa
  memicu run. Tanpa `epic` (parameter opsional, ADR-012) tiket ini jadi epic top-level baru;
  dengan `epic` diisi, tiket ini nempel sebagai anak epic yang sudah ada.
- Menggantikan kebutuhan sementara untuk menyuntikkan daftar tiket ke prompt rutinitas
  (pendekatan yang ditolak karena membengkakkan prompt dan tetap buta terhadap komentar) —
  prompt rutinitas kembali ringkas, agent membaca Board lewat tool.

**Tinjau ulang bila.** Format MCP di dogfood buruk (agent tidak menemukan/memakai tool) atau
kebutuhan interaksi tengah-run muncul (saat itu: MCP server yang lebih kaya, bukan heuristik).

---

## ADR-012 · Epic tetap `Ticket` (reusable), sprint murni timebox

**Keputusan.** "Epic" tidak jadi entity baru — tetap `Ticket` dengan `parent_id IS NULL`, sama
seperti sebelumnya. Yang berubah: epic sekarang **persistent/reusable** secara desain, bukan
container sekali pakai per request. Tiga mekanisme baru menegakkan ini:

1. Katalog epic (top-level tickets, ~100 terbaru diupdate) dan katalog sprint (semua nama)
   di-inject ke kontrak ```map untuk role yang boleh `tickets[]` (pm/qa/pentester), dengan aturan
   WAJIB reuse — pola yang sama persis dengan katalog Artifact Groups (ADR di sekitar
   `_map_contract_block`'s `groups_rule`, lihat docs/03-agent-design.md §3).
2. Field baru `tickets[].epic` (blok ```map) dan parameter baru `create_ticket(epic=...)` (MCP
   tool, ADR-011) — dua-duanya membiarkan agent menempelkan tiket baru ke epic yang sudah ada,
   bukan selalu jadi anak dari tiket yang sedang dikerjakan.
3. Sprint ditegaskan sebagai **timebox murni** — instruksi lama yang meminta PM menyebutkan
   "fokus tiap sprint" dihapus (itu penyebab nama sprint kebobolan nama fitur, mis. "Sprint 2 -
   Kualitas & Keamanan Artikel"). Scope/fitur sekarang eksklusif urusan epic.

**Konteks.** Sebelum ini: satu-satunya cara membuat tiket top-level adalah `is_new_epic: true`
(API manual) atau default `tickets[]`/`create_ticket` (agent) — keduanya selalu bikin epic baru,
tidak pernah reuse. Efeknya, tiap request owner (lewat chat atau lewat MCP) membuat epic
sendiri-sendiri, dan epic tidak pernah terpakai lagi sebagai parent untuk tiket berikutnya —
bertentangan dengan model yang diinginkan: workspace/project → epic (area fitur besar,
reusable) → feature/story/bug/enhancement.

Dua alternatif ditolak:
- **Epic jadi entity baru** (tabel sendiri, tanpa status/board column) — lebih "benar" secara
  konsep tapi migrasi besar: tabel baru, API baru, migrasi semua tiket top-level lama, halaman
  manajemen Epic baru. Ditolak untuk MVP fitur ini — cukup epic tetap `Ticket`, ditambah tooling
  reuse di atasnya.
- **Halaman "Epics" baru** (seperti Artifacts) — ditolak, cukup perbaiki dropdown Epic yang
  sudah ada di Create Ticket dialog + badge yang sudah ada di Board/Timeline.

Aturan reuse ditaruh di kontrak ```map (kode), bukan `workflow_prompt` per-workspace (Settings),
karena dua alasan: (a) katalog epic/sprint butuh data live yang hanya orchestrator bisa query —
field teks statis tidak bisa; (b) blok kontrak selalu dirakit PALING TERAKHIR di prompt (setelah
`workflow_prompt`), jadi aturan ini tidak bisa diam-diam ditimpa oleh workflow_prompt custom
milik workspace.

Bug yang ditemukan sekaligus ditambal: nesting 1-level (`_validate_parent`, `nesting_too_deep`)
hanya ditegakkan di jalur API manual, tidak pernah di jalur `tickets[]` agent — QA/Pentester yang
melapor bug dari tiket yang sudah punya parent (feature/story di bawah epic) diam-diam membuat
cucu (2 level). Ditambal dengan resolusi default baru: tanpa `epic:` eksplisit, tiket baru
menempel ke `ticket.parent_id` kalau ada (bukan `ticket.id`) — tetap flat di bawah epic yang sama.

**Konsekuensi.**
- `TicketDraft.epic` (parser, `core/report.py`) dan helper `_resolve_ticket_parent`/
  `_resolve_epic_target` (orchestrator) — key tak dikenal atau bukan epic top-level di-skip
  dengan catatan di komentar sistem, tidak menggagalkan seluruh laporan (toleransi yang sama
  seperti field lain di blok ```map).
- PM's "final plan" di fase eksploratif chat (sebelum owner approve) sekarang wajib menyebut
  epic tujuan secara eksplisit — bagian dari lima bagian wajib (requirement, goal, epic tujuan,
  breakdown sprint, estimasi durasi), owner request di luar audit awal.
- `_maybe_wake_parent_pm`'s asumsi "epic selalu ditutup begitu semua anak selesai" dilunakkan di
  prompt (bukan kode): PM boleh membiarkan epic tetap terbuka kalau memang area fitur besar yang
  masih akan menerima tiket baru.

**Tinjau ulang bila.** Katalog epic tumbuh sangat besar (ratusan epic) sehingga daftar ~100
teratas tidak lagi cukup mewakili, atau owner butuh metadata epic yang tidak bisa ditumpangkan ke
`Ticket` (mis. deskripsi terstruktur, tag, target rilis) — saat itu barulah entity `Epic` terpisah
masuk akal.

## ADR-013 · Agent hanya boleh dijadwalkan untuk ticket di sprint aktif

**Keputusan.** Guardrail baru, `ticket_not_in_active_sprint`, dicek di `check_guardrails()`
(`core/guardrails.py`) sebelum `Run` dibuat — di titik ini semua 6 jalur penjadwalan (manual,
retry, mention, handoff, auto tickets[], wake-parent-PM) sudah lewat. Aturan: ticket yang
`sprint_id`-nya `NULL` (backlog) atau menunjuk sprint yang bukan `status == "active"` tidak bisa
dijalankan — kena `blocked` + komentar sistem, sama seperti guardrail lain. **Dikecualikan**: role
apa pun yang ada di `workspace.sprint_creator_roles` (default hanya `pm`) — role itu bertugas
merencanakan sprint, jadi harus selalu bisa merespon ticket apa pun (termasuk ticket baru dari
chat yang belum ditriage ke sprint manapun) untuk melakukan triage tersebut. Guardrail ini selalu
aktif, tidak ada toggle di `workspace.guardrails`/Settings (permintaan owner: aturan ini adalah
kebijakan kerja tim, bukan limit yang perlu di-tune per workspace).

**Konteks.** Permintaan owner: PM yang mengatur kapan sprint berikutnya aktif (lewat mekanisme
`PATCH /sprints/{id}` yang sudah ada, ADR di [03-agent-design.md](03-agent-design.md) §4); agent
lain hanya boleh mengerjakan apa yang ada di sprint yang sedang aktif itu — supaya tim tidak
diam-diam mengerjakan ticket dari sprint yang belum waktunya (atau ticket yang belum pernah
ditriage sama sekali) sementara sprint aktifnya sendiri belum kelar.

Konsekuensi tersembunyi yang ditemukan sekaligus ditambal saat implementasi: alur chat "mulai
obrolan baru dengan PM" (`frontend/app/w/[key]/chat/page.tsx`) membuat ticket baru **tanpa
sprint** lalu langsung `@mention` PM — tanpa pengecualian role di atas, PM sendiri akan langsung
terblokir di percakapan pertama, sebelum sempat mengatur sprint apa pun. Ini alasan langsung
kenapa pengecualian dilekatkan ke `sprint_creator_roles` (konsep yang sudah ada, dipakai untuk hal
lain: siapa yang boleh mendeklarasikan `sprints:` di blok ```map) daripada hardcode role `"pm"`.

Dua alternatif ditolak:
- **Backlog dikecualikan, hanya sprint non-aktif yang diblokir** — lebih sederhana (tidak perlu
  memikirkan alur chat di atas sama sekali), tapi bertentangan dengan keputusan owner: backlog
  (belum ditriage ke sprint manapun) *lebih* belum-siap-dikerjakan dibanding sprint yang sudah
  direncanakan tapi belum aktif, jadi seharusnya ikut diblokir juga, bukan malah dikecualikan.
- **Guardrail dikonfigurasi per workspace** (field baru di `workspace.guardrails`, toggle di
  Settings) — mengikuti pola guardrail lain, tapi owner secara eksplisit tidak minta ini bisa
  dimatikan; menambah toggle untuk sesuatu yang belum diminta opt-out-able cuma menambah
  permukaan UI/API tanpa kebutuhan nyata.

**Konsekuensi.**
- `core/orchestrator.py::schedule()` meneruskan `agent.role` dan `workspace.sprint_creator_roles`
  ke `check_guardrails()` — dua parameter baru, keyword-only, default `None`/`[]` supaya tidak
  breaking untuk pemanggil lain.
- Fixture `_make_ticket` di hampir semua file test orkestrator (`test_orchestrator.py`,
  `test_guardrails.py`, `test_handoff.py`, `test_kill_switch.py`, `test_loop_detector.py`,
  `test_run_retry_api.py`, `test_agent_memory_orchestrator.py`) sekarang membuat/reuse sprint aktif
  workspace secara default kecuali `sprint_id` di-override eksplisit — beberapa test yang
  mengaudit isi list sprint (`test_updates_sprint_and_duration_apply_to_target`,
  `test_pm_tickets_with_sprint_creates_and_links_sprint`,
  `test_sprint_creator_roles_setting_gates_sprints_declaration`) disesuaikan supaya tidak
  terpengaruh sprint bootstrap ini.
- `_get_or_create_sprint` (orchestrator, agent-facing) tidak berubah — dates/status masih
  sepenuhnya di luar kendali agent (lihat catatan sprint start/end date terpisah).

**Tinjau ulang bila.** Owner ingin agent lain (bukan hanya `sprint_creator_roles`) bisa merespon
ticket di luar sprint aktif untuk kasus tertentu (mis. hotfix darurat) — saat itu guardrail ini
mungkin perlu jalur bypass baru yang eksplisit, bukan pengecualian role yang sudah ada.
