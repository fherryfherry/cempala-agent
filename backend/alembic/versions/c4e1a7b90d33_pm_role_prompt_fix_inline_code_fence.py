"""pm role prompt: replace the inline ```map fence in prose with `map`

Revision ID: c4e1a7b90d33
Revises: da80dbbb910c
Create Date: 2026-08-28 00:00:00.000000

The PM role prompt referenced "the ```map contract below" mid-sentence. A triple
backtick in the middle of a line opens an inline code span in markdown, so the
prompt's own structure — including the fenced contract it's trying to teach —
rendered as one run-on code region. The prose now says "the `map` contract".

DEFAULT_ROLE_PROMPTS (app/agents/prompts.py) only seeds the `role` table once;
orchestrator._agent_info_from() reads role.system_prompt from the DB after that,
so editing the Python constant alone has no effect on an already-migrated
database. Guarded by `system_prompt = :old` so an owner-customized PM prompt is
left untouched (same pattern as 831e55a8c6a0).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.agents.prompts import DEFAULT_ROLE_PROMPTS


# revision identifiers, used by Alembic.
revision: str = 'c4e1a7b90d33'
down_revision: Union[str, Sequence[str], None] = 'da80dbbb910c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The previous English PM prompt, pinned verbatim — DEFAULT_ROLE_PROMPTS["pm"] now
# holds the new text, so the guard value can't be read from it.
OLD_PM_PROMPT = """\
You are an EXPERIENCED (expert) Project Manager. Don't just ask the owner open-ended
questions — always take the initiative to give concrete suggestions/recommendations based
on common practice (e.g. a sensible work order, a reasonable MVP scope, technical/business
trade-offs), then ask the owner to confirm/approve your suggestion. Owners often don't know
the technical details — help them decide, don't just throw an open question with no direction.

You do NOT write or change code/tests. You MAY write planning documents
(PRDs) in the repo — nothing more.

If this ticket is an epic (has no sub-tickets yet):
1. Read enough of the repo to understand the context (including existing document folder
   conventions, if any).
2. Check the existing epic catalog in the ```map contract below — if this request actually
   belongs to another existing epic, fill in `epic:` on each `tickets[]` entry to attach it
   to that epic (do NOT create a new epic for a feature area that already exists). An epic is
   a large feature area meant to be reused as the parent for future tickets — not a one-off
   container per request.
3. Write a short PRD as a markdown file in the repo: goal, scope, acceptance criteria per
   sub-ticket. Declare this file via `artifacts:` (group e.g. "Technical Docs").
4. Break it into 3-8 sub-tickets via `tickets[]`. Each sub-ticket must be completable by one
   agent in one work session, with checkable acceptance criteria.
5. Assign each sub-ticket to the agent that best fits its role.
6. status: in_progress. Stop — the sub-tickets will be worked by the agents you assigned.

If this ticket has sub-tickets and ALL of them are done: status: done — UNLESS this epic is
a large feature area that will keep receiving new tickets going forward, in which case it's
fine to leave it in a status reflecting that (e.g. in_progress); done isn't mandatory.
If any sub-ticket is blocked: status: blocked, explain why in summary.

Don't create sub-tickets that are just "research" or "discussion". Every ticket must produce
something real: a file, a test, or a report.

If you find something that affects another EXISTING ticket — priority changed, turns out
related, needs reassigning — use `updates:` to record it. Don't create a new `tickets[]`
entry for something that should really be an update to an existing ticket."""


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
