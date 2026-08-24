# Agent Design — Peran, Prompt, Handoff

Versi 0.2 · MVP
Pendamping: [02-tsd.md](02-tsd.md) §4–§6

> **Perubahan v0.2.** Agent tidak punya tool dari kita. Setiap agent adalah satu proses opencode
> yang menerima prompt dan mengembalikan hasil. Yang dulu "tool set per role" sekarang jadi
> **hak per role di dalam blok ```map** — dan hak itu ditegakkan di kode kita, bukan dipercayakan
> ke model.

## 1. Prinsip

1. **Satu agent, satu tanggung jawab.** Engineer tidak menutup tiket. QA tidak menulis fitur.
   PM tidak menyentuh kode.
2. **Komunikasi lewat tiket.** Agent tidak saling kirim pesan; mereka menutup jawabannya dengan
   blok ```map berisi `summary` dan `mention`. Semua jejak terbaca manusia.
3. **Handoff = `status` + `mention` dalam satu blok.** Tidak ada mekanisme lain.
4. **Batasan role ditegakkan di parser.** Kalau Engineer menulis `status: done`, parser menolaknya.
   Prompt hanya membuat model jarang mencobanya.

## 2. Prompt bersama

Setiap system prompt = `BASE` + blok role + konteks tiket + kontrak ```map.

```
BASE:
Kamu adalah {name}, seorang {role} di tim software yang bekerja di repo pada {repo_path}.

Kamu bekerja lewat sistem tiket. Aturan yang tidak bisa ditawar:
- Kerjakan HANYA tiket yang diberikan padamu. Jangan mengambil pekerjaan lain.
- Kalau kamu butuh orang lain, sebut mereka di `mention`. Jangan mengerjakan bagian mereka.
- Kalau kamu terjebak atau kekurangan informasi, gunakan status `blocked` dan jelaskan apa yang
  kamu butuhkan. Jangan menebak lalu melanjutkan.
- Ringkas. `summary` bukan esai.
- Kalau kamu selesai, berhenti. Jangan mencari pekerjaan tambahan.

Anggota tim di workspace ini:
{daftar agent: nama, role}

Tiket saat ini:
{key} — {title}
Status: {status} | Prioritas: {priority}
{description}

Lampiran: {daftar file yang disertakan}

Komentar terakhir:
{5 komentar terakhir}

Hasil kerja sebelumnya di tiket ini:
{summary dari run-run terdahulu}
```

Penutup wajib setiap prompt (kontrak ```map, [02-tsd.md](02-tsd.md) §4.3):

````
Akhiri jawabanmu dengan TEPAT SATU blok berikut. Tanpa blok ini pekerjaanmu dianggap gagal
dan tiket akan diblokir.

```map
status: <salah satu dari: {status legal untuk role ini}>
mention: [<nama agent dari daftar tim di atas>]
summary: |
  <apa yang kamu kerjakan, file apa yang tersentuh, dan bukti bahwa itu jalan>
{blok tickets[] hanya disertakan untuk PM, QA, Pentester}
```
````

## 3. Hak per role dalam blok ```map

| Role | `status` yang boleh | `tickets[]` | Menyentuh kode |
|---|---|---|---|
| PM | status apa pun kecuali `release` | **ya** (wajib disetujui owner dulu di chat; lihat §4) | tidak |
| Lead Engineer | status apa pun kecuali `release` | tidak | tidak |
| Engineer | status apa pun kecuali `release` | tidak | ya |
| Designer | status apa pun kecuali `release` | tidak | ya |
| QA | status apa pun kecuali `release` | **ya** (bug) | hanya file test |
| Pentester | status apa pun kecuali `release` | **ya** (temuan) | tidak |

**Update:** per permintaan owner, matrix status-per-role yang lama (mis. Engineer cuma boleh
`review`/`blocked`, Lead cuma boleh `qa`/`in_progress`/`blocked`) sudah dihapus — matrix itu
sering memblokir perpindahan status yang sebetulnya wajar (mis. Lead memindahkan tiket yang
sudah `done` balik ke `qa`). Sekarang tiap role bebas mendeklarasikan status apa pun di blok
```map, dan boleh berpindah dari status manapun ke status manapun (§5) — satu-satunya
pengecualian adalah `release`, yang tetap aksi manual owner (lihat di bawah).

Kolom "menyentuh kode" adalah instruksi di prompt, bukan penegakan teknis — opencode berjalan
dengan `--auto` dan bisa menulis apa saja ([02-tsd.md](02-tsd.md) §7). Kolom `tickets[]`
ditegakkan parser.

Tiap sub-tiket dari `tickets[]` boleh membawa `category` opsional
(`feature | improvement | fix | security | performance`) — tampil sebagai badge di kanban.
Nilai di luar daftar diabaikan.

**Judul tiket harus rapi, bukan teknis.** `title` di `tickets[]` (dipakai PM, QA, Pentester)
wajib ringkas dan mudah dibaca orang non-teknis — dilarang mencantumkan path file, nama
fungsi/variabel, potongan kode, atau nomor tiket lain di title. Detail teknis (file yang
disentuh, langkah reproduksi, dsb.) masuk ke `description`. Ini instruksi di prompt (kontrak
```map — lihat contoh format di §2), bukan penegakan parser, sama seperti kolom "menyentuh kode"
di atas.

Status `release` (kolom kanban setelah `done`, menandai tiket sudah dirilis) sengaja **tidak**
ada di daftar `status` yang boleh dideklarasikan role mana pun di atas — bukan sesuatu yang agent
putuskan lewat blok ```map. Hanya owner (dan PM lewat wildcard transisinya, §5) yang bisa
memindahkan tiket dari `done` ke `release`, lewat aksi manual di Board.

PM boleh (opsional) menyertakan `sprint`/`duration` per item `tickets[]`, dan blok top-level
`sprints:` (nama, fokus/`goal`, `duration`) untuk mendeklarasikan atau memperbarui sprint —
lihat §4. **Role mana yang boleh mendeklarasikan `sprints:` diatur per workspace** lewat
setting `sprint_creator_roles` di halaman Settings (pill picker; default PM saja) — ditegakkan
di parser, dan kontrak ```map hanya mengajarkan field ini ke role yang diizinkan.

**`artifacts[]`** — beda dari `tickets[]`, field ini terbuka untuk **semua role**: siapa pun
boleh mendeklarasikan file yang ia hasilkan di repo (path relatif ke repo + nama kelompok)
supaya tampil di menu Artifacts. Nama kelompok tidak bebas: blok ```map menyertakan daftar
kelompok yang sudah ada, dan agent wajib memakai yang relevan (membuat baru hanya kalau tidak
ada yang cocok) supaya tidak ada duplikat/ambigu — orchestrator tetap get-or-create
case-insensitive sebagai jaring pengaman terakhir. Orchestrator (bukan parser) yang membaca
file itu dari
`repo_path` dan menyalinnya ke `storage/attachments/` — lihat [02-tsd.md](02-tsd.md) §4.3.
PM's kolom "menyentuh kode" di tabel atas tetap "tidak" untuk kode/test, tapi PM sekarang
boleh menulis dokumen non-kode (PRD) — lihat perubahan prompt PM di §4.

**Membaca/mencari artifacts.** Setiap prompt (semua role) menyertakan katalog artifacts yang
sudah dipublikasikan di workspace (paling baru ~100, format `[kelompok] filename (KEY) —
deskripsi`). Agent diharapkan membaca/mencari katalog ini sebelum membuat file baru, supaya
tidak menduplikasi dokumen yang sudah ada. Isi file tidak ikut di prompt — kalau agent butuh
isinya, ia baca file aslinya di `repo_path` lewat tool opencode yang sudah ada (yang di
`storage/attachments/` hanyalah salinan).

**`artifact_updates[]`** — **HANYA PM** (ditegakkan di parser, sama seperti `tickets[]`):
merapikan menu Artifacts. Empat operasi: `rename` (group→to; kalau `to` sudah ada, otomatis
jadi merge), `merge` (from→into, sumber dihapus), `move` (satu file antar kelompok), `delete`
(hanya kelompok kosong — yang masih berisi file ditolak). Dieksekusi orchestrator setelah
`_publish_artifacts` pada report yang sama; kelompok/file tak ditemukan dicatat di komentar
sistem tanpa menggagalkan report. PM memakai ini untuk merapikan kelompok yang ambigu/duplikat
yang terlanjur dibuat agent lain.

**`memory[]`** (MAP-035) — juga terbuka untuk **semua role**, sama seperti `artifacts[]`: daftar
catatan singkat yang agent itu sendiri mau ingat lintas tiket, supaya run-run berikutnya
(tiket apa pun, bukan cuma yang sedang dikerjakan) tidak mengulang kesalahan/kegagalan yang
sama. Disimpan per `agent_id` (bukan per tiket) ke tabel `agent_memory`. Sengaja **bukan**
retrieval dari histori tiket lama (lihat catatan risiko halusinasi di
[05-roadmap.md](05-roadmap.md) butir 7) — hanya catatan verbatim yang agent tulis sendiri,
dan yang di-inject ke prompt berikutnya dibatasi jumlahnya (paling baru ~20 entri). Owner bisa
melihat, menambah manual, dan menghapus catatan lewat tombol "Memory" di halaman
`/w/[key]/agents` — penghapusan ini satu-satunya cara mengoreksi catatan yang salah/usang.

**`epic` pada `tickets[]`** (ADR-012) — terbuka untuk **semua role** yang boleh `tickets[]`
(pm/qa/pentester), sama seperti `artifacts[]`: field opsional berisi key epic (tiket top-level)
tujuan. Epic adalah area fitur besar di proyek yang **dipakai berkali-kali** sebagai parent
untuk tiket feature/story/bug/enhancement ke depannya — bukan container sekali pakai per
request. Blok ```map menyertakan katalog epic yang sudah ada (pola sama seperti katalog
`artifacts:`), dan agent **WAJIB** memilih yang relevan; hanya boleh mengosongkan `epic:` kalau
memang area fitur besar yang benar-benar baru — tiket yang sedang dikerjakan itu sendiri akan
jadi epic baru (behavior lama, tidak berubah kalau `epic:` tidak diisi dan tiket ini memang
tidak punya parent).

Resolusi tanpa `epic:` eksplisit: kalau tiket yang sedang dikerjakan **sudah punya parent**
(mis. QA/Pentester melapor bug dari tiket feature/story di bawah epic), tiket baru menempel ke
parent itu (sibling di bawah epic yang sama) — **bukan** jadi anak dari tiket yang sedang
dikerjakan. Ini menjaga invarian flat 1-level (§3 tabel di atas) yang sebelumnya hanya
ditegakkan di jalur API manual, tidak di jalur agent. `epic:` yang menyebut key tak
dikenal/bukan epic top-level di-skip dengan catatan di komentar sistem, jatuh ke resolusi
default — tidak menggagalkan seluruh laporan.

Tool MCP `create_ticket` (§3b, ADR-011) punya parameter opsional `epic` yang setara — aturan
reuse yang sama berlaku, dan `list_tickets` menandai tiket top-level dengan `[EPIC]` supaya
agent bisa menemukan kandidat reuse tanpa perlu membaca prompt.

## 3b. Rutinitas (scheduled agent tasks)

Rutinitas (menu `/w/[key]/routines`) adalah tugas terjadwal yang menjalankan agent **tanpa
tiket**: owner menulis prompt tugas, interval, mode, dan agent. Scheduler in-process
(`core/routine_scheduler.py`) tick tiap 60 detik dan memicu rutinitas yang jatuh tempo.

- **Mode `idle_only`**: hanya jalan kalau agent sedang `idle` (tidak ada run berjalan). Kalau
  sibuk, tick dilewati dan `last_run_at` dimajukan (tidak retry tiap tick).
- **Mode `consistent`**: kalau agent sibuk, run masuk antrean FIFO agent (mekanisme
  `_PENDING`/`_BUSY` yang sama dengan run tiket) — tidak pernah terlewat.
- Status rutinitas: `idle` → `waiting` (terjadwal/antre) → `running` → `idle`; `disabled`
  dimatikan owner. Workspace `paused` → semua dilewati. `max_concurrent_runs` tetap berlaku.
- Run rutinitas (`Run.ticket_id = NULL`, `trigger = "routine"`) memakai kontrak ```map khusus:
  **tanpa `status`/`mention`** (ditolak parser → run `failed`). Aksi yang diizinkan:
  `comments[]` (komen ke tiket lain, author = agent), `tickets[]` (backlog `todo`, tidak
  auto-schedule), `updates[]`, `memory[]`, `artifact_updates[]` (PM). Tidak ada transisi
  status tiket apa pun — tiket yang dikomen/di-update tidak berubah statusnya kecuali lewat
  `updates[].status` eksplisit.
- **Agent membaca Board lewat MCP, bukan prompt** (ADR-011): tiap run — rutinitas maupun
  tiket — mendapat MCP server lokal dengan tool `list_tickets`/`get_ticket`/`post_comment`/
  `create_ticket`/`update_ticket`/`list_artifacts`/`read_artifact`/`get_memory`/
  `create_memory`/`update_memory`. Prompt rutinitas tidak perlu menyuntikkan daftar tiket;
  agent memanggil tool untuk melihat status/umur tiket dan menulis komentar follow-up.
- Contoh use case: rutinitas PM "cek tiket macet" tiap 5 menit (idle_only) — PM memanggil
  `list_tickets`, menemukan tiket yang `updated_at`-nya sudah lama, lalu `post_comment`
  follow-up ke assignee-nya. Aksi via MCP tidak memicu run (komentar agent tidak trigger
  handoff — hanya `mention`/`comments[]` di blok ```map yang trigger).

## 4. Role

### PM (`pm`) — satu per workspace

```
Kamu Project Manager. Kamu TIDAK menulis atau mengubah kode/test. Kamu BOLEH menulis dokumen
perencanaan (PRD) di repo — tidak lebih.

Chat owner (tiket yang dimulai dari chat):
- WAJIB eksploratif dulu — gali informasi yang detail (tujuan, lingkup, kriteria sukses)
  tapi jangan berlebihan. Tawarkan PLAN dulu di summary, JANGAN langsung tickets[].
- Owner menyetujui plan dengan membalas kata setuju di chat (mis. "oke lanjut").
- Kamu boleh chat owner duluan kapan pun ada hal yang butuh klarifikasi.

Kalau tiket ini epic (belum punya sub-tiket) dan sudah disetujui:
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
```

Catatan struktural (ditegakkan parser, bukan cuma prompt): pada run PM trigger `mention`
(tiket chat), `tickets[]` **di-drop** selama `ticket.approved_at` kosong — PM hanya boleh
bertanya / menawarkan plan (`status: in_progress`, tanpa `tickets[]`), dan laporan itu
**tidak mem-block** tiket. Setelah owner menyetujui, run berikutnya boleh membawa `tickets[]`.
Tiket yang di-run manual dari board tidak terkena aturan ini.

**Final plan wajib 5 bagian.** Selama fase eksploratif (sebelum owner menyetujui), plan yang
ditawarkan PM di `summary` (masih prosa bebas, `tickets[]` belum berlaku) wajib berisi PERSIS
lima bagian, ditulis satu-satu supaya owner mudah membaca sebelum approve:

1. **Requirement** — ringkasan permintaan owner dengan bahasa PM sendiri, bukan copy-paste chat.
2. **Goal** — tujuan/hasil akhir yang ingin dicapai.
3. **Epic tujuan** — cek katalog epic yang sudah ada di kontrak ```map; sebutkan epic mana yang
   relevan (WAJIB reuse kalau ada) atau nyatakan "epic baru: `<nama>`" HANYA kalau ini benar-benar
   area fitur besar baru.
4. **Breakdown sprint** — jadi berapa sprint, dan goal singkat tiap sprint. **Sprint hanya
   timebox** — JANGAN taruh nama fitur/scope di sini, itu urusan epic di poin 3.
5. **Estimasi durasi** — total dan/atau per sprint, dihitung realistis untuk kecepatan kerja
   agent AI — jauh lebih cepat dari estimasi tim manusia, bukan hasil menyalin rule-of-thumb
   seperti "2 minggu per sprint".

Setelah disetujui, `tickets[]` boleh disertai blok top-level opsional `sprints:` (satu entri per
sprint: `name`, `goal`, `duration`) dan tiap item `tickets[]` boleh membawa `epic` (key epic
tujuan — lihat §3 "epic pada tickets[]"), `sprint` (nama sprint yang cocok dengan salah satu di
`sprints:`), dan `duration` (estimasi durasi tiket itu sendiri). Satuan `duration` mengikuti
pengaturan `time_unit` workspace (`hour`/`day`, dikunci ke dua pilihan itu, diatur owner/PM di
halaman Settings) — sprint/tiket yang namanya belum pernah muncul di-buat otomatis oleh
orchestrator (get-or-create by name, case-insensitive); sprint pertama yang pernah dibuat di
suatu workspace otomatis jadi `active` sebagai bootstrap, setelahnya perpindahan sprint aktif
adalah aksi manual (Board/Timeline). **Sprint sengaja dipisah tegas dari epic**: sprint murni
timebox (kapan dikerjakan), epic murni scope (fitur besar apa) — jangan campur keduanya lewat
nama sprint yang menyebut nama fitur.

**Review/rapikan sprint tiket yang sudah ada, lewat chat.** Run PM trigger `mention` (chat owner)
mendapat tambahan konteks di prompt: daftar tiket lain di workspace ini (key, status, prioritas,
sprint saat ini — hingga ~60 tiket paling baru diupdate), di luar tiket yang sedang di-chat.
Trigger lain (`manual`/`handoff`/`auto`) tidak mendapat daftar ini, untuk menjaga ukuran/biaya
prompt tetap kecil di luar percakapan. Kalau owner minta PM meninjau ulang sprint tiket-tiket
yang sudah ada, PM memakai `updates:` (bukan `tickets[]` — itu untuk tiket baru) dengan field
`sprint`/`duration` per tiket yang mau diubah; sama seperti `tickets[].sprint`, nama sprint yang
belum ada otomatis dibuat (get-or-create).

### Lead Engineer (`lead`) — satu per workspace

```
Kamu Lead Engineer. Tugasmu me-review, bukan mengimplementasikan. Jangan mengubah file.

Baca perubahan yang dibuat (`git diff`, lalu baca file terkait).
Cek: apakah acceptance criteria tiket terpenuhi? Ada bug nyata? Ada yang menduplikasi kode
yang sudah ada di repo?

LOLOS      → status: qa, mention QA, summary berisi apa yang kamu setujui.
TIDAK LOLOS → status: in_progress, mention engineer yang mengerjakan, summary berisi daftar
             konkret apa yang harus diperbaiki (file + baris).

Jangan meminta perbaikan gaya atau preferensi pribadi. Hanya yang benar-benar salah,
tidak lengkap, atau berbahaya.
```

### Engineer (`engineer`) — boleh banyak

```
Kamu Engineer. Implementasikan apa yang diminta tiket ini, tidak lebih.

1. Baca kode yang ada dulu. Kalau sudah ada helper/util/pattern yang menyelesaikan ini, pakai itu.
   Jangan menulis ulang yang sudah ada beberapa file di sebelah.
2. Tulis solusi terkecil yang benar-benar bekerja.
3. Jalankan test atau perintah yang membuktikan itu jalan.
4. status: review, mention Lead Engineer. summary berisi file yang kamu ubah dan bukti jalannya.

Jangan menambah abstraksi, config, atau fitur yang tidak diminta tiket.
Kalau tiket ambigu, jangan menebak: status: blocked, mention PM, tulis pertanyaanmu di summary.
```

### Designer (`designer`) — boleh banyak

```
Kamu Designer. Outputmu berupa file di dalam repo, bukan gambar.

Hasilkan salah satu, sesuai permintaan tiket:
- Spec markdown: layout, state, perilaku, aturan responsif tiap komponen.
- Design token (warna, spasi, tipografi) sebagai file config/CSS.
- Struktur komponen: nama, props, hierarki.

Ikuti pola dan token yang sudah ada di repo — baca dulu sebelum menetapkan yang baru.
Sebutkan aksesibilitas: kontras, label, urutan fokus, target sentuh.
Selesai → status: review, mention Lead Engineer.
```

### QA (`qa`) — boleh banyak

```
Kamu QA. Kamu memverifikasi, bukan memperbaiki. Kamu hanya boleh menambah/mengubah file test.

1. Baca acceptance criteria tiket.
2. Tulis test yang membuktikannya (di lokasi test yang sudah dipakai repo ini) dan jalankan.
3. Coba kasus tepi yang jelas: input kosong, nilai negatif, item duplikat, path aneh.
4. Tulis file evidence singkat (apa yang dijalankan, jumlah lolos/gagal, kasus tepi yang dicoba)
   dan deklarasikan lewat `artifacts:` (group mis. "Hasil Testing").

SEMUA LOLOS → status: security, mention Pentester, summary berisi hasil test.
ADA GAGAL   → status: in_progress, mention engineer yang mengerjakan, dan isi `tickets[]`
              dengan satu tiket bug per masalah (langkah reproduksi + hasil diharapkan vs nyata).

Jangan memperbaiki kode produksi sendiri.
```

### Pentester (`pentester`) — boleh banyak

```
Kamu Security Reviewer. Audit HANYA perubahan pada tiket ini, di dalam repo ini.
Kamu tidak boleh memindai, menguji, atau menyerang sistem apa pun di luar repo ini.
Jangan mengubah file.

Cari: input yang tidak divalidasi di batas kepercayaan, injeksi (SQL/command/path traversal),
secret yang ter-hardcode, authz yang hilang, error yang membocorkan informasi, dependency baru
yang mencurigakan.

Tiap temuan: severity (low/medium/high), file:baris, dampak konkret, perbaikan yang disarankan.

BERSIH (tak ada high/medium) → status: done, mention PM, summary berisi hasil audit.
ADA TEMUAN                    → status: in_progress, mention engineer, isi `tickets[]` satu per
                                temuan high/medium. Temuan low cukup di summary.
```

## 5. State machine & izin transisi

```
backlog → todo → in_progress → review → qa → security → done → release
```

**Update:** per permintaan owner, transisi antar status tidak lagi dibatasi per pasangan
from→to/role (tabel lama yang cuma mengizinkan mis. `review`→`qa` khusus Lead sudah dihapus —
lihat §3). Sekarang **role mana pun (dan owner) boleh memindahkan tiket dari status apa pun ke
status apa pun yang lain**, termasuk drag & drop kartu di Board. Satu-satunya batasan yang masih
berlaku:

| Aturan | Detail |
|---|---|
| Status tidak dikenal | ditolak (baik dari agent maupun PATCH manual) |
| Status sama (no-op) | ditolak — bukan transisi nyata |
| `done` → `release` | boleh siapa pun secara struktural, tapi **`release` tidak bisa dideklarasikan lewat blok ```map** (§3) — hanya aksi manual owner (atau PM lewat status yang boleh ia deklarasikan) di Board/Timeline |
| `blocked` → apa pun | boleh siapa pun (termasuk owner/agent), tapi lihat catatan di bawah soal kapan ini realistis terjadi otomatis |

`blocked` secara desain adalah titik di mana alur otonom biasanya berhenti dan meminta
perhatianmu — bukan karena state machine melarang siapa pun membukanya (sekarang semua role
boleh), tapi karena tidak ada bagian dari alur otomatis yang secara proaktif mengeluarkan tiket
dari `blocked`; itu tetap perilaku yang diharapkan dari owner/PM saat mereka memutuskan lanjut.

Alasan block dicatat di kolom `ticket.blocked_reason` setiap kali tiket masuk status `blocked`
(komponen sistem `_block_ticket` maupun deklarasi `status: blocked` dari agent) dan dibersihkan
saat tiket keluar dari `blocked` — detail tiket menampilkannya langsung, tanpa perlu menelusuri
komentar.

## 6. Aturan handoff

- Handoff dipicu oleh `mention` di blok ```map. Komentar manual owner yang berisi `@agent` juga
  memicu run ([02-tsd.md](02-tsd.md) §3).
- `mention` harus berisi **nama agent**, bukan role — daftar nama sudah ada di prompt. Kalau model
  tetap menulis role (`qa`), orchestrator memilih agent `idle` dengan run paling sedikit di tiket
  itu; kalau semua sibuk, masuk antrean.
- Nama tak dikenal → dicatat di komentar sistem, tidak memicu run. Kalau `status` bukan final dan
  tidak ada mention valid, tiket jadi `blocked` (tidak boleh menggantung).
- Agent tidak bisa mention dirinya sendiri (dibuang saat parsing).
- Tiap handoff menaikkan `ticket.handoff_depth`. Lewat `max_handoff_depth` → `blocked`. Guardrail
  ini khusus membatasi rantai agent-ke-agent (mis. Lead ↔ Engineer bolak-balik) — pesan chat
  owner ke agent (`trigger="mention"`, satu-satunya trigger yang manusia yang picu) **tidak**
  dihitung/dibatasi guardrail ini, karena `handoff_depth` tidak pernah turun: tiket epic yang
  sudah `done` lewat rantai handoff panjang tetap harus bisa terus di-chat oleh owner setelahnya.
- Mention ke agent `disabled` → `blocked` dengan komentar sistem "agent X nonaktif".

## 7. Anti-loop dalam prompt

Selain guardrail di kode ([02-tsd.md](02-tsd.md) §6), tiap prompt reviewer (Lead, QA, Pentester)
mendapat tambahan bila ini bukan review pertama untuk tiket tersebut:

```
Ini review ke-{n} untuk tiket ini. Review sebelumnya:
{ringkasan summary review terdahulu}

Kalau masalah yang sama masih ada setelah dua kali diminta perbaiki, JANGAN meminta lagi.
status: blocked, dan jelaskan kenapa perbaikannya tidak berhasil.
```

Kode adalah rem yang menentukan; prompt hanya mengurangi seberapa sering rem itu terpakai.

## 8. Alur otonom penuh — contoh

```
Owner buat MAP-001 "Bikin halaman login", assign ke PM, klik Run
  │
  ├─ PM (opencode) → tickets[]: MAP-002 (Designer), MAP-003 (Engineer), MAP-004 (Engineer)
  │                  status: in_progress → 3 run terjadwal
  │
  ├─ MAP-002 Designer → review → Lead qa → QA security → Pentester done
  ├─ MAP-003 Engineer → review → Lead REJECT ("validasi email hilang") → in_progress
  │      → Engineer (lanjut session opencode yang sama) → review → Lead qa
  │      → QA gagal → tickets[]: MAP-005 bug → in_progress → ... → done
  └─ MAP-004 ... → done

Semua anak done → PM tutup MAP-001
```

Owner memantau di `/w/[key]/activity` dan bisa menekan Pause kapan saja.
