"""add global_setting table and make agent.model nullable

Revision ID: e759eb8714dc
Revises: c3cfa47eabc7
Create Date: 2026-08-26 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e759eb8714dc'
down_revision: Union[str, Sequence[str], None] = 'c3cfa47eabc7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "global_setting",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.PrimaryKeyConstraint("name"),
    )
    with op.batch_alter_table("agent") as batch:
        batch.alter_column("model", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agent") as batch:
        batch.alter_column("model", existing_type=sa.String(), nullable=False)
    op.drop_table("global_setting")
