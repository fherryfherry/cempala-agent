"""add ticket_auto_check

Revision ID: d8e1f4a7c2b3
Revises: c3d9f1a2b4e5
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e1f4a7c2b3'
down_revision: Union[str, Sequence[str], None] = 'c3d9f1a2b4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ticket_auto_check',
        sa.Column('ticket_id', sa.String(), nullable=False),
        sa.Column('skip_count', sa.Integer(), nullable=False),
        sa.Column('last_nudge_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['ticket_id'], ['ticket.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('ticket_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('ticket_auto_check')
