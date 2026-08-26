"""GET/PUT /api/settings/orchestrator-model — the portal-wide AI default model.

Global (workspace-agnostic). Stored in the `global_setting` key-value table under
name="orchestrator_model". This is a convenience/model-picker only — LLM
credentials stay in `opencode auth`; we never store secrets here.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GlobalSetting
from app.db.session import get_session
from app.schemas.global_setting import OrchestratorModel, OrchestratorModelUpdate

router = APIRouter(prefix="/settings", tags=["settings"])

_ORCHESTRATOR_KEY = "orchestrator_model"


@router.get("/orchestrator-model", response_model=OrchestratorModel)
async def get_orchestrator_model(session: AsyncSession = Depends(get_session)):
    row = await session.get(GlobalSetting, _ORCHESTRATOR_KEY)
    value = row.value if row is not None else None
    return OrchestratorModel(model=value if isinstance(value, str) else None)


@router.put("/orchestrator-model", response_model=OrchestratorModel)
async def set_orchestrator_model(
    body: OrchestratorModelUpdate,
    session: AsyncSession = Depends(get_session),
):
    if body.model is None or body.model.strip() == "":
        row = await session.get(GlobalSetting, _ORCHESTRATOR_KEY)
        if row is not None:
            await session.delete(row)
        await session.commit()
        return OrchestratorModel(model=None)

    model = body.model.strip()
    row = await session.get(GlobalSetting, _ORCHESTRATOR_KEY)
    if row is None:
        row = GlobalSetting(name=_ORCHESTRATOR_KEY, value=model)
        session.add(row)
    else:
        row.value = model
    await session.commit()
    return OrchestratorModel(model=model)
