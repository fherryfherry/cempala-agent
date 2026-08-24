from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AgentMemoryCreate(BaseModel):
    note: str


class AgentMemoryOut(BaseModel):
    id: str
    agent_id: str
    note: str
    origin: Literal["agent", "owner"]
    source_ticket_key: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
