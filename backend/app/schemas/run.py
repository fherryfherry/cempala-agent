from datetime import datetime

from pydantic import BaseModel


class RunCreate(BaseModel):
    agent_id: str | None = None


class RunOut(BaseModel):
    id: str
    ticket_id: str | None
    agent_id: str
    status: str
    trigger: str
    parent_run_id: str | None
    routine_id: str | None = None
    tool_kind: str
    model: str
    session_id: str | None
    tokens_in: int
    tokens_out: int
    cost: float
    report: dict | None
    error: str | None
    started_at: datetime | None
    ended_at: datetime | None

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    id: str
    run_id: str
    seq: int
    type: str
    payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class RunDetail(RunOut):
    events: list[EventOut]
