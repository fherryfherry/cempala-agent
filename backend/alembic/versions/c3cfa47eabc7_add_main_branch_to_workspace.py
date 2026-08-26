"""add main_branch to workspace

Revision ID: c3cfa47eabc7
Revises: d8e1f4a7c2b3
Create Date: 2026-08-26 12:23:22.086944

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3cfa47eabc7'
down_revision: Union[str, Sequence[str], None] = 'd8e1f4a7c2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("workspace") as batch:
        batch.add_column(sa.Column("main_branch", sa.String(), nullable=False, server_default="main"))


def downgrade() -> None:
    with op.batch_alter_table("workspace") as batch:
        batch.drop_column("main_branch")
