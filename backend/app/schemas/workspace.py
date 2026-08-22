import re
from datetime import datetime

from pydantic import BaseModel, field_validator

KEY_RE = re.compile(r"^[A-Z]{2,5}$")

DEFAULT_GUARDRAILS = {
    "run_timeout_sec": 1800,
    "max_cost_per_run": 2.0,
    "max_cost_per_ticket": 20.0,
    "max_handoff_depth": 12,
    "loop_threshold": 3,
    "max_concurrent_runs": 3,
}


class WorkspaceCreate(BaseModel):
    name: str
    key: str
    repo_path: str

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        if not KEY_RE.match(v):
            raise ValueError("key must be 2-5 uppercase letters (A-Z)")
        return v


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    repo_path: str | None = None
    guardrails: dict | None = None


class WorkspaceOut(BaseModel):
    id: str
    name: str
    key: str
    repo_path: str
    paused: bool
    guardrails: dict
    ticket_counter: int
    created_at: datetime

    model_config = {"from_attributes": True}
