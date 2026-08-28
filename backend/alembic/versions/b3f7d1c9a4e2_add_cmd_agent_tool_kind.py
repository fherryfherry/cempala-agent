"""add cmd agent tool kind

Revision ID: b3f7d1c9a4e2
Revises: d97ad763fc97
Create Date: 2026-08-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f7d1c9a4e2'
down_revision: Union[str, Sequence[str], None] = 'd97ad763fc97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('agent') as batch_op:
        batch_op.alter_column(
            'tool_kind',
            type_=sa.Enum('opencode', 'claude', 'agy', 'codex', 'cmd', name='agent_tool_kind'),
            existing_type=sa.Enum('opencode', 'claude', 'agy', 'codex', name='agent_tool_kind'),
            existing_nullable=False,
        )
        batch_op.alter_column(
            'fallback_tool_kind',
            type_=sa.Enum('opencode', 'claude', 'agy', 'codex', 'cmd', name='agent_tool_kind'),
            existing_type=sa.Enum('opencode', 'claude', 'agy', 'codex', name='agent_tool_kind'),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('agent') as batch_op:
        batch_op.alter_column(
            'fallback_tool_kind',
            type_=sa.Enum('opencode', 'claude', 'agy', 'codex', name='agent_tool_kind'),
            existing_type=sa.Enum('opencode', 'claude', 'agy', 'codex', 'cmd', name='agent_tool_kind'),
            existing_nullable=True,
        )
        batch_op.alter_column(
            'tool_kind',
            type_=sa.Enum('opencode', 'claude', 'agy', 'codex', name='agent_tool_kind'),
            existing_type=sa.Enum('opencode', 'claude', 'agy', 'codex', 'cmd', name='agent_tool_kind'),
            existing_nullable=False,
        )
