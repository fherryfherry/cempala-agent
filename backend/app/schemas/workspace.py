import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

KEY_RE = re.compile(r"^[A-Z]{2,5}$")

DEFAULT_GUARDRAILS = {
    "run_timeout_sec": 1800,
    "max_cost_per_run": 2.0,
    "max_cost_per_ticket": 20.0,
    "max_handoff_depth": 1000,
    "loop_threshold": 3,
    # Caps how many `tickets[]` entries a single report may create (MAP-0xx: agents
    # were over-decomposing simple requests into many sub-tickets). Excess entries
    # are dropped with a named system comment, not silently created.
    "max_tickets_per_report": 5,
    "max_concurrent_runs": 3,
    "max_auto_retries": 3,
    # Auto-check (MAP-050): how often the built-in scheduler scans for stale
    # tickets, and how stale (minutes) a ticket must be before the assigned
    # agent gets nudged to follow up. 0 disables the auto-check entirely.
    "auto_check_interval_minutes": 3,
    "auto_check_stale_minutes": 3,
}

# Default workflow prompt for new workspaces — intentionally empty. Owners write
# their own from Settings; the portal ships no opinionated multi-agent workflow text
# out of the box.
DEFAULT_WORKFLOW_PROMPT = ""


class WorkspaceCreate(BaseModel):
    name: str
    key: str
    repo_path: str
    description: str | None = None
    # When set, repo_path is populated via `git clone <clone_url> <repo_path>`
    # instead of being created as an empty directory.
    clone_url: str | None = None

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        if not KEY_RE.match(v):
            raise ValueError("key must be 2-5 uppercase letters (A-Z)")
        return v


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    repo_path: str | None = None
    description: str | None = None
    guardrails: dict | None = None
    workflow_prompt: str | None = None
    time_unit: Literal["hour", "day"] | None = None
    timezone: str | None = None
    sprint_creator_roles: list[str] | None = None
    main_branch: str | None = None


class WorkspaceOut(BaseModel):
    id: str
    name: str
    key: str
    repo_path: str
    description: str | None = None
    paused: bool
    guardrails: dict
    workflow_prompt: str = ""
    ticket_counter: int
    time_unit: Literal["hour", "day"] = "day"
    timezone: str = "Asia/Jakarta"
    sprint_creator_roles: list[str] = ["pm"]
    main_branch: str = "main"
    created_at: datetime

    model_config = {"from_attributes": True}
