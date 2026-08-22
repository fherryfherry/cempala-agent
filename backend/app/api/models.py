"""GET /api/models — list of `provider/model` strings from `opencode models` (MAP-007).

Never stores LLM credentials: this just shells out to the opencode CLI and parses stdout.
"""

import subprocess
import time

from fastapi import APIRouter

from app.api.errors import AppError
from app.config import settings

router = APIRouter(tags=["models"])

_CACHE_TTL_SECONDS = 5 * 60
# ponytail: module-level (workspace-agnostic) cache tuple, single opencode install assumed.
_cache: tuple[float, list[str]] | None = None

_AUTH_HINT = "run `opencode auth login` to configure a provider"


def _fetch_models() -> list[str]:
    try:
        result = subprocess.run(
            [settings.OPENCODE_BIN, "models"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise AppError(503, "opencode_unavailable", f"opencode binary not found — {_AUTH_HINT}")
    except subprocess.TimeoutExpired:
        raise AppError(503, "opencode_unavailable", f"opencode models timed out — {_AUTH_HINT}")

    if result.returncode != 0:
        raise AppError(503, "opencode_unavailable", f"opencode models failed — {_AUTH_HINT}")

    models = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not models:
        raise AppError(503, "opencode_unavailable", f"opencode returned no models — {_AUTH_HINT}")
    return models


@router.get("/models", response_model=list[str])
async def list_models():
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]

    models = _fetch_models()
    _cache = (now, models)
    return models
