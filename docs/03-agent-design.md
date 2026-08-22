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
| PM | `in_progress`, `done`, `blocked` | **ya** | tidak |
| Lead Engineer | `qa`, `in_progress`, `blocked` | tidak | tidak |
| Engineer | `review`, `blocked` | tidak | ya |
| Designer | `review`, `blocked` | tidak | ya |
| QA | `security`, `in_progress`, `blocked` | **ya** (bug) | hanya file test |
| Pentester | `done`, `in_progress`, `blocked` | **ya** (temuan) | tidak |

Kolom "menyentuh kode" adalah instruksi di prompt, bukan penegakan teknis — opencode berjalan
dengan `--auto` dan bisa menulis apa saja ([02-tsd.md](02-tsd.md) §7). Dua kolom pertama
ditegakkan parser.

## 4. Role

### PM (`pm`) — satu per workspace

```
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
```

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
backlog → todo → in_progress → review → qa → security → done
```

| Dari → Ke | Siapa yang boleh |
|---|---|
| `backlog` → `todo` | PM, owner |
| `todo` → `in_progress` | otomatis saat run dimulai, owner |
| `in_progress` → `review` | Engineer, Designer |
| `review` → `qa` | Lead |
| `review` → `in_progress` | Lead (reject) |
| `qa` → `security` | QA (lolos) |
| `qa` → `in_progress` | QA (gagal) |
| `security` → `done` | Pentester (bersih) |
| `security` → `in_progress` | Pentester (ada temuan) |
| epic → `done` | PM (semua anak `done`) |
| any → `blocked` | agent mana pun, orchestrator (guardrail, blok map rusak) |
| `blocked` → `todo` | **hanya owner** |

`blocked` sengaja hanya bisa dibuka manusia. Itu satu-satunya titik di mana alur otonom berhenti
dan meminta perhatianmu.

## 6. Aturan handoff

- Handoff dipicu oleh `mention` di blok ```map. Komentar manual owner yang berisi `@agent` juga
  memicu run ([02-tsd.md](02-tsd.md) §3).
- `mention` harus berisi **nama agent**, bukan role — daftar nama sudah ada di prompt. Kalau model
  tetap menulis role (`qa`), orchestrator memilih agent `idle` dengan run paling sedikit di tiket
  itu; kalau semua sibuk, masuk antrean.
- Nama tak dikenal → dicatat di komentar sistem, tidak memicu run. Kalau `status` bukan final dan
  tidak ada mention valid, tiket jadi `blocked` (tidak boleh menggantung).
- Agent tidak bisa mention dirinya sendiri (dibuang saat parsing).
- Tiap handoff menaikkan `ticket.handoff_depth`. Lewat `max_handoff_depth` → `blocked`.
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
