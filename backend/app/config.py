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
    # MCP ticket server: each opencode run gets a per-run opencode.json exposing
    # ticket/artifact/memory tools via stdio (ADR-011). Disable to keep runs pure.
    MAP_MCP_ENABLED: bool = True
    MAP_API_BASE: str = "http://127.0.0.1:8000/api"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
