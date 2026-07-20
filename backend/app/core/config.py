from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "OpenSLT"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./backend/data/openslt.sqlite3"
    redis_url: str = "redis://127.0.0.1:6379/0"
    jwt_secret: str = "development-only-secret-change-me-please"
    jwt_algorithm: str = "HS256"
    jwt_access_minutes: int = 30
    jwt_refresh_days: int = 7
    credential_encryption_key: str | None = None
    artifact_root: Path = Path("./backend/data/artifacts")
    log_dir: Path = Path("./backend/logs")
    log_level: str = "INFO"
    app_log_retention_days: int = 90
    audit_log_retention_days: int = 365
    execution_mode: str = Field(default="simulated", pattern="^(simulated|remote)$")
    initial_admin_username: str = "admin"
    initial_admin_password: str = "shengli123"

    @model_validator(mode="after")
    def ensure_directories(self) -> "Settings":
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            Path(self.database_url.removeprefix("sqlite:///" )).parent.mkdir(parents=True, exist_ok=True)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

