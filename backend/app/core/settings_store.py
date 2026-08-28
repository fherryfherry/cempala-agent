"""Per-workspace and global settings, stored as YAML files under a `.cempala` folder
(ADR-015) instead of the database.

Workspace settings live at `<repo_path>/.cempala/settings.yaml` — deliberately keyed
off the workspace's *current* `repo_path`, not any database identity, so the file is
portable: commit it to the project's own repo and every clone/install that points a
workspace at that repo picks up the same settings. Global (portal-wide) settings live
at `~/.cempala/settings.yaml`.

These files are owner-authored config, not agent output — unlike `core/report.py`'s
tolerant parsing of the agent-written ```map block, a malformed settings file raises
`SettingsLoadError` instead of silently falling back to defaults (CLAUDE.md: no silent
failure path). The one deliberate exception is `orchestrator._global_orchestrator_model`,
which has always swallowed all errors and returned None by contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from app.schemas.workspace import DEFAULT_GUARDRAILS, DEFAULT_WORKFLOW_PROMPT

_SETTINGS_DIRNAME = ".cempala"
_SETTINGS_FILENAME = "settings.yaml"


class SettingsLoadError(Exception):
    """Raised when a `.cempala/settings.yaml` file exists but can't be parsed into a
    valid settings object — malformed YAML, non-mapping content, or a bad field type."""


class WorkspaceSettings(BaseModel):
    guardrails: dict = Field(default_factory=lambda: dict(DEFAULT_GUARDRAILS))
    workflow_prompt: str = DEFAULT_WORKFLOW_PROMPT
    sprint_creator_roles: list[str] = Field(default_factory=lambda: ["pm"])
    time_unit: Literal["hour", "day"] = "day"
    timezone: str = "Asia/Jakarta"
    main_branch: str = "main"

    model_config = {"extra": "ignore"}


class GlobalSettings(BaseModel):
    orchestrator_model: str | None = None

    model_config = {"extra": "ignore"}


def workspace_settings_path(repo_path: str) -> Path:
    repo_root = Path(repo_path).resolve()
    path = repo_root / _SETTINGS_DIRNAME / _SETTINGS_FILENAME
    if not path.parent.resolve().is_relative_to(repo_root):
        raise ValueError(f"resolved settings path escapes repo_path: {repo_path}")
    return path


def global_settings_path() -> Path:
    return Path.home() / _SETTINGS_DIRNAME / _SETTINGS_FILENAME


def _load_yaml(path: Path, model: type[BaseModel]):
    if not path.exists():
        return model()
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise SettingsLoadError(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SettingsLoadError(f"{path} must contain a YAML mapping, got {type(raw).__name__}")
    try:
        return model(**raw)
    except ValidationError as exc:
        raise SettingsLoadError(f"invalid settings in {path}: {exc}") from exc


def _atomic_write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def load_workspace_settings(repo_path: str) -> WorkspaceSettings:
    return _load_yaml(workspace_settings_path(repo_path), WorkspaceSettings)


def load_global_settings() -> GlobalSettings:
    return _load_yaml(global_settings_path(), GlobalSettings)


async def save_workspace_settings(repo_path: str, settings: WorkspaceSettings) -> None:
    await asyncio.to_thread(_atomic_write_yaml, workspace_settings_path(repo_path), settings.model_dump())


async def save_global_settings(settings: GlobalSettings) -> None:
    await asyncio.to_thread(_atomic_write_yaml, global_settings_path(), settings.model_dump())


# In-process locks guarding a load-modify-save sequence against a concurrent request
# doing the same — os.replace() alone makes each individual write atomic, but without
# this a lost-update is still possible: two PATCHes could both read the pre-patch
# state and each save a different single-field change, with the second silently
# clobbering the first's. Keyed by resolved path; this backend is single-process (no
# multi-worker/Redis anywhere in config.py), so no cross-process lock is needed. Not
# held around plain reads, which never observe a torn file either way.
_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(path: Path) -> asyncio.Lock:
    key = str(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def workspace_settings_lock(repo_path: str) -> asyncio.Lock:
    return _lock_for(workspace_settings_path(repo_path))


def global_settings_lock() -> asyncio.Lock:
    return _lock_for(global_settings_path())
