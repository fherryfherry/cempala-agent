# Roadmap — MAP MVP

Versi 0.2 · MVP
Tiket: [04-tasks.md](04-tasks.md) · ~26 hari kerja satu orang.

## Peta

```
M0 Skeleton      MAP-001…005   ~3 hari   ── jalan, tapi belum ada isi
M1 Ticketing     MAP-006…016   ~9 hari   ── Jira-like lengkap, tanpa agent
M2 Agent Runtime MAP-017…026   ~8 hari   ── opencode bekerja & terpantau, trigger manual
M3 Otonomi       MAP-027…033   ~6 hari   ── otonom penuh  ← MVP RILIS
```

Tiap milestone bisa didemokan sendiri. Kalau M3 tidak pernah selesai, yang sudah ada tetap
berguna: portal tiket dengan agent opencode yang dijalankan manual per tiket.

---

## M0 — Skeleton (~3 hari)

**Tujuan.** Dua proses menyala, DB punya skema, satu perintah menjalankan semuanya.

MAP-001 repo · MAP-002 FastAPI · MAP-003 skema DB · MAP-004 Next.js · MAP-005 dev runner

**Definition of done**
- `make dev` menyalakan backend :8000 (bind `127.0.0.1`) dan frontend :3000.
- Halaman root menampilkan status backend **dan** versi opencode yang terdeteksi.
- `alembic upgrade head` membuat seluruh tabel; `downgrade base` bersih.
- README: setup dari nol ≤5 langkah, termasuk `opencode auth login`, dan memuat peringatan
  keamanan `--auto`.

**Jalur kritis.** MAP-003 — skema yang salah menular ke semua milestone. Kunci dulu bentuk
`run` (terutama `session_id` dan `report`) dan `event`.

---

## M1 — Ticketing (~9 hari)

**Tujuan.** Portal Jira-like yang bisa dipakai manusia, sebelum ada satu pun agent yang jalan.

Backend: MAP-006 workspace · MAP-007 models · MAP-008 agent · MAP-009 ticket ·
MAP-010 comment · MAP-011 attachment · MAP-012 state machine
Frontend: MAP-013 workspace+agent · MAP-014 board · MAP-015 detail tiket
Test: MAP-016

**Definition of done** (semua diuji manual tanpa menyentuh terminal)
1. Buat workspace ke folder repo nyata; `repo_path` ngawur ditolak dengan pesan jelas.
2. Tambah 6 agent dengan role berbeda; dropdown model terisi dari `opencode models`.
3. Buat epic + 3 sub-tiket, assign, lampirkan file, drag antar kolom.
4. Komentar `@nama-agent`; autocomplete jalan; mention tersimpan (belum memicu apa pun).
5. Drag ke kolom yang transisinya ilegal ditolak dengan toast.
6. `make test` hijau.

**Cek awal yang murah.** Sebelum menulis MAP-007, jalankan `opencode models` sendiri di terminal
dan pastikan provider yang kamu mau (mis. `ollama`) benar-benar muncul. Kalau tidak, urus
`opencode auth` dulu — itu prasyarat di luar kode kita.

---

## M2 — Agent Runtime (~8 hari)

**Tujuan.** Satu agent opencode benar-benar mengerjakan satu tiket di repo nyata, hasilnya masuk
kembali ke sistem tiket, dan kamu melihatnya live. Trigger masih manual.

Fondasi: MAP-017 EventBus · MAP-022 SSE · MAP-024 SSE frontend
Kontrak: MAP-018 parser ```map · MAP-019 perakit prompt
Eksekusi: MAP-020 adapter opencode · MAP-021 stub adapter · MAP-023 API run + orchestrator ·
MAP-026 recovery
UI: MAP-025 feed + panel run

**Urutan.** MAP-018 (parser) sebelum MAP-019 (prompt) sebelum MAP-020 (adapter). Kontrak balik
adalah bagian tersulit dan paling menentukan; membangun adapter dulu berarti menemukan bentuk
kontrak yang salah setelah semuanya terpasang.

**Definition of done**
1. Klik Run pada tiket yang di-assign ke Engineer → proses opencode berjalan di `repo_path`,
   mengubah file, dan menutup dengan blok ```map.
2. Blok itu ter-parse: komentar `summary` muncul di tiket, status berpindah ke `review`.
3. Activity feed menampilkan output & tool call opencode <1 detik setelah terjadi.
4. Refresh halaman: riwayat feed utuh (dibaca dari DB).
5. Stop mematikan proses opencode (dicek `ps`); agent kembali `idle`.
6. Agent yang lupa menulis blok ```map → tiket `blocked` + komentar sistem berisi potongan
   output aslinya. Tidak diam-diam sukses.
7. Agent bertool `claude` → run `failed` dengan pesan "adapter belum tersedia", bukan 500.
8. Kill backend di tengah run, nyalakan lagi → nol run `running`.

**Risiko utama: kepatuhan format.** Ini pengganti risiko tool-calling di v0.1, dan bentuknya
mirip — model kecil sering mengarang format atau lupa menutup blok. Uji minimal dua model
berbeda (satu besar, satu kecil) sebelum menyatakan M2 selesai. Kalau model kecil tidak
sanggup, itu batasan yang harus tertulis di halaman setup agent (MAP-013), bukan bug.
Kalau bahkan model besar sering gagal, itu sinyal untuk pindah ke MCP server berisi tool
ticketing ([ADR-009](06-adr.md)) — keputusan itu diambil di sini, bukan setelah M3.

---

## M3 — Otonomi (~6 hari) — **MVP RILIS**

**Tujuan.** Satu klik pada epic → tim agent menyelesaikannya sendiri, dan kamu bisa menghentikannya.

Rem dulu: MAP-027 guardrail · MAP-028 loop detector · MAP-031 kill switch · MAP-032 UI settings
Baru gas: MAP-029 handoff engine · MAP-030 alur otonom
Penutup: MAP-033 dogfood

**Urutan tidak bisa ditawar.** Guardrail, loop detector, dan kill switch **selesai dan teruji**
sebelum MAP-029/030 dinyalakan. Menyalakan otonomi tanpa rem berarti proses opencode beranak-pinak
sambil membakar biaya — dan tiap run di sini adalah proses penuh, bukan satu panggilan HTTP.
Itu sebabnya `max_concurrent_runs` default 3.

**Definition of done**
1. Satu epic, satu klik Run → PM memecah jadi sub-tiket lewat `tickets[]`, agent mengerjakan,
   Lead review, QA test, Pentester audit, PM menutup. Tanpa intervensi.
2. Tidak ada tiket menggantung: setiap tiket berakhir `done` atau `blocked` dengan alasan tertulis.
3. Setiap guardrail yang kena meninggalkan komentar sistem yang menyebut guardrail mana.
4. Loop ping-pong Lead↔Engineer berhenti di `loop_threshold` dengan tiket `blocked`.
5. Pause di tengah kesibukan → nol proses opencode dalam ≤5 detik, agent `idle`, banner tampil.
6. Engineer yang kembali ke tiket yang sama melanjutkan session opencode sebelumnya.
7. `docs/07-dogfood-report.md` terisi, memuat tingkat kepatuhan blok ```map.

---

## Setelah MVP (tidak dijadwalkan)

Urutan berdasarkan nilai per usaha, bukan komitmen:

1. **Operasi git** — branch per tiket, commit, diff di UI. Ini yang membuat review Lead jadi nyata,
   dan yang membuat kerja beberapa Engineer paralel di satu repo tidak saling menimpa.
   Kandidat kuat untuk naik ke MVP kalau dogfood M3 menunjukkan agent saling menabrak file.
2. **MCP server berisi tool ticketing** — menggantikan blok ```map. Agent bisa bikin tiket dan
   ubah status di tengah kerja, bukan cuma di akhir, dan tidak ada lagi risiko format.
   Naikkan prioritas kalau kepatuhan format di M2/M3 buruk ([ADR-009](06-adr.md)).
3. **Adapter claude / agy / codex** — setelah pola opencode terbukti; `AgentTool` sudah
   menyiapkan tempatnya.
4. **Sandbox (Docker)** — kalau portal dipakai pada repo yang tidak sepenuhnya dipercaya.
5. **Sub-tiket lebih dari 1 level.**
6. **Auth & multi-user** — saat portal keluar dari laptop. Sebelum itu, jangan expose ke jaringan.
7. **Memori agent lintas tiket** — retrieval dari tiket lama; berguna, tapi mudah jadi sumber
   halusinasi kalau dipasang terlalu dini.
