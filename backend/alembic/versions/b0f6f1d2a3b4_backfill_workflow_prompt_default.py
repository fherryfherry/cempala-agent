"""backfill workflow_prompt default for existing workspaces

Revision ID: b0b1f1d2a3b4
Revises: 3449d6c58e65
Create Date: 2026-08-22 23:30:00.000000

Workspaces created before the workflow_prompt column existed got an empty value
from the server_default. Backfill them with the default workflow prompt so
existing workspaces behave the same as newly created ones (workspaces the owner
already customized are left untouched — only empty ones are filled).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.schemas.workspace import DEFAULT_WORKFLOW_PROMPT


# revision identifiers, used by Alembic.
revision: str = 'b0f6f1d2a3b4'
down_revision: Union[str, Sequence[str], None] = '3449d6c58e65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE workspace SET workflow_prompt = :prompt WHERE workflow_prompt = ''"),
        {"prompt": DEFAULT_WORKFLOW_PROMPT},
    )


def downgrade() -> None:
    """Downgrade schema. Intentionally no-op: emptying backfilled values would
    destroy an owner's active workflow config; there's no safe inverse."""
    pass
