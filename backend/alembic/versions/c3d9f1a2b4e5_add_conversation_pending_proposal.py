"""add conversation pending_proposal

Revision ID: c3d9f1a2b4e5
Revises: a1b2c3d4e5f6
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d9f1a2b4e5'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('conversation') as batch_op:
        batch_op.add_column(sa.Column('pending_proposal', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('conversation') as batch_op:
        batch_op.drop_column('pending_proposal')
