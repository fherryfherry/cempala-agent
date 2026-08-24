"""add sprint start/end date

Revision ID: f2c8a4d6b9e1
Revises: e7b3c5d9f1a2
Create Date: 2026-08-23 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2c8a4d6b9e1'
down_revision: Union[str, Sequence[str], None] = 'e7b3c5d9f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("sprint", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("sprint", sa.Column("end_date", sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sprint", "end_date")
    op.drop_column("sprint", "start_date")
