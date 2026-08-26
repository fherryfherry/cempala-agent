from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

Role = Literal[
    "pm", "lead", "engineer", "designer", "qa", "pentester", "business_analyst", "system_architect"
]
ToolKind = Literal["opencode", "claude", "agy", "codex"]
AvatarTemplate = Literal[
    "person-1", "person-2", "person-3", "person-4", "person-5", "person-6",
]


def _validate_avatar_color(v: str | None) -> str | None:
    if v is None:
        return v
    if len(v) == 7 and v[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in v[1:]):
        return v
    raise ValueError("avatar_color must be a #rrggbb hex color")


class AgentCreate(BaseModel):
    name: str
    role: Role
    model: str | None = None
    tool_kind: ToolKind
    system_prompt: str | None = None
    avatar_template: AvatarTemplate | None = None
    avatar_color: str | None = None

    _validate_avatar_color = field_validator("avatar_color")(_validate_avatar_color)


class AgentUpdate(BaseModel):
    name: str | None = None
    role: Role | None = None
    model: str | None = None
    tool_kind: ToolKind | None = None
    system_prompt: str | None = None
    enabled: bool | None = None
    avatar_template: AvatarTemplate | None = None
    avatar_color: str | None = None

    _validate_avatar_color = field_validator("avatar_color")(_validate_avatar_color)


class AgentOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    role: str
    model: str | None
    tool_kind: str
    system_prompt: str | None
    avatar_template: str | None
    avatar_color: str | None
    enabled: bool
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentListOut(AgentOut):
    memory_count: int
