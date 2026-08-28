"""add agent fallback_tool_kind

Revision ID: da80dbbb910c
Revises: 831e55a8c6a0
Create Date: 2026-08-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'da80dbbb910c'
down_revision: Union[str, Sequence[str], None] = '831e55a8c6a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'agent',
        sa.Column(
            'fallback_tool_kind',
            sa.Enum('opencode', 'claude', 'agy', 'codex', name='agent_tool_kind'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('agent', 'fallback_tool_kind')
