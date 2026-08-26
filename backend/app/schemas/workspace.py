import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

from app.schemas.agent import Role

KEY_RE = re.compile(r"^[A-Z]{2,5}$")

DEFAULT_GUARDRAILS = {
    "run_timeout_sec": 1800,
    "max_cost_per_run": 2.0,
    "max_cost_per_ticket": 20.0,
    "max_handoff_depth": 1000,
    "loop_threshold": 3,
    "max_concurrent_runs": 3,
    "max_auto_retries": 3,
    # Auto-check (MAP-050): how often the built-in scheduler scans for stale
    # tickets, and how stale (minutes) a ticket must be before the assigned
    # agent gets nudged to follow up. 0 disables the auto-check entirely.
    "auto_check_interval_minutes": 3,
    "auto_check_stale_minutes": 3,
}

# Default workflow prompt for new workspaces — adapted to this system's actual
# machinery: statuses from the ticket state machine, handoff via `mention` in the
# ```map block, role gates (only PM/QA/Pentester may create tickets[]), and the
# owner-approval gate before a PM executes (docs/03-agent-design.md §4).
DEFAULT_WORKFLOW_PROMPT = """\
## Alur Kerja Multi-Agent

Kamu bagian dari tim pengembangan software yang bekerja di repo ini. Tim terdiri dari:
- PM (Project Manager): pemilik requirements, acceptance criteria, tidak menulis kode.
- Designer: membuat spesifikasi desain, design token, dan struktur komponen di repo.
- Engineer: mengimplementasikan fitur + automated tests.
- Lead (Technical Lead): mereview desain teknis & kode, gate teknis terakhir.
- QA (Quality Assurance): menulis & menjalankan test case fungsional (hanya file test).
- Pentester (Security Audit): menulis & menjalankan security test case, tidak menyentuh kode produksi.

Ikuti alur ini kecuali task yang benar-benar trivial.

## 1. Requirements
Permintaan user -> PM menggali requirements secara eksploratif: konteks, lingkup, business rules,
edge cases, acceptance criteria, dan asumsi. Kalau butuh klarifikasi, tanya dulu — jangan menebak.
PM menuliskan ringkasan requirements. PM TIDAK boleh langsung membuat tickets[] sebelum owner
menyetujui plan (di chat: balasan user "oke lanjut" atau sejenisnya). PM juga boleh chat user
duluan kapan pun ada hal yang butuh klarifikasi.

## 2. Planning & Design
- Setelah plan disetujui, PM memecah pekerjaan jadi sub-tiket (tickets[]) dan meng-assign agent
  yang paling cocok.
- Lead mereview requirements + kode existing; menentukan desain teknis (arsitektur, DB, API,
  performa, keamanan); menuliskan TSD/desain teknis di repo.
- Designer menghasilkan spesifikasi desain sesuai permintaan tiket.

## 3. Test Design
- QA menyiapkan test case dari requirements + desain SEBELUM implementasi selesai.
- Pentester menyiapkan security test case dari requirements + desain.

## 4. Implementation
- Engineer mengimplementasikan sesuai requirements + desain.
- Ikuti arsitektur yang sudah ada; jangan refactor yang tidak perlu.
- Tambah/update automated tests.
- Tangani validasi, authorization, error handling, dan security requirements.
- Jalankan test/perintah untuk membuktikan pekerjaan jalan.
- Selesai -> status review, mention Lead.

## 5. Verification
- QA menjalankan test fungsional, integrasi, regression, dan acceptance.
- Pentester menjalankan security test.
- Gagal -> QA/Pentester kembali ke Engineer untuk perbaikan, lalu retest. Ulangi sampai lulus.

## 6. Review Teknis
- QA/Pentester lulus -> status ke Lead untuk final technical review.
- Ada isu teknis -> Lead kembali ke Engineer.
- Disetujui -> Lead lanjutkan ke PM.

## 7. Product Acceptance
- PM verifikasi hasil terhadap requirements & acceptance criteria.
- Ditolak -> kembali ke Lead/Engineer untuk perbaikan, QA/Pentester retest, Lead review, PM
  acceptance lagi.
- Diterima -> PM menutup tiket (status done).

## 8. Protokol Status (wajib sesuai izin role)
Pakai salah satu dari: backlog, todo, in_progress, review, qa, security, done, blocked.
- in_progress: sedang dikerjakan / menunggu persetujuan.
- review: menunggu review Lead.
- qa: menunggu verifikasi QA.
- security: menunggu audit keamanan.
- done: seluruh gate selesai.
- blocked: tidak bisa lanjut tanpa info/putusan — jelaskan alasannya di summary.
Jangan pernah menandai done tanpa benar-benar melakukan verifikasi yang dibutuhkan; sertakan bukti
di summary (test lulus, hasil, file yang disentuh).

## 9. Handoff Protocol
Setiap penyerahan pekerjaan dilakukan lewat mention: [nama agent] di blok ```map. Dalam summary,
tuliskan: apa yang dikerjakan, file yang tersentuh, bukti bahwa itu jalan, dan apa yang diminta
dari penerima. Jangan mengulang pekerjaan yang sudah diselesaikan agent lain. Use existing
artifacts (file di repo) sebagai source of truth.

## 10. Ownership
- PM: requirements & acceptance criteria (final product acceptance gate).
- Lead: desain teknis, arsitektur, keputusan teknis (final technical gate).
- Engineer: implementasi + automated tests.
- Designer: spesifikasi desain.
- QA: test case fungsional + hasil QA.
- Pentester: security test case + temuan keamanan (issue kepemilikan sampai tuntas/diterima).

## Alur Ringkas
User -> PM -> plan -> PM -> tickets[] -> Engineer/Designer -> review (Lead) -> qa (QA) ->
security (Pentester) -> done (PM acceptance)."""


class WorkspaceCreate(BaseModel):
    name: str
    key: str
    repo_path: str
    description: str | None = None

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        if not KEY_RE.match(v):
            raise ValueError("key must be 2-5 uppercase letters (A-Z)")
        return v


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    repo_path: str | None = None
    description: str | None = None
    guardrails: dict | None = None
    workflow_prompt: str | None = None
    time_unit: Literal["hour", "day"] | None = None
    timezone: str | None = None
    sprint_creator_roles: list[Role] | None = None
    main_branch: str | None = None


class WorkspaceOut(BaseModel):
    id: str
    name: str
    key: str
    repo_path: str
    description: str | None = None
    paused: bool
    guardrails: dict
    workflow_prompt: str = ""
    ticket_counter: int
    time_unit: Literal["hour", "day"] = "day"
    timezone: str = "Asia/Jakarta"
    sprint_creator_roles: list[str] = ["pm"]
    main_branch: str = "main"
    created_at: datetime

    model_config = {"from_attributes": True}
