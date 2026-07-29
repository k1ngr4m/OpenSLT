from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.core.config import Settings


def _settings(tmp_path: Path, **values) -> Settings:
    values.setdefault("database_url", "")
    return Settings(
        _env_file=None,
        artifact_root=tmp_path / "artifacts",
        log_dir=tmp_path / "logs",
        **values,
    )


def test_split_database_fields_build_encoded_mysql_url(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        database_host="10.0.0.8",
        database_port=3307,
        database_name="openslt",
        database_user="app-user",
        database_password="p@ss:#/word",
    )

    url = make_url(settings.database_url)
    assert url.drivername == "mysql+pymysql"
    assert url.host == "10.0.0.8"
    assert url.port == 3307
    assert url.database == "openslt"
    assert url.username == "app-user"
    assert url.password == "p@ss:#/word"
    assert url.query == {"charset": "utf8mb4"}


def test_offline_environment_file_loads_split_database_and_defaults(tmp_path: Path) -> None:
    env_file = tmp_path / "openslt.env"
    env_file.write_text(
        """DATABASE_HOST=127.0.0.1
DATABASE_PORT=3306
DATABASE_NAME=openslt
DATABASE_USER=openslt
DATABASE_PASSWORD="p@ss:#word"
BACKEND_PORT=4400
FRONTEND_PORT=7788
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=shengli123
""",
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=env_file,
        database_url="",
        artifact_root=tmp_path / "artifacts",
        log_dir=tmp_path / "logs",
    )

    url = make_url(settings.database_url)
    assert url.password == "p@ss:#word"
    assert settings.auto_create_database is True
    assert settings.backend_port == 4400
    assert settings.frontend_port == 7788
    assert settings.initial_admin_username == "admin"
    assert settings.initial_admin_password == "shengli123"


def test_legacy_database_url_is_still_supported(tmp_path: Path) -> None:
    database_url = "mysql+pymysql://openslt:secret@127.0.0.1:3306/openslt"

    settings = _settings(tmp_path, database_url=database_url)

    assert settings.database_url == database_url


def test_database_url_cannot_be_mixed_with_split_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        _settings(
            tmp_path,
            database_url="sqlite:///./test.sqlite3",
            database_host="127.0.0.1",
            database_name="openslt",
            database_user="openslt",
            database_password="secret",
        )


def test_incomplete_split_database_configuration_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DATABASE_PASSWORD"):
        _settings(
            tmp_path,
            database_host="127.0.0.1",
            database_name="openslt",
            database_user="openslt",
        )


def test_default_database_and_ports_remain_available(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert settings.database_url == "sqlite:///./backend/data/openslt.sqlite3"
    assert settings.auto_create_database is True
    assert settings.backend_port == 4396
    assert settings.frontend_port == 7777
    assert settings.initial_admin_username == "admin"
    assert settings.initial_admin_password == "shengli123"


@pytest.mark.parametrize(
    "backend_port, frontend_port",
    [(1023, 7777), (4396, 65536), (4396, 4396)],
)
def test_invalid_deployment_ports_are_rejected(
    tmp_path: Path,
    backend_port: int,
    frontend_port: int,
) -> None:
    with pytest.raises(ValueError):
        _settings(
            tmp_path,
            backend_port=backend_port,
            frontend_port=frontend_port,
        )
