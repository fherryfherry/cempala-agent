# Multi-Agent Portal (MAP)

Portal Jira-like untuk menjalankan satu "tim software" yang seluruhnya diisi agent AI (PM, Lead
Engineer, Engineer, Designer, QA, Pentester). Kamu bikin tiket, tim agent mengerjakannya secara
otonom di dalam folder repo lokal, dan kamu memantau semuanya real-time lewat activity feed dan
streaming output.

Portal ini tidak membangun coding agent sendiri — ia merakit prompt, melempar ke coding tool
eksternal (`opencode`), dan menerima hasilnya lewat blok ` ```map ` di akhir jawaban agent.

Lihat [`docs/00-overview.md`](docs/00-overview.md) untuk pitch lengkap, dan
[`docs/02-tsd.md`](docs/02-tsd.md) untuk arsitektur teknis.

## ⚠️ Peringatan keamanan — baca sebelum menjalankan

- `opencode` dijalankan dengan flag **`--auto`**, yang berarti agent **menyetujui sendiri semua
  permission** — tidak ada manusia yang mengonfirmasi dialog izin apa pun.
- `--dir <repo_path>` hanya menetapkan **working directory**, **BUKAN sandbox**. Tidak ada yang
  mencegah agent menyentuh file di luar folder tersebut.
- Konsekuensinya: agent bisa menjalankan **perintah apa pun** dengan hak akses user yang
  menjalankan backend ini.
- Karena itu:
  - Backend **wajib** bind ke `127.0.0.1` saja. **Jangan pernah** mengekspos portal ini ke
    jaringan — itu sama dengan membuka remote code execution.
  - Jalankan hanya pada repo yang kamu percayai, di mesin yang kamu kendalikan.
  - **Jangan** menaruh secret produksi di dalam `repo_path`.
  - Validasi `repo_path` di API adalah kenyamanan, bukan batas keamanan.

Ini bukan detail implementasi yang bisa diabaikan — ini konsekuensi arsitektur yang diterima
sadar (lihat [ADR-010](docs/06-adr.md)).

## Prasyarat

- Binary [`opencode`](https://opencode.ai) terinstal dan terautentikasi:

  ```
  opencode auth login
  ```

  Backend shell out ke binary ini untuk setiap agent run dan untuk daftar model. Kredensial LLM
  tidak pernah disimpan oleh portal ini.

## Menjalankan

Repo ini masih pre-implementation (lihat [`CLAUDE.md`](CLAUDE.md)). Belum ada `make dev`,
`make migrate`, atau `make test` — target-target itu baru dibuat di MAP-005.

## Struktur

```
backend/    FastAPI + SQLite (via SQLAlchemy/Alembic)
frontend/   Next.js App Router
storage/    Attachment (di luar repo_path agent, bukan source code)
docs/       Spesifikasi — baca ini duluan
```
