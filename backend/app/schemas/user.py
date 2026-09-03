from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _validate_email(v: str) -> str:
    # ponytail: no email-validator dependency for one "has an @" check — not a
    # deliverability check, just enough to reject obvious typos.
    v = v.strip()
    if "@" not in v or v.startswith("@") or v.endswith("@"):
        raise ValueError("must be a valid email address")
    return v


class UserOut(BaseModel):
    id: str
    email: str
    is_superadmin: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    is_superadmin: bool = False

    _validate_email = field_validator("email")(_validate_email)


class UserUpdate(BaseModel):
    is_superadmin: bool | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class LoginRequest(BaseModel):
    email: str
    password: str

    _validate_email = field_validator("email")(_validate_email)
