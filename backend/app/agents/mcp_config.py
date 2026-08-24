"""Per-run MCP config for opencode (ADR-011).

Pure stdlib — no imports from `app.agents.base` (which would create the
`base -> opencode_tool -> base` circular import). `opencode_tool` calls this
with the run's workspace/agent ids.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from app.config import settings


def mcp_config_path(workspace_id: str, agent_id: str) -> str | None:
    """Write a per-run opencode.json wiring the map-tickets MCP server.

    The server is spawned by opencode as a stdio subprocess: `<backend python> -m
    app.mcp_server` with PYTHONPATH pointing at the backend dir (opencode runs with
    cwd=repo_path, so `app` wouldn't resolve otherwise) and MAP_* env vars set to
    this run's workspace/agent. Returns the config file path, or None when MCP is
    disabled.
    """
    if not settings.MAP_MCP_ENABLED:
        return None
    backend_dir = Path(__file__).resolve().parent.parent.parent
    config = {
        "mcp": {
            "map-tickets": {
                "type": "local",
                "command": [sys.executable, "-m", "app.mcp_server"],
                "env": {
                    "PYTHONPATH": str(backend_dir),
                    "MAP_API_BASE": settings.MAP_API_BASE,
                    "MAP_WORKSPACE_ID": workspace_id,
                    "MAP_AGENT_ID": agent_id,
                },
            }
        }
    }
    fd, path = tempfile.mkstemp(suffix=".json", prefix="map-mcp-")
    with os.fdopen(fd, "w") as f:
        json.dump(config, f)
    return path
