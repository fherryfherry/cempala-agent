"""GET /api/models — list of `provider/model` strings from `opencode models` (MAP-007).

Never stores LLM credentials: this just shells out to the opencode CLI and parses stdout.
"""

import json
import os
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


def _opencode_config_path() -> str:
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "opencode", "opencode.json")


def _read_default_model() -> str | None:
    """The user's own `opencode` CLI default (the "model" key in their
    opencode.json), if any — read directly off disk since the backend runs as
    the same host user (ADR-010) and this is just a config-file read, no new
    trust boundary. Never raises: missing file / bad JSON / missing key -> None."""
    try:
        with open(_opencode_config_path()) as f:
            config = json.load(f)
    except (OSError, ValueError):
        return None
    model = config.get("model")
    return model if isinstance(model, str) and model.strip() else None


@router.get("/models/default")
async def get_default_model():
    """Suggested default model for new agents — the host's own `opencode` CLI
    default, so the onboarding wizard doesn't have to guess (e.g. picking
    whatever happens to sort first in `opencode models`)."""
    return {"model": _read_default_model()}
