"""move workspace and global settings to .cempala YAML files (ADR-015)

Revision ID: d97ad763fc97
Revises: c4e1a7b90d33
Create Date: 2026-08-28 00:00:00.000000

Per-workspace settings (guardrails, workflow_prompt, sprint_creator_roles, time_unit,
timezone, main_branch) move from columns on `workspace` to
`<repo_path>/.cempala/settings.yaml`; the single global setting (orchestrator_model)
moves from the `global_setting` key-value table to `~/.cempala/settings.yaml`. See
`app/core/settings_store.py` and docs/06-adr.md ADR-015.

Before dropping the columns/table, every existing workspace's current values are
written out to its `.cempala/settings.yaml` (best-effort — a workspace whose
repo_path is missing/unwritable is skipped with a warning, not a migration failure),
and the `global_setting` row for orchestrator_model (if any) is written to
`~/.cempala/settings.yaml`.
"""
import json
import sys
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import yaml

from app.core.settings_store import GlobalSettings, WorkspaceSettings, global_settings_path, workspace_settings_path

# revision identifiers, used by Alembic.
revision: str = 'd97ad763fc97'
down_revision: Union[str, Sequence[str], None] = 'c4e1a7b90d33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _write_yaml(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _json_field(value):
    """Raw `conn.execute(sa.text(...))` bypasses the ORM's JSON type decorator, so
    JSON columns come back as their raw TEXT encoding, not parsed Python objects."""
    return json.loads(value) if isinstance(value, str) else value


def upgrade() -> None:
    conn = op.get_bind()

    rows = conn.execute(
        sa.text(
            "SELECT id, repo_path, guardrails, workflow_prompt, sprint_creator_roles, "
            "time_unit, timezone, main_branch FROM workspace"
        )
    ).mappings().all()
    for row in rows:
        try:
            settings = WorkspaceSettings(
                guardrails=_json_field(row["guardrails"]) or {},
                workflow_prompt=row["workflow_prompt"] or "",
                sprint_creator_roles=_json_field(row["sprint_creator_roles"]) or ["pm"],
                time_unit=row["time_unit"] or "day",
                timezone=row["timezone"] or "Asia/Jakarta",
                main_branch=row["main_branch"] or "main",
            )
            _write_yaml(workspace_settings_path(row["repo_path"]), settings.model_dump())
        except Exception as exc:  # best-effort: one bad repo_path must not abort the migration
            print(
                f"[migration d97ad763fc97] skipping workspace {row['id']} ({row['repo_path']!r}): {exc}",
                file=sys.stderr,
            )

    try:
        gs_row = conn.execute(
            sa.text("SELECT value FROM global_setting WHERE name = 'orchestrator_model'")
        ).mappings().first()
        model = _json_field(gs_row["value"]) if gs_row is not None else None
        _write_yaml(
            global_settings_path(),
            GlobalSettings(orchestrator_model=model if isinstance(model, str) else None).model_dump(),
        )
    except Exception as exc:
        print(f"[migration d97ad763fc97] skipping global settings: {exc}", file=sys.stderr)

    op.drop_column('workspace', 'guardrails')
    op.drop_column('workspace', 'workflow_prompt')
    op.drop_column('workspace', 'time_unit')
    op.drop_column('workspace', 'sprint_creator_roles')
    op.drop_column('workspace', 'timezone')
    op.drop_column('workspace', 'main_branch')
    op.drop_table('global_setting')


def downgrade() -> None:
    """Re-adds the columns/table with their original defaults. Does NOT restore
    values from the .cempala YAML files — there's no safe generic inverse (a workspace's
    repo_path may have moved, been deleted, or hold owner-edited settings since the
    upgrade ran)."""
    op.add_column('workspace', sa.Column('guardrails', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('workspace', sa.Column('workflow_prompt', sa.Text(), nullable=False, server_default=''))
    op.add_column(
        'workspace',
        sa.Column('time_unit', sa.Enum('hour', 'day', name='time_unit'), nullable=False, server_default='day'),
    )
    op.add_column('workspace', sa.Column('sprint_creator_roles', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column(
        'workspace',
        sa.Column('timezone', sa.String(), nullable=False, server_default='Asia/Jakarta'),
    )
    op.add_column('workspace', sa.Column('main_branch', sa.String(), nullable=False, server_default='main'))
    op.create_table(
        'global_setting',
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('value', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )
