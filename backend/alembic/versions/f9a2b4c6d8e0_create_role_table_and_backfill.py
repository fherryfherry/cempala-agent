"""create role table, backfill builtin roles, agent.role enum -> string

Revision ID: f9a2b4c6d8e0
Revises: e759eb8714dc
Create Date: 2026-08-27 09:00:00.000000

docs/superpowers/specs/2026-08-27-dynamic-roles-design.md:

1. Create the global `role` table.
2. Backfill the 8 builtin roles from the current constants.
3. Alter `agent.role` from the SQLAlchemy Enum to a plain String — existing
   values are already exactly the builtin keys, so no data rewrite is needed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.agents.prompts import DEFAULT_ROLE_PROMPTS
from app.core.role_defs import BUILTIN_ROLES

# revision identifiers, used by Alembic.
revision: str = 'f9a2b4c6d8e0'
down_revision: Union[str, Sequence[str], None] = 'e759eb8714dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "role",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("may_declare_tickets", sa.Boolean(), nullable=False),
        sa.Column("may_manage_artifacts", sa.Boolean(), nullable=False),
        sa.Column("is_reviewer", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    conn = op.get_bind()
    for role in BUILTIN_ROLES:
        conn.execute(
            sa.text(
                "INSERT INTO role (id, key, name, description, system_prompt, is_builtin, "
                "may_declare_tickets, may_manage_artifacts, is_reviewer, created_at) "
                "VALUES (:id, :key, :name, NULL, :system_prompt, 1, :may_declare_tickets, "
                ":may_manage_artifacts, :is_reviewer, CURRENT_TIMESTAMP)"
            ),
            {
                "id": f"builtin-{role['key']}",
                "key": role["key"],
                "name": role["name"],
                "system_prompt": DEFAULT_ROLE_PROMPTS.get(role["key"]),
                "may_declare_tickets": int(role["may_declare_tickets"]),
                "may_manage_artifacts": int(role["may_manage_artifacts"]),
                "is_reviewer": int(role["is_reviewer"]),
            },
        )

    with op.batch_alter_table("agent") as batch:
        batch.alter_column(
            "role",
            existing_type=sa.Enum(
                "pm", "lead", "engineer", "designer", "qa", "pentester",
                "business_analyst", "system_architect", name="agent_role",
            ),
            type_=sa.String(),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agent") as batch:
        batch.alter_column(
            "role",
            existing_type=sa.String(),
            type_=sa.Enum(
                "pm", "lead", "engineer", "designer", "qa", "pentester",
                "business_analyst", "system_architect", name="agent_role",
            ),
            existing_nullable=False,
        )
    op.drop_table("role")
