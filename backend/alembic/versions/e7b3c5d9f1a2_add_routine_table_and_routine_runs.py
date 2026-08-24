"""add routine table and routine runs

Revision ID: e7b3c5d9f1a2
Revises: d4f2a9c1e7b6
Create Date: 2026-08-23 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7b3c5d9f1a2'
down_revision: Union[str, Sequence[str], None] = 'd4f2a9c1e7b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'routine',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('workspace_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('interval_minutes', sa.Integer(), nullable=False),
        sa.Column('mode', sa.Enum('idle_only', 'consistent', name='routine_mode'), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=True),
        sa.Column('status', sa.Enum('idle', 'waiting', 'running', 'disabled', name='routine_status'), nullable=False),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agent.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # SQLite batch mode for the run table changes (ALTER COLUMN not supported).
    with op.batch_alter_table('run') as batch_op:
        batch_op.alter_column('ticket_id', existing_type=sa.String(), nullable=True)
        batch_op.add_column(sa.Column('routine_id', sa.String(), nullable=True))
        batch_op.create_foreign_key('fk_run_routine_id', 'routine', ['routine_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('run') as batch_op:
        batch_op.drop_constraint('fk_run_routine_id', type_='foreignkey')
        batch_op.drop_column('routine_id')
        batch_op.alter_column('ticket_id', existing_type=sa.String(), nullable=False)
    op.drop_table('routine')
