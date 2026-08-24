"""add workspace timezone

Revision ID: 7556b3bd0291
Revises: 18ae78571463
Create Date: 2026-08-23 09:59:22.878877

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7556b3bd0291'
down_revision: Union[str, Sequence[str], None] = '18ae78571463'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workspace",
        sa.Column(
            "timezone", sa.String(), nullable=False, server_default="Asia/Jakarta"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workspace", "timezone")
