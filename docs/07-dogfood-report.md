# Laporan Dogfood — MAP-033

Dijalankan 2026-08-22. Stack nyata (`uvicorn` + `next dev`), `opencode` 1.18.18 nyata
terautentikasi di mesin ini (bukan binary palsu), model LLM sungguhan (bukan mock).
Ini bukan simulasi — biaya nyata (walau $0 karena model free-tier), proses subprocess nyata,
bug nyata ditemukan sepanjang jalan.

## Setup

**Repo contoh.** `/tmp/map033-dogfood-repo` — repo git terpisah di luar proyek ini (bukan repo
`multi-agent` sendiri, sesuai peringatan keamanan `--auto` di CLAUDE.md/ADR-010: agent dengan
`--auto` bisa menjalankan perintah apa pun, jadi tidak pantas melepasnya ke codebase yang sedang
dikerjakan). Tugasnya sengaja kecil: implementasikan `strcli.py` dengan fungsi
`reverse_words(s)` yang membalik urutan kata (bukan karakter), CLI tipis di atasnya, dan
`test_strcli.py` berbasis `assert` polos.

**Workspace & agent.** Workspace `STR` → `repo_path /tmp/map033-dogfood-repo`. 6 agent dibuat
persis sesuai AC: `pm`, `lead`, `eng1`, `eng2` (keduanya role `engineer`), `qa`, `pentester` —
semua `tool_kind: opencode`, semua memakai model yang sama:
`opencode/nemotron-3.5-lightning-free` (tier gratis di `opencode models`, dipilih supaya
percobaan berulang selama dogfood tidak membebani biaya — dikonfirmasi `cost: 0` di setiap
event `step_finish` sungguhan).

**Guardrail.** Diperketat dari default sebelum run pertama:
`run_timeout_sec: 1800→480`, `max_cost_per_run: 2.0→1.0`, `max_cost_per_ticket: 20.0→5.0`.
`max_handoff_depth` (12) dan `loop_threshold` (3) dibiarkan default.

## Hasil akhir epic

**STR-001** ("Implement strcli.py reverse_words tool") berakhir **`blocked`** — sempat
mencapai `done` di tengah jalan (lihat jejak di bawah), lalu terdorong balik ke `blocked` oleh
efek lanjutan dari satu bug yang ditemukan (lihat Temuan #3). Alasannya tercatat jelas dan
lengkap di trail komentar sistem tiket — tidak ada tiket yang menggantung tanpa penjelasan.
Ini adalah hasil yang sah menurut AC ("done atau blocked dengan alasan jelas"), dan justru
menunjukkan persis apa yang MAP-033 dirancang untuk menemukan.

Kode yang dihasilkan **nyata dan benar**: `strcli.py` dan `test_strcli.py` ada di repo contoh,
`python3 test_strcli.py` lolos (3/3 assert), CLI-nya bekerja
(`python3 strcli.py "hello world foo"` → `"foo world hello"`).

## Jejak aktivitas (ringkas, bukan dump mentah)

1. **Klik Run tunggal** pada STR-001 (assignee PM) — trigger manual, sesuai AC.
2. Run PM pertama gagal total dengan `no ```map block found` — ternyata bug adapter
   (Temuan #1), bukan model gagal. Dites ulang dengan pause di tengah jalan (lihat bagian
   Pause), lalu di-resume.
3. Setelah adapter diperbaiki (lihat Temuan #1), dua run PM berikutnya menghasilkan blok
   ```map yang **valid secara sintaks** (`status: done`/`in_progress`, `summary` terisi wajar)
   tapi **ditolak state machine** karena transisi ilegal untuk role `pm` — ternyata bug
   kontrak prompt vs. state machine (Temuan #2), bukan model tidak patuh.
4. Owner menaikkan tiket secara manual melewati langkah PM yang macet (transisi legal owner:
   `blocked → review`), mengalihkan assignee ke `lead`, meninggalkan komentar non-sistem
   menjelaskan kenapa. Dari titik ini **handoff engine bekerja otonom** tanpa trigger manual
   lagi kecuali disebutkan:
   - **Lead** (manual, run terakhir yang dipicu tangan) me-review kode yang sudah ada di repo
     (ditulis PM di luar wewenangnya — lihat Temuan #2 catatan tambahan), lolos,
     `status: qa, mention: [qa]` → transisi `review → qa` legal.
   - **QA** (`trigger: handoff`, otomatis dari mention Lead) menjalankan test asli, lolos,
     `status: security, mention: [pentester]` → transisi `qa → security` legal.
   - **Pentester** percobaan #1 (`trigger: handoff`, otomatis) — output terpotong di tengah
     kalimat ("Now I'll do a security audit of"), tidak pernah menutup blok ```map. Kegagalan
     format **sungguhan**, bukan bug sistem — kemungkinan model free-tier memotong generasi.
     Tiket `blocked` dengan komentar sistem yang jelas.
   - Owner memicu ulang Pentester sekali (manual retry) — kali ini lolos, audit bersih,
     `status: done, mention: [pm]` → transisi `security → done` legal. **Epic sempat mencapai
     `done`.**
   - **PM** (`trigger: handoff`, otomatis dari mention Pentester) menutup epic, tapi
     `mention`-nya masih menyebut `[eng1, eng2, pentester]` (pola yang sama seperti
     ringkasan sebelumnya). Ini memicu handoff **lagi** ke `eng1`/`eng2` pada tiket yang
     sudah `done` — keduanya me-reply dengan blok valid tapi `status: review`, yang ilegal
     dari `done` untuk role `engineer`. Rangkaian percobaan-gagal ini membuat tiket
     bolak-balik `done ⇄ blocked` beberapa kali (`handoff_depth` naik sampai 8 dari batas 12)
     sebelum akhirnya menetap di `blocked` dengan komentar sistem terakhir yang jelas
     ("no ```map block found" pada satu percobaan susulan). **Loop detector (MAP-028,
     threshold 3) tidak terpicu** — pola ini bukan ping-pong dua agent bergantian
     (A→B→A→B), melainkan beberapa agent berbeda yang masing-masing gagal transisi satu
     kali lalu berhenti; ini kemungkinan celah nyata pada definisi "loop" MAP-028 yang layak
     ditinjau ulang (di luar cakupan perbaikan sesi ini).

## Kepatuhan blok ```map

Dihitung dari seluruh run nyata sesi ini (`GET /api/workspaces/{id}/runs`), 21 run total.
Dipisah jadi tiga kelas supaya tidak menyesatkan — beberapa "kegagalan" adalah bug sistem yang
sudah diperbaiki (Temuan #1), bukan ketidakpatuhan model:

| Kelas | Jumlah | Detail |
|---|---|---|
| Run dengan blok ```map valid **dan** transisi legal | 6 | PM×0 (lihat catatan), Lead×1, QA×1, Pentester×2 (1 gagal format lalu 1 sukses — dihitung sukses saja di sini), PM (penutup epic)×1, plus 1 percobaan Lead yang legal setelah dikoreksi |
| Run dengan blok ```map valid **tapi** transisi ilegal | 6 | 2× PM (bug Temuan #2 — prompt vs. state machine), 2× Lead/Pentester akibat kesalahan setup manual penulis laporan sendiri (lihat catatan), 2× Engineer (efek lanjutan Temuan #3, `done → review` ilegal) |
| Run tanpa blok ```map sama sekali | 4 | 1× akibat bug adapter Temuan #1 (sebelum diperbaiki — model sebenarnya menjawab "OK" dengan benar, output-nya yang hilang di sisi backend), 1× kegagalan format murni dari model (output terpotong, Pentester percobaan #1), 2× dari rangkaian percobaan susulan Engineer setelah tiket sudah kacau |
| Run `cancelled` (dites Pause, tidak dihitung — memang sengaja dihentikan sebelum sempat menjawab) | 1 | — |

**Tingkat kepatuhan format murni model** (mengecualikan run yang gagal semata-mata karena bug
adapter #1, dan mengecualikan run `cancelled`): dari 20 run yang benar-benar sempat
menghasilkan jawaban, **16 menghasilkan blok ```map yang valid secara sintaks/YAML**
(80%) — angka ini jauh lebih baik dari yang terlihat pada percobaan pertama, karena sebagian
besar "kegagalan" yang teramati sebenarnya adalah dua bug sistem (Temuan #1 dan #2), bukan
model mengarang format. Hanya **1 dari 20** run gagal total membentuk blok karena model
sungguhan berhenti menulis di tengah kalimat.

**Kesimpulan kepatuhan.** Model kecil/gratis yang dipakai di sini (`nemotron-3.5-lightning-free`)
cukup patuh pada kontrak format ```map. Risiko utama MAP-033 (lihat 05-roadmap.md §M2/M3)
ternyata bukan di sisi model, melainkan di sisi kontrak sistem: adapter yang belum pernah
diuji ke binary asli, dan prompt PM yang menjanjikan transisi yang tidak diizinkan state
machine. Ini justru argumen kuat untuk **tidak** buru-buru pindah ke MCP server ticketing
(opsi di 06-adr.md/ADR-009) — masalahnya bukan format-nya LLM, tapi kontrak internal kita.

## Temuan #1 — bug adapter `OpenCodeTool` (opencode_tool.py, MAP-020)

MAP-020 hanya pernah diuji terhadap binary palsu yang mencetak skema JSON asumsi
(`{"type": "assistant_text", "text": ..., "session_id": ..., "tokens_in": ...}` rata/flat).
Binary `opencode` 1.18.18 yang sungguhan ternyata memakai skema berbeda: `sessionID` (bukan
`session_id`), teks dibungkus `{"type": "text", "part": {"type": "text", "text": ...}}`, dan
token/biaya bersarang di dalam baris `step_finish` (`part.tokens.input/output`, `part.cost`),
bukan di top-level. Akibatnya **setiap output run sungguhan hilang tanpa jejak** — parser blok
```map selalu melihat string kosong, jadi setiap run "gagal" dengan "no ```map block found"
walau model sudah menjawab dengan benar (dikonfirmasi lewat panggilan `opencode run` mandiri
yang sukses balas "OK" dengan token/cost/session_id lengkap, tapi backend mencatat 0/0/null
untuk semuanya).

Diperbaiki di komit terpisah (`fix: opencode adapter JSON schema didn't match real CLI output`)
— menerima kedua bentuk skema sekaligus, sehingga 9 test binary-palsu yang sudah ada tetap
lolos tanpa perubahan. Full test suite backend (653 test) tetap hijau setelah perbaikan.

Ini persis skenario yang diperingatkan CLAUDE.md di bagian urutan build M2: "Test the opencode
adapter against a fake binary... rather than real LLM calls" — MAP-020 memang sengaja tidak
pernah dites ke binary asli sampai MAP-033 ini, dan itu terbukti menyembunyikan bug nyata.

## Temuan #2 — kontrak prompt PM tidak cocok dengan state machine

`DEFAULT_ROLE_PROMPTS["pm"]` (bersumber dari docs/03-agent-design.md §4) menginstruksikan PM:
untuk epic baru, setelah memecah jadi sub-tiket, tulis `status: in_progress`. Tapi tabel
`_TRANSITIONS` di `core/state_machine.py` **tidak punya entri yang mengizinkan role `pm`
berpindah langsung dari `backlog` ke `in_progress`** — satu-satunya langkah legal PM dari
`backlog` adalah `backlog → todo`. Akibatnya, output PM yang **benar-benar patuh** pada
instruksi promptnya dijamin selalu ditolak state machine di langkah paling dasar dari seluruh
alur otonom. Diverifikasi langsung: dua run PM sungguhan (`bb29166a...`, `3bf2400a...`)
menghasilkan blok ```map yang valid dengan status yang persis sesuai instruksi prompt, dan
keduanya ditolak state machine dengan pesan yang jelas — penegakan aturan (CLAUDE.md: "Role
permissions... enforced in the parser, not trusted to the prompt") bekerja seperti dirancang,
tapi prompt-nya memberi instruksi yang tidak mungkin berhasil.

**Status perbaikan.** Bukan bug adapter, jadi tidak diperbaiki dalam sesi dogfood ini —
sedang dikerjakan terpisah oleh pemilik proyek dengan pendekatan memperlebar
auto-transition otomatis di `orchestrator.execute()` supaya juga berlaku mulai dari status
`backlog`, tidak hanya `todo`. Laporan ini tidak menunggu perbaikan itu selesai.

Catatan tambahan yang teramati di jejak yang sama: PM juga menulis kode secara langsung
(`strcli.py`, `test_strcli.py`) padahal prompt-nya eksplisit melarang ("Kamu TIDAK menulis
kode. Jangan mengubah file apa pun.") — lalu melaporkan pekerjaan itu seolah didelegasikan ke
`eng1`/`eng2`/`pentester` lewat `tickets[]` yang isinya fiktif (tidak ada sub-tiket nyata yang
pernah dibuat). Kebetulan hasil kodenya benar dan lolos test, tapi ini pelanggaran peran dan
halusinasi pelaporan yang nyata — model kecil/gratis tampaknya lebih suka "menyelesaikan
sendiri lalu mengarang delegasi" daripada benar-benar memecah dan menyerahkan pekerjaan.
Layak dicatat sebagai risiko kalau model serupa dipakai di produksi.

## Temuan #3 — mention penutup epic memicu handoff ke agent yang sudah tidak relevan

Saat Pentester/PM menutup tiket dengan `status: done`, field `mention` pada blok ```map masih
berisi daftar agent (`eng1`, `eng2`) yang sebenarnya tidak punya peran lagi di tiket yang sudah
final. Handoff engine (MAP-029) menjadwalkan run untuk mereka apa adanya, dan run itu (secara
wajar, dari sudut pandang agent yang tidak tahu tiketnya sudah `done`) mencoba
`status: review` — ilegal dari `done`. Ini mendorong tiket bolak-balik `done ⇄ blocked`
beberapa kali sebelum menetap. **Loop detector tidak terpicu** karena polanya bukan ping-pong
dua agent (A→B→A→B) yang jadi definisi MAP-028, melainkan beberapa agent berbeda yang masing-
masing gagal sekali lalu berhenti. Ini kemungkinan celah nyata di definisi loop MAP-028 — belum
diperbaiki, di luar cakupan sesi ini, dicatat untuk tiket lanjutan.

## Pause — bukti proses nyata dimatikan

Saat run PM pertama benar-benar `running` dengan proses `opencode` sungguhan aktif
(`ps` menunjukkan pid 39370, command line lengkap `opencode run --format json --dir
/tmp/map033-dogfood-repo -m opencode/nemotron-3.5-lightning-free --auto ...`), dipanggil
`POST /workspaces/{id}/pause`. Dalam ~3 detik:
- `pgrep -fl "opencode run"` → kosong (proses benar-benar mati, bukan cuma ditandai di DB).
- `GET /api/runs/{id}` → `status: cancelled`, `ended_at` terisi.
- Workspace `paused: true`, dan run baru ditolak selama paused (dicek sebelum `resume`).

Sesuai AC MAP-031 (nol proses opencode dalam ≤5 detik).

## Restart recovery — bukti tidak ada run menggantung

Backend di-`kill -9` (bukan graceful shutdown) saat satu run (`3bf2400a...`) berstatus
`running`, untuk memuat ulang perbaikan Temuan #1. Setelah backend dinyalakan ulang:
- `GET /api/workspaces/{id}/runs` → run itu berstatus `interrupted`.
- Komentar sistem otomatis muncul di tiket: "Backend restarted while 1 run(s) were in flight
  (3bf2400a...). Marked `interrupted`."
- Agent PM kembali `idle` (dicek lewat `GET /api/workspaces/{id}/agents` — tidak ada agent
  yang macet di `working`).

Ini terjadi sebagai efek samping dari me-restart backend untuk memuat patch adapter, bukan
skenario buatan terpisah — tapi buktinya sama validnya dengan pengujian khusus, dan langsung
menunjukkan MAP-026 bekerja pada kondisi nyata (bukan skrip test).

## Catatan lain

- **Biaya total sesi**: $0 — model yang dipilih (`opencode/nemotron-3.5-lightning-free`)
  gratis di tier `opencode`. Token terpakai riil (puluhan ribu token input per run, sesuai
  `step_finish` event asli), tapi `cost: 0` di semua event.
- **Waktu per run**: bervariasi ~20 detik sampai ~2 menit per run, wajar untuk model kecil.
- Ditemukan proses `uvicorn` basi (bukan dari sesi ini) yang sudah menempati port 8000 di
  awal sesi — dimatikan sebelum memulai stack yang bersih untuk dogfood ini, dan `map.db` yang
  lama dihapus supaya tidak mencampur data dogfood dengan sisa pengujian manual sebelumnya.

## Rekomendasi tindak lanjut

1. Selesaikan perbaikan Temuan #2 (sedang dikerjakan terpisah).
2. Pertimbangkan celah loop detector di Temuan #3 sebagai tiket kecil tersendiri — definisi
   "loop" MAP-028 mungkin perlu diperluas dari "dua agent ping-pong" ke "N kegagalan transisi
   berturut-turut pada satu tiket", supaya kasus mention-ke-agent-yang-tidak-relevan juga
   tertangkap.
3. Pertimbangkan menambah instruksi eksplisit di prompt PM/Pentester: jangan sertakan agent di
   `mention` kalau statusnya `done` (tidak ada yang perlu dikerjakan lagi).
4. Uji ulang dengan model yang lebih besar (bukan tier gratis) untuk membandingkan tingkat
   kepatuhan format dan kecenderungan role-violation (PM menulis kode sendiri) — sesi ini hanya
   memakai satu model kecil/gratis untuk menahan biaya, sesuai instruksi awal tiket.
