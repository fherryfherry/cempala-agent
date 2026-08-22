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

## Setup dari nol

1. Install & autentikasi `opencode`: `opencode auth login` (lihat [Prasyarat](#prasyarat)).
2. Setup backend: `cd backend && uv venv --python 3.12 .venv && uv pip install -e ".[dev]"`
   (tanpa `uv`: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`).
3. Setup frontend: `cd frontend && npm install`.
4. `make migrate` — apply migrasi database.
5. `make dev` — jalankan backend (`:8000`) dan frontend (`:3000`) bareng, `Ctrl+C` mematikan keduanya.

## Menjalankan

```
make dev       # backend (uvicorn :8000) + frontend (next dev :3000)
make migrate   # alembic upgrade head
make test      # pytest
```

## Struktur

```
backend/    FastAPI + SQLite (via SQLAlchemy/Alembic)
frontend/   Next.js App Router
storage/    Attachment (di luar repo_path agent, bukan source code)
docs/       Spesifikasi — baca ini duluan
```
