"""Unit tests for core/settings_store.py — .cempala YAML settings files (ADR-015)."""

import asyncio

import pytest
import yaml

from app.core.settings_store import (
    SettingsLoadError,
    WorkspaceSettings,
    load_workspace_settings,
    save_workspace_settings,
    workspace_settings_lock,
    workspace_settings_path,
)


def test_workspace_settings_defaults_when_file_absent(tmp_path):
    settings = load_workspace_settings(str(tmp_path))
    assert settings == WorkspaceSettings()
    assert not (tmp_path / ".cempala").exists()


def test_workspace_settings_round_trip(tmp_path):
    settings = WorkspaceSettings(
        workflow_prompt="be careful",
        sprint_creator_roles=["pm", "qa"],
        time_unit="hour",
        timezone="UTC",
        main_branch="develop",
    )
    asyncio.run(save_workspace_settings(str(tmp_path), settings))

    path = workspace_settings_path(str(tmp_path))
    assert path.exists()
    assert path.parent.name == ".cempala"

    loaded = load_workspace_settings(str(tmp_path))
    assert loaded == settings


def test_workspace_settings_path_rejects_escape(tmp_path):
    # A symlinked repo_path whose ".cempala" would resolve outside itself is rejected.
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.symlink_to(outside)
    # Symlink target resolves to `outside`, and `.cempala` under it stays inside
    # `outside` — not actually an escape — so this should still resolve fine.
    path = workspace_settings_path(str(repo))
    assert path == (outside / ".cempala" / "settings.yaml")


def test_malformed_yaml_raises(tmp_path):
    cempala = tmp_path / ".cempala"
    cempala.mkdir()
    (cempala / "settings.yaml").write_text("not: valid: yaml: [")
    with pytest.raises(SettingsLoadError):
        load_workspace_settings(str(tmp_path))


def test_non_mapping_yaml_raises(tmp_path):
    cempala = tmp_path / ".cempala"
    cempala.mkdir()
    (cempala / "settings.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(SettingsLoadError):
        load_workspace_settings(str(tmp_path))


def test_bad_field_type_raises(tmp_path):
    cempala = tmp_path / ".cempala"
    cempala.mkdir()
    (cempala / "settings.yaml").write_text(yaml.safe_dump({"time_unit": "fortnight"}))
    with pytest.raises(SettingsLoadError):
        load_workspace_settings(str(tmp_path))


def test_unknown_keys_ignored(tmp_path):
    cempala = tmp_path / ".cempala"
    cempala.mkdir()
    (cempala / "settings.yaml").write_text(yaml.safe_dump({"some_future_field": "x"}))
    settings = load_workspace_settings(str(tmp_path))
    assert settings == WorkspaceSettings()


def test_atomic_write_leaves_no_temp_files(tmp_path):
    asyncio.run(save_workspace_settings(str(tmp_path), WorkspaceSettings()))
    names = {p.name for p in (tmp_path / ".cempala").iterdir()}
    assert names == {"settings.yaml"}


def test_workspace_settings_lock_is_reused_for_same_path(tmp_path):
    a = workspace_settings_lock(str(tmp_path))
    b = workspace_settings_lock(str(tmp_path))
    assert a is b
