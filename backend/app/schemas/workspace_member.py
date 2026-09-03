from datetime import datetime

from pydantic import BaseModel


class WorkspaceMemberOut(BaseModel):
    id: str
    workspace_id: str
    user_id: str
    email: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkspaceMemberCreate(BaseModel):
    user_id: str
    role: str


class WorkspaceMemberUpdate(BaseModel):
    role: str
