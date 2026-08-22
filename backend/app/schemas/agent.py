from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Role = Literal["pm", "lead", "engineer", "designer", "qa", "pentester"]
ToolKind = Literal["opencode", "claude", "agy", "codex"]


class AgentCreate(BaseModel):
    name: str
    role: Role
    model: str
    tool_kind: ToolKind
    system_prompt: str | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    role: Role | None = None
    model: str | None = None
    tool_kind: ToolKind | None = None
    system_prompt: str | None = None
    enabled: bool | None = None


class AgentOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    role: str
    model: str
    tool_kind: str
    system_prompt: str | None
    enabled: bool
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
