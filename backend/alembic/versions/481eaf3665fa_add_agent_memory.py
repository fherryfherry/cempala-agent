"""add agent_memory

Revision ID: 481eaf3665fa
Revises: a712e487fbab
Create Date: 2026-08-23 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '481eaf3665fa'
down_revision: Union[str, Sequence[str], None] = 'a712e487fbab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'agent_memory',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('origin', sa.Enum('agent', 'owner', name='agent_memory_origin'), nullable=False),
        sa.Column('source_ticket_key', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agent.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('agent_memory')
