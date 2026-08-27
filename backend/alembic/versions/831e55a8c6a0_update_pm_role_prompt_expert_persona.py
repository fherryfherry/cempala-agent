"""update pm role default system_prompt with expert/proactive persona

Revision ID: 831e55a8c6a0
Revises: f9a2b4c6d8e0
Create Date: 2026-08-27 18:00:00.000000

DEFAULT_ROLE_PROMPTS["pm"] (app/agents/prompts.py) only seeds the `role` table
once, at migration f9a2b4c6d8e0 — orchestrator._agent_info_from() then always
reads agent.system_prompt or role.system_prompt from the DB, never the Python
constant again. Editing the constant alone has zero effect on any already-
migrated database, so this backfills the new persona text into the `pm` row —
but only if it still holds the exact old default (an owner who customized the
PM's system_prompt is left untouched, same guard as b0f6f1d2a3b4).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.agents.prompts import DEFAULT_ROLE_PROMPTS


# revision identifiers, used by Alembic.
revision: str = '831e55a8c6a0'
down_revision: Union[str, Sequence[str], None] = 'f9a2b4c6d8e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_PM_PROMPT = """\
Kamu Project Manager. Kamu TIDAK menulis atau mengubah kode/test. Kamu BOLEH menulis dokumen
perencanaan (PRD) di repo — tidak lebih.

Kalau tiket ini epic (belum punya sub-tiket):
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

Kalau nemuin sesuatu yang mempengaruhi tiket LAIN yang sudah ada — prioritas berubah, ternyata
saling terkait, perlu di-reassign — pakai `updates:` buat mencatatnya. Jangan bikin `tickets[]`
baru untuk hal yang seharusnya jadi update ke tiket yang sudah ada."""


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE role SET system_prompt = :new WHERE key = 'pm' AND system_prompt = :old"),
        {"new": DEFAULT_ROLE_PROMPTS["pm"], "old": OLD_PM_PROMPT},
    )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE role SET system_prompt = :old WHERE key = 'pm' AND system_prompt = :new"),
        {"new": DEFAULT_ROLE_PROMPTS["pm"], "old": OLD_PM_PROMPT},
    )
