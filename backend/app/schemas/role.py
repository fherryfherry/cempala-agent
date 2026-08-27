import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_ROLE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_role_key(v: str) -> str:
    """Role keys are immutable slugs: `[a-z][a-z0-9_]*`, no spaces."""
    v = v.strip()
    if not _ROLE_KEY_RE.match(v):
        raise ValueError(
            "role key must be lowercase letters/digits/underscore, starting with a letter "
            "([a-z][a-z0-9_]*) — no spaces"
        )
    return v


class RoleOut(BaseModel):
    id: str
    key: str
    name: str
    description: str | None
    system_prompt: str | None
    is_builtin: bool
    may_declare_tickets: bool
    may_manage_artifacts: bool
    is_reviewer: bool
    created_at: datetime
    agent_count: int = 0

    model_config = {"from_attributes": True}


class RoleCreate(BaseModel):
    key: str
    name: str = Field(min_length=1)
    description: str | None = None
    system_prompt: str | None = None
    may_declare_tickets: bool = False
    may_manage_artifacts: bool = False
    is_reviewer: bool = False

    _validate_key = field_validator("key")(validate_role_key)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    system_prompt: str | None = None
    may_declare_tickets: bool | None = None
    may_manage_artifacts: bool | None = None
    is_reviewer: bool | None = None
