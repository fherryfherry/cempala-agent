from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RoutineCreate(BaseModel):
    name: str
    prompt: str
    interval_minutes: int = Field(ge=1)
    mode: Literal["idle_only", "consistent"] = "idle_only"
    agent_id: str | None = None


class RoutineUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    interval_minutes: int | None = Field(default=None, ge=1)
    mode: Literal["idle_only", "consistent"] | None = None
    agent_id: str | None = None
    status: Literal["idle", "waiting", "running", "disabled"] | None = None


class RoutineOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    prompt: str
    interval_minutes: int
    mode: str
    agent_id: str | None
    status: str
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
