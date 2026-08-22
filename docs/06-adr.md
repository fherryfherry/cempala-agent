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
