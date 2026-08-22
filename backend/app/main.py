import shutil
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.agents import agents_router, workspace_agents_router
from app.api.attachments import attachments_router, ticket_attachments_router
from app.api.comments import comments_router
from app.api.errors import AppError, app_error_handler, validation_error_handler
from app.api.models import router as models_router
from app.api.tickets import tickets_router, workspace_tickets_router
from app.api.workspaces import router as workspaces_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic migrations (`make migrate`), not created here.
    yield


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
app.include_router(workspace_agents_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(workspace_tickets_router, prefix="/api")
app.include_router(tickets_router, prefix="/api")
app.include_router(comments_router, prefix="/api")
app.include_router(ticket_attachments_router, prefix="/api")
app.include_router(attachments_router, prefix="/api")


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


@app.get("/api/health")
async def health():
    return {"status": "ok", "opencode": _opencode_version()}
