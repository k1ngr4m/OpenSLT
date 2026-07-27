from __future__ import annotations

import typing
from functools import lru_cache
from pathlib import Path
import os
import secrets
import sys

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "OpenSLT"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./backend/data/openslt.sqlite3"
    jwt_secret: typing.Union[str, None] = None
    jwt_algorithm: str = "HS256"
    jwt_access_minutes: int = 30
    jwt_refresh_days: int = 7
    credential_encryption_key: typing.Union[str, None] = None
    artifact_root: Path = Path("./backend/data/artifacts")
    log_dir: Path = Path("./backend/logs")
    log_level: str = "INFO"
    app_log_retention_days: int = 90
    audit_log_retention_days: int = 365
    portable_mode: bool = False
    enable_internal_scheduler: bool = True
    frontend_dist: typing.Union[Path, None] = None
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    open_browser: bool = False
    initial_admin_username: str = "admin"
    initial_admin_password: str = "shengli123"

    @model_validator(mode="after")
    def ensure_directories(self) -> "Settings":
        if self.frontend_dist is None:
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
            candidate = bundle_root / "frontend" / "dist"
            self.frontend_dist = candidate if candidate.is_dir() else None
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            database_path = self.database_url[len("sqlite:///") :]
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.jwt_secret = _load_or_create_jwt_secret(self.jwt_secret, self.artifact_root)
        return self


def _load_or_create_jwt_secret(configured: typing.Union[str, None], artifact_root: Path) -> str:
    if configured and configured.strip():
        return configured.strip()

    secret_path = artifact_root.expanduser().resolve().parent / "secrets" / "jwt_secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = secret_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = ""
    if not value:
        value = secrets.token_urlsafe(48)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(secret_path, flags, 0o600)
        except FileExistsError:
            value = secret_path.read_text(encoding="utf-8").strip()
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
