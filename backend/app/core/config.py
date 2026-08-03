from __future__ import annotations

import os
import secrets
import typing
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "OpenSLT"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = ""
    database_host: typing.Union[str, None] = None
    database_port: int = Field(default=3306, ge=1, le=65535)
    database_name: typing.Union[str, None] = None
    database_user: typing.Union[str, None] = None
    database_password: typing.Union[str, None] = None
    auto_create_database: bool = True
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
    observability_body_limit_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    observability_queue_size: int = Field(default=10_000, ge=100, le=100_000)
    observability_index_enabled: bool = True
    observability_hot_retention_days: int = Field(default=30, ge=1, le=365)
    observability_archive_retention_days: int = Field(default=90, ge=1, le=3_650)
    enable_internal_scheduler: bool = True
    task_lease_seconds: int = Field(default=60, ge=10, le=3600)
    task_heartbeat_seconds: int = Field(default=20, ge=3, le=1200)
    frontend_dist: typing.Union[Path, None] = None
    backend_port: int = Field(default=4396, ge=1024, le=65535)
    frontend_port: int = Field(default=7777, ge=1024, le=65535)
    initial_admin_username: str = "admin"
    initial_admin_password: str = "shengli123"

    @model_validator(mode="after")
    def ensure_directories(self) -> "Settings":
        split_database_values = {
            "DATABASE_HOST": self.database_host,
            "DATABASE_NAME": self.database_name,
            "DATABASE_USER": self.database_user,
            "DATABASE_PASSWORD": self.database_password,
        }
        has_split_database = any(value is not None for value in split_database_values.values())
        if self.database_url.strip() and has_split_database:
            raise ValueError(
                "DATABASE_URL cannot be combined with DATABASE_HOST/NAME/USER/PASSWORD"
            )
        if has_split_database:
            missing = [
                key
                for key, value in split_database_values.items()
                if value is None or not str(value).strip()
            ]
            if missing:
                raise ValueError(
                    "Incomplete split database configuration: " + ", ".join(missing)
                )
            self.database_url = URL.create(
                drivername="mysql+pymysql",
                username=self.database_user,
                password=self.database_password,
                host=self.database_host,
                port=self.database_port,
                database=self.database_name,
                query={"charset": "utf8mb4"},
            ).render_as_string(hide_password=False)
        elif not self.database_url.strip():
            self.database_url = "sqlite:///./backend/data/openslt.sqlite3"
        if self.backend_port == self.frontend_port:
            raise ValueError("BACKEND_PORT and FRONTEND_PORT must be different")
        if self.frontend_dist is None:
            candidate = Path(__file__).resolve().parents[3] / "frontend" / "dist"
            self.frontend_dist = candidate if candidate.is_dir() else None
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            database_path = self.database_url[len("sqlite:///") :]
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.jwt_secret = _load_or_create_jwt_secret(self.jwt_secret, self.artifact_root)
        self.credential_encryption_key = _load_or_create_credential_encryption_key(
            self.credential_encryption_key,
            self.artifact_root,
        )
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


def _load_or_create_credential_encryption_key(
    configured: typing.Union[str, None],
    artifact_root: Path,
) -> str:
    if configured and configured.strip():
        value = configured.strip()
        try:
            Fernet(value.encode())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "CREDENTIAL_ENCRYPTION_KEY 必须是 Fernet.generate_key() 生成的合法密钥"
            ) from exc
        return value

    secret_path = artifact_root.expanduser().resolve().parent / "secrets" / "credential_encryption_key"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = secret_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = ""
    if not value:
        value = Fernet.generate_key().decode()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(secret_path, flags, 0o600)
        except FileExistsError:
            value = secret_path.read_text(encoding="utf-8").strip()
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
    try:
        Fernet(value.encode())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"持久化的资源凭据加密密钥格式不合法：{secret_path}"
        ) from exc
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
