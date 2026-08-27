import asyncio
import contextlib
import shutil
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.agent_memory import agent_memory_router, memory_router
from app.api.agents import agents_router, workspace_agents_router
from app.api.artifacts import workspace_artifacts_router
from app.api.git import workspace_git_router
from app.api.attachments import attachments_router, ticket_attachments_router
from app.api.comments import comments_router
from app.api.conversations import conversations_router, workspace_conversations_router
from app.api.errors import AppError, app_error_handler, validation_error_handler
from app.api.events import router as events_router
from app.api.global_settings import router as global_settings_router
from app.api.models import router as models_router
from app.api.routines import routines_router, workspace_routines_router
from app.api.roles import router as roles_router
from app.api.runs import runs_router, ticket_run_router, workspace_runs_router
from app.api.sprints import sprints_router, workspace_sprints_router
from app.api.tickets import tickets_router, workspace_tickets_router
from app.api.workspaces import router as workspaces_router
from app.config import settings
from app.core.auto_check import run_auto_check
from app.core.orchestrator import recover_interrupted_runs
from app.core.routine_scheduler import run_scheduler
from app.db import session as db_session
from app.mcp_server import create_server as create_mcp_server


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic migrations (`make migrate`), not created here.
    await recover_interrupted_runs(db_session.async_session)
    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(run_scheduler(db_session.async_session, stop_event))
    auto_check_task = asyncio.create_task(run_auto_check(db_session.async_session, stop_event))
    yield
    stop_event.set()
    scheduler_task.cancel()
    auto_check_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await scheduler_task
        await auto_check_task


app = FastAPI(title="Multi-Agent Portal", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.include_router(workspaces_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(global_settings_router, prefix="/api")
app.include_router(roles_router, prefix="/api")
app.include_router(workspace_agents_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(agent_memory_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(workspace_tickets_router, prefix="/api")
app.include_router(tickets_router, prefix="/api")
app.include_router(workspace_sprints_router, prefix="/api")
app.include_router(sprints_router, prefix="/api")
app.include_router(workspace_artifacts_router, prefix="/api")
app.include_router(workspace_git_router, prefix="/api")
app.include_router(workspace_routines_router, prefix="/api")
app.include_router(routines_router, prefix="/api")
app.include_router(comments_router, prefix="/api")
app.include_router(workspace_conversations_router, prefix="/api")
app.include_router(conversations_router, prefix="/api")
app.include_router(ticket_attachments_router, prefix="/api")
app.include_router(attachments_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(ticket_run_router, prefix="/api")
app.include_router(runs_router, prefix="/api")
app.include_router(workspace_runs_router, prefix="/api")


def _opencode_version() -> str | None:
    if shutil.which(settings.OPENCODE_BIN) is None:
        return None
    try:
        result = subprocess.run(
            [settings.OPENCODE_BIN, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


async def _mcp_info() -> dict:
    if not settings.MAP_MCP_ENABLED:
        return {"enabled": False, "api_base": settings.MAP_API_BASE, "tools": []}
    tools = await create_mcp_server().list_tools()
    return {
        "enabled": True,
        "api_base": settings.MAP_API_BASE,
        "tools": [{"name": t.name, "description": t.description} for t in tools],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "opencode": _opencode_version(), "mcp": await _mcp_info()}
