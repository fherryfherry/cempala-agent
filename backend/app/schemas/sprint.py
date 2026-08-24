from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, model_validator

SprintStatus = Literal["planned", "active", "completed"]


def _check_date_order(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("end_date must be on or after start_date")


class SprintCreate(BaseModel):
    name: str
    goal: str | None = None
    duration_estimate: float | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _validate_dates(self):
        _check_date_order(self.start_date, self.end_date)
        return self


class SprintUpdate(BaseModel):
    name: str | None = None
    goal: str | None = None
    duration_estimate: float | None = None
    status: SprintStatus | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _validate_dates(self):
        _check_date_order(self.start_date, self.end_date)
        return self


class SprintOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    goal: str | None
    index: int
    status: SprintStatus
    duration_estimate: float | None
    start_date: date | None
    end_date: date | None
    created_at: datetime

    model_config = {"from_attributes": True}
