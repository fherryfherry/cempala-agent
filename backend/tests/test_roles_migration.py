"""Migration test (docs/superpowers/specs/2026-08-27-dynamic-roles-design.md):
running `alembic upgrade head` on a fresh DB creates the `role` table with the 8
backfilled builtin roles, and `agent.role` becomes a plain string column while
existing agent values are preserved."""

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

_PREV_HEAD = "e759eb8714dc"


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "migrated.db"

    import app.config as app_config

    app_config.settings.DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")

    # Upgrade to the revision right before this feature's migration, insert an
    # agent holding a builtin role value, then upgrade to head.
    command.upgrade(cfg, _PREV_HEAD)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO agent (id, workspace_id, name, role, model, tool_kind, enabled, status, created_at) "
        "VALUES ('a1', 'w1', 'Alice', 'engineer', 'opencode/pm', 'opencode', 1, 'idle', "
        "'2026-08-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")
    yield db_path


def test_upgrade_creates_and_backfills_roles(migrated_db):
    conn = sqlite3.connect(migrated_db)
    rows = conn.execute(
        "SELECT key, name, is_builtin, may_declare_tickets, may_manage_artifacts, is_reviewer "
        "FROM role ORDER BY key"
    ).fetchall()
    assert len(rows) == 8
    by_key = {r[0]: r for r in rows}
    assert set(by_key) == {
        "pm", "lead", "engineer", "designer", "qa", "pentester",
        "business_analyst", "system_architect",
    }
    assert all(r[2] == 1 for r in rows)  # all builtin
    assert by_key["pm"][3] == 1 and by_key["pm"][4] == 1 and by_key["pm"][5] == 0
    assert by_key["lead"][3] == 0 and by_key["lead"][4] == 0 and by_key["lead"][5] == 1
    # prompts backfilled for all 8
    prompts = conn.execute(
        "SELECT key, system_prompt FROM role WHERE system_prompt IS NOT NULL"
    ).fetchall()
    assert len(prompts) == 8
    conn.close()


def test_upgrade_keeps_existing_agent_role_values(migrated_db):
    conn = sqlite3.connect(migrated_db)
    row = conn.execute("SELECT role FROM agent WHERE name='Alice'").fetchone()
    assert row == ("engineer",)
    conn.close()
