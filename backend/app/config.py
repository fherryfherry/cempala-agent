import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend config. No LLM credentials live here — those belong to `opencode auth`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite+aiosqlite:///./map.db"
    STORAGE_DIR: str = "../storage"
    CORS_ORIGINS: str = "http://localhost:3000"
    OPENCODE_BIN: str = "opencode"
    OPENCODE_STREAM_LIMIT_BYTES: int = 10 * 1024 * 1024
    CLAUDE_BIN: str = "claude"
    CLAUDE_STREAM_LIMIT_BYTES: int = 10 * 1024 * 1024
    CODEX_BIN: str = "codex"
    CODEX_STREAM_LIMIT_BYTES: int = 10 * 1024 * 1024
    AGY_BIN: str = "agy"
    AGY_STREAM_LIMIT_BYTES: int = 10 * 1024 * 1024
    CMD_BIN: str = "cmd"
    CMD_STREAM_LIMIT_BYTES: int = 10 * 1024 * 1024
    # MCP ticket server: each opencode run gets a per-run opencode.json exposing
    # ticket/artifact/memory tools via stdio (ADR-011). Disable to keep runs pure.
    MAP_MCP_ENABLED: bool = True
    MAP_API_BASE: str = "http://127.0.0.1:8000/api"

    # Auth (ADR-016). SECRET_KEY signs session cookies — set a real value via .env
    # once this backend may be reached by more than the owner; the fallback below
    # is dev-only (a startup warning is logged if it's still in use).
    SECRET_KEY: str = "dev-insecure-secret-key-change-me"
    # Bootstrap: if the `user` table is empty at startup and both are set, a first
    # superadmin account is created. Left unset, the app still starts but nobody
    # can log in until an admin is created some other way (direct DB write).
    ADMIN_EMAIL: str | None = None
    ADMIN_PASSWORD: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()

# The MCP server (app/mcp_server.py) is a STDIO subprocess spawned by this very
# process to proxy ticket/artifact/memory tool calls to the backend HTTP API for
# a run this backend already authorized (ADR-011). Its HTTP calls therefore need
# to bypass the ADR-016 login check without a real user session — this secret,
# regenerated every backend startup and passed to each spawned MCP subprocess via
# its env (app/agents/mcp_config.py), is that bypass. Never logged, never
# persisted, never reachable except by a process this backend itself started.
INTERNAL_MCP_SECRET = secrets.token_hex(32)
