# CEMPALA — Overview

Portal Jira-like untuk menjalankan satu "tim software" yang seluruhnya diisi agent AI.
Kamu bikin tiket, tim agent (PM, Lead Engineer, Engineer, Designer, QA, Pentester) mengerjakannya
secara otonom di dalam folder repo lokal, dan kamu memantau semuanya real-time.

## Kenapa ada

Menjalankan banyak agent AI hari ini = banyak terminal, tanpa memori bersama, tanpa jejak
siapa mengerjakan apa. MAP memberi mereka satu papan kerja bersama: tiket, komentar, mention,
status, dan satu working directory yang sama.

## Prinsip

1. **Kita tidak membangun coding agent.** Portal merakit prompt, melempar ke coding tool
   eksternal (opencode), dan menerima hasilnya. Yang kita bangun: tiket, orkestrasi, guardrail,
   visibilitas.
2. **Satu workspace = satu folder repo lokal.** Agent bekerja di file nyata, bukan simulasi.
3. **Semua yang terjadi jadi event.** Tabel `event` adalah satu-satunya sumber untuk feed,
   replay, dan debugging.
4. **Otonom, tapi ada rem.** Agent boleh assign & handoff sendiri; guardrail (timeout, budget
   biaya, loop detector, kill switch) tidak opsional.
5. **MVP dulu.** Rilis cepat, lalu perluas.

## Bentuk MVP

- Multi workspace, tanpa login (single user lokal).
- Setup agent manual: pilih role, model, dan coding tool per agent.
- Coding tool yang jalan di MVP: **`opencode`** saja. `claude` / `agy` / `codex` bisa dipilih
  di config tapi belum dieksekusi.
- Model diambil dari `opencode models` (`provider/model`). Portal tidak menyimpan kredensial LLM
  sama sekali — itu urusan `opencode auth`. Model Ollama Cloud muncul setelah provider `ollama`
  dikonfigurasi di opencode.
- Agent melapor balik lewat blok ```map di akhir jawabannya: status tujuan, siapa yang di-mention,
  ringkasan, dan sub-tiket yang perlu dibuat.
- Ticketing: nomor tiket, judul, deskripsi, attachment, komentar, mention, assignee.
- Real-time: activity feed + streaming output per agent via SSE.

> **Peringatan keamanan.** opencode dijalankan dengan `--auto`, artinya agent menyetujui sendiri
> semua permission dan bisa menjalankan perintah apa pun dengan hak akses user-mu. `--dir` adalah
> working directory, **bukan** sandbox. Jalankan hanya pada repo yang kamu percayai, di mesinmu
> sendiri, dan jangan pernah expose backend ke jaringan. Detail: [02-tsd.md](02-tsd.md) §7.

## Glossary

| Istilah | Arti |
|---|---|
| **Workspace** | Projek. Punya `repo_path` = folder lokal tempat agent bekerja. |
| **Agent** | Satu pekerja AI. Punya role, model, coding tool, system prompt. Milik satu workspace. |
| **Role** | PM, Lead Engineer, Engineer, Designer, QA, Pentester. |
| **Ticket** | Unit kerja. Punya key seperti `MAP-001`, status, assignee, parent (untuk sub-tiket). |
| **Run** | Satu proses opencode: agent X mengerjakan tiket Y. Punya status, session, biaya. |
| **Event** | Satu kejadian di dalam run: output agent, tool call, perubahan status, komentar. |
| **Coding tool** | Binary eksternal yang mengeksekusi kerja. MVP: `opencode`. |
| **Blok ```map** | Kontrak balik: YAML di akhir jawaban agent berisi `status`, `mention`, `summary`, `tickets[]`. |
| **Handoff** | Agent memindahkan tiket ke role lain lewat `status` + `mention` di blok ```map. |

## Index dokumen

| Dokumen | Isi |
|---|---|
| [01-prd.md](01-prd.md) | Product requirements, user story, scope MVP |
| [02-tsd.md](02-tsd.md) | Arsitektur, data model, API, agent runtime, guardrail |
| [03-agent-design.md](03-agent-design.md) | Peran & prompt tiap agent, state machine tiket, aturan handoff |
| [04-tasks.md](04-tasks.md) | Breakdown tiket `MAP-001`…`MAP-033` |
| [05-roadmap.md](05-roadmap.md) | Milestone M0–M3 + definition of done |
| [06-adr.md](06-adr.md) | Architecture decision records |

## Status

Fase perencanaan. Belum ada kode. Mulai dari [05-roadmap.md](05-roadmap.md) → M0.
