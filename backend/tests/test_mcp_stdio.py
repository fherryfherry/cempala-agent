"""Smoke test: the MCP server actually speaks stdio JSON-RPC when spawned as a
subprocess (the way opencode launches it per run), and lists the expected tools.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from app.agents.base import RunContext
from app.agents.mcp_config import mcp_config_path


def _read_message(proc, timeout=10):
    """Read one JSON-RPC message (newline-delimited) from the process stdout."""
    line = proc.stdout.readline()
    return json.loads(line)


def test_mcp_server_stdio_initialize_and_tools(tmp_path):
    """Spawn `python -m app.mcp_server` exactly like opencode would (via the
    per-run opencode.json env), do the initialize handshake, and confirm the
    ticket tools are advertised."""
    backend_dir = Path(__file__).resolve().parent.parent

    ctx = RunContext(
        run_id="run-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        agent_model="opencode/big-pickle",
        ticket_id="ticket-1",
        repo_path=str(tmp_path),
        prompt="x",
        agent_name="mcp-test-agent",
    )
    config_path = mcp_config_path(ctx.workspace_id, ctx.agent_id)
    assert config_path is not None

    with open(config_path) as f:
        config = json.load(f)
    mcp_cfg = config["mcp"]["map-tickets"]
    env = dict(os.environ)
    env.update(mcp_cfg["env"])

    proc = subprocess.Popen(
        mcp_cfg["command"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    def _send(msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    _send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "map-test", "version": "0"},
            },
        }
    )
    resp = _read_message(proc)
    assert resp["id"] == 1
    assert "serverInfo" in resp["result"]

    _send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    _send(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
    )
    resp = _read_message(proc)
    tool_names = {t["name"] for t in resp["result"]["tools"]}
    expected = {
        "list_tickets",
        "get_ticket",
        "list_comments",
        "post_comment",
        "create_ticket",
        "update_ticket",
        "list_artifacts",
        "read_artifact",
        "get_memory",
        "create_memory",
        "update_memory",
    }
    assert expected <= tool_names, f"missing tools: {expected - tool_names}"

    proc.stdin.close()
    proc.wait(timeout=5)
    assert proc.returncode == 0, proc.stderr.read()
    os.unlink(config_path)


def test_claude_mcp_config_path_shape(tmp_path, monkeypatch):
    from app.agents.mcp_config import claude_mcp_config_path
    from app.config import settings

    monkeypatch.setattr(settings, "MAP_MCP_ENABLED", True)
    path = claude_mcp_config_path("ws-claude", "agent-claude")
    assert path is not None
    try:
        with open(path) as f:
            config = json.load(f)
        assert "mcpServers" in config
        server = config["mcpServers"]["map-tickets"]
        assert isinstance(server["command"], str)
        assert "--workspace-id" in server["args"]
        assert server["env"]["MAP_WORKSPACE_ID"] == "ws-claude"
    finally:
        os.unlink(path)

    monkeypatch.setattr(settings, "MAP_MCP_ENABLED", False)
    assert claude_mcp_config_path("ws", "agent") is None
