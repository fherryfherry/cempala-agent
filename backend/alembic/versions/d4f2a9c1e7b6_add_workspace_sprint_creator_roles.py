"""add workspace sprint_creator_roles

Revision ID: d4f2a9c1e7b6
Revises: c09208ff9333
Create Date: 2026-08-23 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f2a9c1e7b6'
down_revision: Union[str, Sequence[str], None] = '481eaf3665fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('workspace', sa.Column('sprint_creator_roles', sa.JSON(), nullable=False, server_default=sa.text("'[\"pm\"]'")))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('workspace', 'sprint_creator_roles')
