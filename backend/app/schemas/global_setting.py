"""Global-setting request/response schemas (orchestrator default model)."""

from pydantic import BaseModel


class OrchestratorModel(BaseModel):
    model: str | None


class OrchestratorModelUpdate(BaseModel):
    model: str | None
