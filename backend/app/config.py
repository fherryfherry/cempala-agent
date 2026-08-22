from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend config. No LLM credentials live here — those belong to `opencode auth`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite+aiosqlite:///./map.db"
    STORAGE_DIR: str = "../storage"
    CORS_ORIGINS: str = "http://localhost:3000"
    OPENCODE_BIN: str = "opencode"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
