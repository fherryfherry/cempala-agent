"""GET/PUT /api/settings/orchestrator-model — the portal-wide AI default model.

Global (workspace-agnostic). Stored in `~/.cempala/settings.yaml` (ADR-015). This is
a convenience/model-picker only — LLM credentials stay in `opencode auth`; we never
store secrets here.
"""

from fastapi import APIRouter

from app.api.errors import AppError
from app.core.settings_store import (
    GlobalSettings,
    SettingsLoadError,
    global_settings_lock,
    load_global_settings,
    save_global_settings,
)
from app.schemas.global_setting import OrchestratorModel, OrchestratorModelUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


def _load_or_500() -> GlobalSettings:
    try:
        return load_global_settings()
    except SettingsLoadError as exc:
        raise AppError(500, "invalid_global_settings", str(exc))


@router.get("/orchestrator-model", response_model=OrchestratorModel)
async def get_orchestrator_model():
    return OrchestratorModel(model=_load_or_500().orchestrator_model)


@router.put("/orchestrator-model", response_model=OrchestratorModel)
async def set_orchestrator_model(body: OrchestratorModelUpdate):
    model = body.model.strip() if body.model else None
    async with global_settings_lock():
        await save_global_settings(GlobalSettings(orchestrator_model=model or None))
    return OrchestratorModel(model=model or None)
